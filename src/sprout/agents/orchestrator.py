"""Pipeline orchestrator: `uv run python -m sprout.orchestrator`.

Deterministic scheduling code (D27), not an LLM supervisor. It picks a working set of companies,
works out which steps each needs from `run_log` (status.py), and runs enrich, classify, news and
verify per company across a pool of workers (D38). `process_company` is the per-company seam: it
never raises and never prints, so the eval runner and a later MCP server can call it.
"""

import argparse
import asyncio
import os
import signal
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlmodel import Session, col, select

from sprout.agents.classify import CLASSIFY_BUDGET, classify_with_result
from sprout.agents.common import AgentFailed, Deps
from sprout.agents.enrich import DEFAULT_BUDGET as ENRICH_BUDGET
from sprout.agents.enrich import CompanyNotFound, enrich_with_result, resolve_company
from sprout.agents.loop import Budget, LoopResult, MessagesClient, make_client
from sprout.agents.news import NEWS_BUDGET, collect_with_result
from sprout.agents.verify import VERIFY_BUDGET, build_claims, verify_with_result
from sprout.candidates import passes_prescreen
from sprout.config import MissingConfigError, Settings, get_settings
from sprout.costs import BatchBudget
from sprout.db import Company, RunKind, RunLog, get_engine, init_db, logged_run
from sprout.failures import FailureCategory, failure_category_for
from sprout.sources import companies_house, web
from sprout.sources.companies_house import CompaniesHouseClient
from sprout.sources.http import FetchError
from sprout.sources.web import WebFetcher
from sprout.status import (
    STEP_KINDS,
    STEPS,
    CompanyStatus,
    StatusPolicy,
    company_statuses,
    profile_gaps,
    reap_abandoned,
)

MAX_CONCURRENCY = 8
RATE_LIMIT_PAUSE_SECONDS = 30.0
MAX_RATE_LIMIT_TRIPS = 5
PASS_RATIO = 0.85  # acceptance: ok companies over the working set
RATE_LIMIT_ERROR = "RateLimitError"
ERROR_TEXT_CHARS = 200

# Expected mean cost per step (cross-cutting 9), used only by --dry-run.
ESTIMATED_STEP_USD = {
    "enrich": Decimal("0.026"),
    "classify": Decimal("0.002"),
    "news": Decimal("0.014"),
    "verify": Decimal("0.05"),
}

StepState = Literal[
    "ok", "failed", "skipped_fresh", "skipped_no_claims", "skipped_budget", "stopped"
]
CompanyResult = Literal["ok", "partial", "failed", "gave_up", "skipped_budget", "stopped"]


@dataclass
class Worker:
    """The per-thread resources of one slot: nothing here is shared between workers."""

    session: Session
    fetcher: WebFetcher
    ch: CompaniesHouseClient | None


def _open_companies_house(settings: Settings, stack: ExitStack) -> CompaniesHouseClient | None:
    if not settings.companies_house_api_key:
        return None
    http = companies_house.make_http_client(settings.companies_house_api_key, settings.cache_dir)
    return CompaniesHouseClient(stack.enter_context(http))


def open_worker(settings: Settings, engine: Any, stack: ExitStack) -> Worker:
    """`HttpClient` is not thread-safe (D3), so every worker owns its clients and session."""
    fetcher = WebFetcher(stack.enter_context(web.make_http_client(settings.cache_dir)))
    ch = _open_companies_house(settings, stack)
    return Worker(stack.enter_context(Session(engine)), fetcher, ch)


class RateLimitGate:
    """After a 429 survives the SDK's retries, every worker waits before starting any step."""

    def __init__(self, pause_seconds: float) -> None:
        self._pause = pause_seconds
        self._until = 0.0
        self._lock = threading.Lock()
        self.trips = 0

    def trip(self) -> None:
        with self._lock:
            self.trips += 1
            self._until = time.monotonic() + self._pause

    def wait(self, stop: threading.Event) -> None:
        while not stop.is_set():
            with self._lock:
                remaining = self._until - time.monotonic()
            if remaining <= 0:
                return
            stop.wait(remaining)


@dataclass
class RunContext:
    client: MessagesClient
    settings: Settings
    batch: BatchBudget
    gate: RateLimitGate
    stop: threading.Event
    policy: StatusPolicy
    now: datetime


@dataclass
class StepOutcome:
    step: str
    state: StepState
    cost_usd: Decimal = Decimal(0)
    failure_category: str | None = None
    error: str | None = None


@dataclass
class CompanyOutcome:
    company_id: int
    result: CompanyResult
    steps: list[StepOutcome] = field(default_factory=list)
    cost_usd: Decimal = Decimal(0)


# ---- one company ----------------------------------------------------------------------------

AgentCall = Callable[[Session, Company, Deps, Budget], LoopResult]


def _verify(session: Session, company: Company, deps: Deps, budget: Budget) -> LoopResult:
    return verify_with_result(session, company, deps, build_claims(session, company), budget)[1]


# Per step: the default budget (each call's cap is clamped to the batch slice) and the call.
AGENTS: dict[str, tuple[Budget, AgentCall]] = {
    "enrich": (ENRICH_BUDGET, lambda s, c, d, b: enrich_with_result(s, c, d, b)[1]),
    "classify": (CLASSIFY_BUDGET, lambda s, c, d, b: classify_with_result(s, c, d, b)[1]),
    "news": (NEWS_BUDGET, lambda s, c, d, b: collect_with_result(s, c, d, b)[1]),
    "verify": (VERIFY_BUDGET, _verify),
}


def _run_step(step: str, worker: Worker, company: Company, ctx: RunContext) -> StepOutcome:
    """One agent call, plus one re-run if it ended on a 429. Never raises."""
    session = worker.session
    cost = Decimal(0)
    try:
        if ctx.stop.is_set():
            return StepOutcome(step, "stopped")
        if step == "verify" and not build_claims(session, company):
            return StepOutcome(step, "skipped_no_claims")
        budget, call = AGENTS[step]
        model = ctx.settings.strong_model if step == "verify" else ctx.settings.bulk_model
        deps = Deps(ctx.client, worker.fetcher, worker.ch, ctx.settings, model)
        for attempt in (1, 2):
            ctx.gate.wait(ctx.stop)
            if ctx.stop.is_set():
                return StepOutcome(step, "stopped", cost)
            cap = ctx.batch.try_slice(budget.max_usd)
            if cap is None:
                return StepOutcome(step, "skipped_budget", cost)
            try:
                result = call(session, company, deps, replace(budget, max_usd=cap))
            except AgentFailed as exc:
                ctx.batch.charge(exc.cost_usd)
                cost += exc.cost_usd
                if exc.error_type == RATE_LIMIT_ERROR:
                    ctx.gate.trip()
                    if attempt == 1:
                        continue
                category = failure_category_for(exc.stop)
                failure = category.value if category else None
                return StepOutcome(step, "failed", cost, failure, str(exc)[:ERROR_TEXT_CHARS])
            ctx.batch.charge(result.cost_usd)
            return StepOutcome(step, "ok", cost + result.cost_usd)
    except Exception as exc:  # isolate: the run_log row (if any) is already committed
        session.rollback()
        text = f"{type(exc).__name__}: {exc}"[:ERROR_TEXT_CHARS]
        return StepOutcome(step, "failed", cost, FailureCategory.INTERNAL_ERROR.value, text)
    raise AssertionError("unreachable: the second attempt always returns")


def _needed(
    session: Session, company_id: int, steps: Sequence[str], ctx: RunContext, force: bool
) -> tuple[CompanyStatus, list[str]]:
    status = company_statuses(session, [company_id], ctx.policy, ctx.now)[company_id]
    return status, [s for s in status.needs(force=force) if s in steps]


def _result(outcomes: list[StepOutcome], *, exhausted: bool, no_profile: bool) -> CompanyResult:
    if exhausted:
        return "gave_up"
    if no_profile:
        return "failed"
    states = {o.step: o.state for o in outcomes}
    blocked = {"failed", "stopped", "skipped_budget"}
    enrich = states.get("enrich")
    if enrich in blocked:
        return enrich  # type: ignore[return-value]  # the same words as the results
    return "partial" if blocked & set(states.values()) else "ok"


def process_company(
    worker: Worker,
    company_id: int,
    steps: Sequence[str],
    ctx: RunContext,
    *,
    force: bool = False,
) -> CompanyOutcome:
    """Run the steps of `steps` that the company's status says it needs, in pipeline order.

    Sync, never raises, never prints. A failed or budget-skipped enrich stops the company (there
    is no profile to work from); any other step failing leaves the rest to run."""
    session = worker.session
    wanted = [s for s in STEPS if s in steps]
    outcomes: list[StepOutcome] = []
    try:
        company = session.get(Company, company_id)
        if company is None:
            raise LookupError(f"no company with id {company_id}")
        status, needed = _needed(session, company_id, wanted, ctx, force)
        exhausted = not force and any(status.steps[s].state == "exhausted" for s in wanted)
        no_profile = False
        if "enrich" in needed:
            outcomes.append(_run_step("enrich", worker, company, ctx))
            if outcomes[-1].state != "ok":
                return _outcome(company_id, outcomes, exhausted=exhausted, no_profile=False)
            # classify and verify describe an enrichment, so a new one makes them due again.
            status, needed = _needed(session, company_id, wanted, ctx, force)
            needed = [s for s in needed if s != "enrich"]
        elif status.steps["enrich"].state not in ("fresh", "stale"):
            needed, no_profile = [], True
        for step in needed:
            outcomes.append(_run_step(step, worker, company, ctx))
        ran = {o.step for o in outcomes}
        outcomes += [
            StepOutcome(s, "skipped_fresh")
            for s in wanted
            if s not in ran and status.steps[s].state == "fresh"
        ]
        outcomes.sort(key=lambda o: STEPS.index(o.step))
        return _outcome(company_id, outcomes, exhausted=exhausted, no_profile=no_profile)
    except Exception as exc:
        session.rollback()
        text = f"{type(exc).__name__}: {exc}"[:ERROR_TEXT_CHARS]
        failure = StepOutcome(
            wanted[0] if wanted else "enrich",
            "failed",
            failure_category=FailureCategory.INTERNAL_ERROR.value,
            error=text,
        )
        return _outcome(company_id, [*outcomes, failure], exhausted=False, no_profile=False)


def _outcome(
    company_id: int, outcomes: list[StepOutcome], *, exhausted: bool, no_profile: bool
) -> CompanyOutcome:
    return CompanyOutcome(
        company_id,
        _result(outcomes, exhausted=exhausted, no_profile=no_profile),
        outcomes,
        sum((o.cost_usd for o in outcomes), Decimal(0)),
    )


# ---- selecting and ordering the working set -------------------------------------------------


def select_working_set(
    session: Session,
    *,
    limit: int,
    only: Sequence[str] = (),
    prescreen: bool = True,
    ch: CompaniesHouseClient | None = None,
) -> list[int]:
    """Pre-screen survivors by `candidate_score` then id, first `limit` (D39), so the same limit
    always names the same set and a small pilot is a prefix of a bigger run. `only` bypasses the
    pre-screen and the limit; targets not in the database are added through Companies House."""
    if only:
        ids = [resolve_company(session, target, ch).id for target in only]
        return [i for i in dict.fromkeys(ids) if i is not None]
    rows = session.exec(select(Company.id, Company.candidate_score, Company.active_officers)).all()
    survivors = [r for r in rows if not prescreen or passes_prescreen(r.active_officers)]
    survivors.sort(key=lambda r: (-r.candidate_score, r.id))
    return [r.id for r in survivors[:limit]]


def order_for_work(
    ids: Sequence[int],
    statuses: dict[int, CompanyStatus],
    steps: Sequence[str],
    *,
    force: bool = False,
) -> list[int]:
    """Failed retries, then partial companies, never-enriched, stale; ties keep the rank order.
    Cheap tails finish before new enrichment spend, so a budget stop leaves the fewest half-done."""

    def priority(company_id: int) -> int:
        status = statuses[company_id]
        if any(status.steps[s].state == "failed" for s in steps):
            return 0
        if not [s for s in status.needs(force=force) if s in steps]:
            return 4
        return {"fresh": 1, "never": 2}.get(status.steps["enrich"].state, 3)

    return sorted(ids, key=priority)


# ---- the run --------------------------------------------------------------------------------


@dataclass
class RunSummary:
    exit_code: int
    text: str = ""
    error: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)


def _company_line(name: str, outcome: CompanyOutcome) -> str:
    parts = []
    for o in outcome.steps:
        detail = f"{o.state}({o.cost_usd:.4f})" if o.cost_usd else o.state
        if o.state == "failed":
            detail += f"[{o.failure_category}: {o.error}]"
        parts.append(f"{o.step}={detail}")
    return f"{outcome.result:<14} {name}  cost=USD {outcome.cost_usd:.4f}  {' '.join(parts)}"


def _running_pipeline(session: Session, now: datetime, policy: StatusPolicy) -> int | None:
    cutoff = now - policy.running_timeout
    return session.exec(
        select(RunLog.id).where(
            RunLog.kind == RunKind.PIPELINE_RUN.value,
            RunLog.status == "running",
            col(RunLog.started_at) >= cutoff,
        )
    ).first()


def _dry_run_text(
    session: Session,
    ids: Sequence[int],
    statuses: dict[int, CompanyStatus],
    steps: Sequence[str],
    args: argparse.Namespace,
) -> str:
    rows = {c.id: c for c in session.exec(select(Company).where(col(Company.id).in_(ids)))}
    lines, total, working = [], Decimal(0), 0
    for company_id in ids:
        status, company = statuses[company_id], rows[company_id]
        needed = [s for s in status.needs(force=args.force) if s in steps]
        estimate = sum((ESTIMATED_STEP_USD[s] for s in needed), Decimal(0))
        total += estimate
        working += bool(needed)
        states = " ".join(f"{s}={status.steps[s].state}" for s in steps)
        lines.append(
            f"{company.name} ({company.company_number})  {states}  "
            f"needs: {','.join(needed) or '-'}  est USD {estimate:.3f}"
        )
    lines.append(
        f"{len(ids)} companies, {working} need work, estimated USD {total:.2f} "
        f"(cap USD {args.max_total_usd}). Dry run: nothing was spent."
    )
    return "\n".join(lines)


def _install_interrupt(stop: threading.Event) -> Callable[[], None]:
    """First Ctrl-C finishes in-flight work and exits 1; the second exits at once (abandoned
    `running` rows are reaped on the next start). Returns the function that removes the handler."""
    loop = asyncio.get_running_loop()

    def handler() -> None:
        if stop.is_set():
            os._exit(130)
        stop.set()
        print("interrupt: finishing in-flight work (Ctrl-C again to quit now)", file=sys.stderr)

    try:
        loop.add_signal_handler(signal.SIGINT, handler)
    except (NotImplementedError, RuntimeError, ValueError):  # not the main thread, or Windows
        return lambda: None
    return lambda: loop.remove_signal_handler(signal.SIGINT)


async def _run_pool(
    settings: Settings,
    engine: Any,
    ids: Sequence[int],
    names: dict[int, str],
    steps: Sequence[str],
    ctx: RunContext,
    args: argparse.Namespace,
) -> list[CompanyOutcome]:
    concurrency = max(1, min(MAX_CONCURRENCY, args.concurrency, len(ids)))
    with ExitStack() as stack:
        pool: asyncio.Queue[Worker] = asyncio.Queue()
        for _ in range(concurrency):
            pool.put_nowait(open_worker(settings, engine, stack))

        async def one(company_id: int) -> CompanyOutcome:
            worker = await pool.get()
            try:
                if ctx.stop.is_set() or ctx.gate.trips >= MAX_RATE_LIMIT_TRIPS:
                    outcome = CompanyOutcome(company_id, "stopped")
                else:
                    outcome = await asyncio.to_thread(
                        process_company, worker, company_id, steps, ctx, force=args.force
                    )
            finally:
                pool.put_nowait(worker)
            print(_company_line(names[company_id], outcome), file=sys.stderr)
            return outcome

        return list(await asyncio.gather(*(one(i) for i in ids)))


def _summary_text(stats: dict[str, Any]) -> str:
    lines = [
        f"Selected {stats['selected']} companies "
        f"(limit {stats['limit']}, concurrency {stats['concurrency']})",
        "Results: " + (", ".join(f"{k}={v}" for k, v in sorted(stats["results"].items())) or "-"),
        f"Spent USD {stats['spent_usd']:.4f} of {stats['max_total_usd']:.2f}: "
        + (", ".join(f"{k} {v:.4f}" for k, v in sorted(stats["by_kind"].items())) or "-"),
        f"Rate-limit trips: {stats['rate_limit_trips']}",
        f"Incomplete profiles among ok companies: {len(stats['incomplete_profiles'])}",
    ]
    if stats["stopped_early"]:
        lines.append(f"Stopped early: {stats['stopped_early']}")
    return "\n".join(lines)


async def run(
    settings: Settings,
    args: argparse.Namespace,
    *,
    client: MessagesClient | None = None,
    stop: threading.Event | None = None,
) -> RunSummary:
    """Select, order and process the working set. `client` and `stop` are injectable for tests."""
    policy = StatusPolicy(enrich_stale_days=args.stale_days, news_stale_days=args.news_stale_days)
    now = datetime.now(UTC)
    steps: tuple[str, ...] = tuple(args.steps)
    if client is None and not args.dry_run:
        try:
            client = make_client(settings.require_anthropic_key(), max_retries=5)
        except MissingConfigError as exc:
            return RunSummary(2, error=str(exc))
    engine = get_engine(settings.db_path)
    init_db(engine)
    with ExitStack() as stack:
        session = stack.enter_context(Session(engine))
        if not args.dry_run:
            running = _running_pipeline(session, now, policy)
            if running is not None and not args.force:
                return RunSummary(
                    2,
                    error=f"another orchestrator run is in progress (pipeline_run {running}); "
                    "wait for it, or pass --force",
                )
            reaped = reap_abandoned(session, now, policy)
            if reaped:
                print(f"reaped {reaped} abandoned run(s)", file=sys.stderr)
        try:
            ids = select_working_set(
                session,
                limit=args.limit,
                only=args.only,
                prescreen=not args.no_prescreen,
                ch=_open_companies_house(settings, stack),
            )
        except (CompanyNotFound, FetchError, ValueError) as exc:
            return RunSummary(2, error=str(exc))
        statuses = company_statuses(session, ids, policy, now)
        ids = order_for_work(ids, statuses, steps, force=args.force)
        if args.dry_run:
            return RunSummary(0, _dry_run_text(session, ids, statuses, steps, args))
        assert client is not None

        stop = stop or threading.Event()
        batch = BatchBudget(args.max_total_usd)
        ctx = RunContext(
            client, settings, batch, RateLimitGate(RATE_LIMIT_PAUSE_SECONDS), stop, policy, now
        )
        names = dict(session.exec(select(Company.id, Company.name)).all())
        with logged_run(session, RunKind.PIPELINE_RUN.value) as pipeline_run:
            remove_handler = _install_interrupt(stop)
            try:
                outcomes = await _run_pool(settings, engine, ids, names, steps, ctx, args)
            finally:
                remove_handler()
            session.commit()  # start a new read snapshot: the workers wrote while we waited
            incomplete = {
                str(o.company_id): gaps
                for o in outcomes
                if o.result == "ok" and (gaps := profile_gaps(session, o.company_id))
            }
            results = dict(Counter(o.result for o in outcomes))
            by_kind: Counter[str] = Counter()
            for outcome in outcomes:
                for o in outcome.steps:
                    by_kind[STEP_KINDS[o.step].value] += float(o.cost_usd)
            rate_limited = ctx.gate.trips >= MAX_RATE_LIMIT_TRIPS
            stopped_early = (
                "interrupted"
                if stop.is_set()
                else "rate_limited"
                if rate_limited
                else "budget"
                if results.keys() & {"skipped_budget", "stopped"}
                else None
            )
            pipeline_run.stats = {
                "limit": args.limit,
                "concurrency": max(1, min(MAX_CONCURRENCY, args.concurrency)),
                "max_total_usd": float(args.max_total_usd),
                "selected": len(ids),
                "results": results,
                "spent_usd": round(float(batch.spent), 6),
                "by_kind": {k: round(v, 6) for k, v in by_kind.items()},
                "rate_limit_trips": ctx.gate.trips,
                "incomplete_profiles": incomplete,
                "stopped_early": stopped_early,
            }
            stats = pipeline_run.stats
    ok_ratio = results.get("ok", 0) / len(ids) if ids else 1.0
    exit_code = 0 if stopped_early is None and ok_ratio >= PASS_RATIO else 1
    return RunSummary(exit_code, _summary_text(stats), stats=stats)


# ---- CLI ------------------------------------------------------------------------------------


def _steps_arg(text: str) -> tuple[str, ...]:
    chosen = [s.strip() for s in text.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in STEPS]
    if unknown or not chosen:
        raise argparse.ArgumentTypeError(f"steps must be a comma list of {', '.join(STEPS)}")
    return tuple(chosen)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sprout.orchestrator", description=__doc__)
    parser.add_argument("--limit", type=int, default=50, help="working-set size (default 50)")
    parser.add_argument("--concurrency", type=int, default=4, help=f"1 to {MAX_CONCURRENCY}")
    parser.add_argument(
        "--max-total-usd",
        type=Decimal,
        default=Decimal("5.00"),
        help="one cap over all enrich, classify, news and verify spend (default 5.00)",
    )
    parser.add_argument("--stale-days", type=int, default=30, help="re-enrich after this many days")
    parser.add_argument("--news-stale-days", type=int, default=7)
    parser.add_argument("--steps", type=_steps_arg, default=STEPS, help="comma list, default all")
    parser.add_argument(
        "--only",
        nargs="+",
        default=(),
        metavar="NAME_OR_NUMBER",
        help="work on these companies only (skips the pre-screen and --limit)",
    )
    parser.add_argument("--no-prescreen", action="store_true", help="skip the D34 officer filter")
    parser.add_argument(
        "--force",
        action="store_true",
        help="ignore freshness, the 3-attempt cap and the single-run lock",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the plan, spend nothing")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.max_total_usd <= 0:
        parser.error("--limit and --max-total-usd must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = asyncio.run(run(get_settings(), args))
    if summary.error:
        print(summary.error, file=sys.stderr)
    else:
        print(summary.text)
    return summary.exit_code


if __name__ == "__main__":
    sys.exit(main())
