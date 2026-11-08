"""Eval runner: `uv run python -m evals.run_evals [flags]`.

Enriches the golden-set companies into a separate eval database, scores the result, optionally
judges the free-text fields, and prints one Markdown table plus a list of misses. Exit codes:
0 all gates pass (or none apply), 1 a gate failed, 2 configuration error or incomplete run.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from evals.golden import EVALS_DIR, GOLDEN_PATH, GoldenEntry, GoldenError, load_golden
from evals.injection import CaseResult, load_cases, run_case
from evals.judge import (
    CALIBRATED_MIN_PERCENT,
    Calibration,
    Field,
    calibrate,
    judge_field,
    tally_verdicts,
)
from evals.metrics import (
    Observed,
    Ratio,
    Scores,
    cited_urls,
    fetched_page_text,
    last_round,
    read_observed,
    rebuild_ledger,
    score_all,
)
from sprout.agents.enrich import (
    DEFAULT_BUDGET,
    DEFAULT_EFFORT,
    SYSTEM_PROMPT,
    CompanyNotFound,
    Deps,
    EnrichmentFailed,
    enrich_with_result,
    resolve_company,
)
from sprout.agents.loop import Budget, MessagesClient, make_client
from sprout.config import DEFAULT_EVAL_DB_PATH, MissingConfigError, Settings, get_settings
from sprout.costs import BatchBudget
from sprout.db import Company, delete_profile, get_engine, init_db
from sprout.sources import companies_house, web
from sprout.sources.companies_house import CompaniesHouseClient
from sprout.sources.http import FetchError
from sprout.sources.web import WebFetcher

DEFAULT_DB = DEFAULT_EVAL_DB_PATH
DEFAULT_MAX_TOTAL_USD = Decimal("1.50")
RESULTS_PATH = EVALS_DIR / "results.md"
NOISE_COMPANIES = 2  # a change smaller than this is within run-to-run noise (one company ~3 points)
JUDGED_FIELDS: tuple[Field, ...] = ("business_model", "target_user")
COUNTS_COMMENT = re.compile(r"<!-- sprout-eval-counts (\{.*?\}) -->")


@dataclass(frozen=True)
class Gate:
    op: str  # ">=" or "<="
    percent: float

    def passes(self, ratio: Ratio) -> bool:
        value = ratio.percent
        if value is None:  # nothing measurable: a rate to keep low holds, a rate to reach does not
            return self.op == "<="
        return value >= self.percent if self.op == ">=" else value <= self.percent

    def __str__(self) -> str:
        return f"{self.op} {self.percent:g}%"


# Set one company below the baseline run (24/29 category, 0/70 fabricated) recorded in
# evals/results.md. Every other metric is report-only until then.
GATES: dict[str, Gate] = {
    "category_accuracy": Gate(">=", 79),
    "fabricated_funding": Gate("<=", 2),
}

LABELS = {
    "profiles_submitted": "Profiles submitted",
    "infrastructure_errors": "Infrastructure errors",
    "repair_rate": "Repair rate",
    "category_accuracy": "Category accuracy",
    "stage_accuracy": "Stage accuracy",
    "funding_exact": "Funding exact",
    "funding_within_10pct": "Funding within 10%",
    "unexpected_disclosure": "Unexpected disclosure",
    "fabricated_funding": "Fabricated funding",
    "unverifiable_funding": "Unverifiable funding",
    "source_ledger": "Source ledger membership",
    "thin_other": "Thin Other profiles",
}


class RunnerError(RuntimeError):
    """A configuration problem; the run stops with exit code 2."""


@dataclass
class Services:
    client: MessagesClient | None
    fetcher: WebFetcher
    ch: CompaniesHouseClient | None


def open_services(settings: Settings, stack: ExitStack) -> Services:
    client = make_client(settings.anthropic_api_key) if settings.anthropic_api_key else None
    fetcher = WebFetcher(stack.enter_context(web.make_http_client(settings.cache_dir)))
    ch = None
    if settings.companies_house_api_key:
        http = companies_house.make_http_client(
            settings.companies_house_api_key, settings.cache_dir
        )
        ch = CompaniesHouseClient(stack.enter_context(http))
    return Services(client, fetcher, ch)


# ---- selection and enrichment ---------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="evals.run_evals", description=__doc__)
    p.add_argument("--db", default=DEFAULT_DB, help="eval database (reset per run)")
    p.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    p.add_argument("--only", nargs="+", metavar="NAME_OR_NUMBER", help="re-run just these")
    p.add_argument("--limit", type=int, help="first N selected entries")
    p.add_argument("--strong", action="store_true", help="generate with the strong model")
    p.add_argument("--effort", choices=["low", "medium", "high"], default=DEFAULT_EFFORT)
    p.add_argument("--max-usd", type=Decimal, default=DEFAULT_BUDGET.max_usd, help="per company")
    p.add_argument("--max-total-usd", type=Decimal, default=DEFAULT_MAX_TOTAL_USD)
    p.add_argument("--score-only", action="store_true", help="rescore the existing eval database")
    p.add_argument("--skip-judge", action="store_true")
    p.add_argument("--calibrate", action="store_true", help="grade the judge on the 12 pairs")
    p.add_argument("--check-urls", action="store_true", help="fetch snippet-only cited URLs")
    p.add_argument("--injection", action="store_true", help="run the injection suite live")
    p.add_argument("--record", action="store_true", help="append the report to evals/results.md")
    return p.parse_args(argv)


def select_entries(entries: list[GoldenEntry], args: argparse.Namespace) -> list[GoldenEntry]:
    if args.split != "all":
        entries = [e for e in entries if e.split == args.split]
    if args.only:
        wanted = {t.strip().lower() for t in args.only}
        entries = [e for e in entries if {e.company_number.lower(), e.name.lower()} & wanted]
        found = {k for e in entries for k in (e.company_number.lower(), e.name.lower())}
        missing = sorted(wanted - found)
        if missing:
            raise RunnerError(f"--only matches no selected golden entry: {', '.join(missing)}")
    if args.limit is not None:
        entries = entries[: max(args.limit, 0)]
    if not entries:
        raise RunnerError("no verified golden entries selected")
    return entries


def _enrich_entry(session: Session, company: Company, deps: Deps, budget: Budget) -> Decimal:
    """Re-enrich one company from a clean slate and return what it cost. The single per-company
    call, so Phase 5 can swap in the orchestrator. A failed run is counted, never retried."""
    delete_profile(session, company)
    session.commit()
    try:
        _, result = enrich_with_result(session, company, deps, budget)
    except EnrichmentFailed as exc:
        return exc.cost_usd
    return result.cost_usd


# ---- judge, liveness ------------------------------------------------------------------------


def judge_entries(
    client: MessagesClient,
    model: str,
    batch: BatchBudget,
    pairs: list[tuple[GoldenEntry, Observed]],
) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Verdict counts per free-text field (plus `missing`) and a note for each non-correct one."""
    counts: dict[str, dict[str, int]] = {}
    notes: list[str] = []
    for field in JUDGED_FIELDS:
        results, missing = [], 0
        for entry, observed in pairs:
            if not getattr(entry, f"{field}_sources"):
                continue  # no sourced reference: nothing to judge against
            candidate = observed.value(field)
            if not isinstance(candidate, str) or not candidate.strip():
                missing += 1
                continue
            judged = judge_field(
                client,
                model=model,
                field=field,
                reference=getattr(entry, field),
                candidate=candidate,
                batch=batch,
            )
            results.append(judged)
            if judged is not None and judged.verdict != "correct":
                notes.append(f"{entry.name} {field}: {judged.verdict} ({judged.reason})")
        counts[field] = {**tally_verdicts(results), "missing": missing}
    return counts, notes


@dataclass(frozen=True)
class Liveness:
    ok: int
    dead: int
    unchecked: int
    dead_urls: tuple[str, ...]

    @property
    def total(self) -> int:
        return self.ok + self.dead + self.unchecked


def check_liveness(fetcher: WebFetcher, pairs: list[tuple[GoldenEntry, Observed]]) -> Liveness:
    """Cited URLs that were fetched during the run are known OK; only the rest cost a request."""
    urls: dict[str, bool] = {}  # url -> already fetched OK by the run
    for _, observed in pairs:
        ledger = None if observed.sources is None else rebuild_ledger(observed.sources)
        for url in cited_urls(observed):
            seen = None if ledger is None else ledger.get(url)
            urls[url] = urls.get(url, False) or bool(seen and seen.fetched)
    ok, dead, unchecked = 0, [], 0
    for url, fetched in urls.items():
        if fetched:
            ok += 1
            continue
        try:
            fetcher.fetch(url)
            ok += 1
        except FetchError as exc:
            if exc.status_code in (404, 410) or "could not resolve" in str(exc):
                dead.append(url)
            else:  # robots, 5xx, timeout: the page may be fine
                unchecked += 1
    return Liveness(ok, len(dead), unchecked, tuple(dead))


# ---- report ---------------------------------------------------------------------------------


@dataclass
class Extras:
    """Measurements beyond `Scores`; None means that part did not run."""

    judge: dict[str, dict[str, int]] | None = None
    judge_notes: tuple[str, ...] = ()
    calibration: Calibration | None = None
    liveness: Liveness | None = None
    injection: list[CaseResult] | None = None
    enrich_usd: Decimal = Decimal(0)
    judge_usd: Decimal = Decimal(0)
    injection_usd: Decimal = Decimal(0)

    @property
    def total_usd(self) -> Decimal:
        return self.enrich_usd + self.judge_usd + self.injection_usd


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    ratio: Ratio
    note: str = ""


def _metrics(scores: Scores, extras: Extras) -> list[Metric]:
    rows = [Metric(k, LABELS[k], r) for k, r in scores.ratios.items()]
    if scores.ledger_unverifiable:  # runs from before stats["sources"] existed
        ledger = next(m for m in rows if m.key == "source_ledger")
        rows[rows.index(ledger)] = replace(
            ledger, note=f"; {scores.ledger_unverifiable} unverifiable"
        )
    if extras.judge is not None:
        calibrated = extras.calibration is not None and extras.calibration.calibrated
        tag = "" if calibrated else " [uncalibrated]"
        for field, counts in extras.judge.items():
            note = (
                f"; partial {counts['partial']}, incorrect {counts['incorrect']}, "
                f"unjudged {counts['unjudged']}, missing {counts['missing']}{tag}"
            )
            ratio = Ratio(counts["correct"], sum(counts.values()))
            rows.append(Metric(f"judge_{field}", f"Judge {field} correct", ratio, note))
    if extras.calibration is not None:
        rows.append(Metric("calibration", "Judge calibration", extras.calibration.agreement))
    if extras.liveness is not None:
        live = extras.liveness
        note = f"; {live.ok} ok, {live.unchecked} unchecked"
        rows.append(Metric("urls_dead", "Dead cited URLs", Ratio(live.dead, live.total), note))
    if extras.injection is not None:
        passed = sum(r.passed for r in extras.injection)
        inconclusive = sum(r.inconclusive for r in extras.injection)
        note = f"; {inconclusive} inconclusive"
        ratio = Ratio(passed, len(extras.injection))
        rows.append(Metric("injection", "Injection cases passed", ratio, note))
    return rows


def _delta(previous: dict[str, Any] | None, metric: Metric) -> str:
    if previous is None or metric.key not in previous:
        return "n/a"
    prev_num, prev_den = previous[metric.key]
    if prev_den != metric.ratio.den:
        return "n/a"
    change = metric.ratio.num - prev_num
    text = f"{change:+d}" if change else "0"
    return f"{text} (noise)" if 0 < abs(change) < NOISE_COMPANIES else text


def _gate_cell(metric: Metric, evaluate: bool) -> str:
    if metric.key == "calibration":
        percent = metric.ratio.percent
        ok = percent is not None and percent >= CALIBRATED_MIN_PERCENT
        return f">= {CALIBRATED_MIN_PERCENT}% {'calibrated' if ok else 'uncalibrated'}"
    gate = GATES.get(metric.key)
    if gate is None:
        return "report"
    if not evaluate:
        return f"{gate} not evaluated"
    return f"{gate} {'pass' if gate.passes(metric.ratio) else 'FAIL'}"


def render_table(
    scores: Scores, extras: Extras, previous: dict[str, Any] | None, evaluate: bool
) -> str:
    lines = ["| Metric | Result | Delta | Gate |", "|---|---|---|---|"]
    for m in _metrics(scores, extras):
        lines.append(
            f"| {m.label} | {m.ratio}{m.note} | {_delta(previous, m)} | {_gate_cell(m, evaluate)} |"
        )
    mean, worst = scores.cost_mean, scores.cost_max
    if mean is not None and worst is not None:
        lines.append(
            f"| Cost per company | mean USD {mean:.4f}, max USD {worst:.4f} | n/a | report |"
        )
    breakdown = f"enrichment {extras.enrich_usd:.4f}, judge {extras.judge_usd:.4f}" + (
        f", injection {extras.injection_usd:.4f}" if extras.injection is not None else ""
    )
    lines.append(f"| Total cost | USD {extras.total_usd:.4f} ({breakdown}) | n/a | report |")
    return "\n".join(lines)


def _amount(value: int | None) -> str:
    return "unknown" if value is None else f"US${value}m"


def render_misses(
    pairs: list[tuple[GoldenEntry, Observed]],
    scores: Scores,
    ch_names: dict[str, str],
    judge_notes: Sequence[str],
) -> str:
    blocks = []
    for (entry, observed), es in zip(pairs, scores.entries, strict=True):
        problems = []
        if not es.submitted:
            problems.append(
                f"not submitted (stop={observed.stop}, failure={observed.failure_category})"
            )
        if es.category_ok is False:
            golden = " or ".join(c.value for c in entry.categories)
            problems.append(f"category: golden {golden}; observed {observed.category}")
        if es.stage_ok is False:
            problems.append(
                f"stage: golden {entry.stage.value}; observed {observed.value('stage')}"
            )
        if es.funding_exact is False and entry.last_round is not None:
            latest = last_round(observed.rounds)
            got = None if latest is None else latest.amount_million_usd
            kind = "within 10%" if es.funding_within else "off"
            problems.append(
                f"funding: golden {_amount(entry.last_round.amount_million_usd)}; "
                f"observed {_amount(got)} ({kind})"
            )
        if es.unexpected_disclosure:
            amounts = [
                _amount(r.amount_million_usd) for r in observed.rounds if r.amount_million_usd
            ]
            problems.append(f"unexpected disclosure: golden has no round; observed {amounts}")
        problems += [
            f"fabricated {c.label} {_amount(c.amount_million_usd)} at {c.source_url}"
            for c, result in es.claims
            if result == "fabricated"
        ]
        if es.ledger_hits < es.ledger_cited:
            problems.append(f"{es.ledger_cited - es.ledger_hits} cited URL(s) not in the ledger")
        if problems:
            ch_name = ch_names.get(entry.company_number, "not in the database")
            head = f"- {entry.name} ({entry.company_number}, Companies House: {ch_name})"
            blocks.append("\n".join([head, *(f"  - {p}" for p in problems)]))
    if judge_notes:
        blocks.append("- Judge:\n" + "\n".join(f"  - {n}" for n in judge_notes))
    return "\n".join(blocks) if blocks else "None."


def _git_short_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def _label_dates(entries: Sequence[GoldenEntry]) -> str:
    dates = sorted({e.label.date.isoformat() for e in entries})
    return dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}"


def previous_counts(path: Path, split: str) -> dict[str, Any] | None:
    """Counts of the latest recorded full run on the same split (the baseline for deltas)."""
    if not path.exists():
        return None
    found = None
    for raw in COUNTS_COMMENT.findall(path.read_text(encoding="utf-8")):
        try:
            counts = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if counts.get("full") and counts.get("split") == split:
            found = counts
    return found


def append_record(path: Path, report: str, counts: dict[str, Any]) -> None:
    header = ""
    if not path.exists():
        header = (
            "# Eval results\n\nAppend-only log written by `uv run python -m evals.run_evals "
            "--record`. Newest last. Each run ends with a machine-readable counts comment that "
            "the next run reads for its Delta column.\n"
        )
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{header}\n{report}\n\n<!-- sprout-eval-counts {json.dumps(counts)} -->\n")


# ---- main -----------------------------------------------------------------------------------


def _resolve_all(
    session: Session, entries: list[GoldenEntry], ch: CompaniesHouseClient | None
) -> dict[str, Company]:
    companies = {}
    for entry in entries:
        try:
            companies[entry.company_number] = resolve_company(session, entry.company_number, ch)
        except (CompanyNotFound, FetchError, ValueError) as exc:
            raise RunnerError(f"{entry.name} ({entry.company_number}): {exc}") from exc
    return companies


def _prepare_db(args: argparse.Namespace, settings: Settings) -> Path:
    db = Path(args.db)
    if args.score_only:
        if not db.exists():
            raise RunnerError(f"--score-only needs an existing eval database, none at {db}")
    elif not args.only:
        if db.resolve() == settings.db_path.resolve():
            raise RunnerError(f"refusing to reset the live database {db}; pass a separate --db")
        for suffix in ("", "-journal"):
            Path(f"{db}{suffix}").unlink(missing_ok=True)
    return db


def run(args: argparse.Namespace, settings: Settings, services: Services) -> int:
    entries = select_entries(load_golden(GOLDEN_PATH), args)
    needs_llm = not args.score_only or not args.skip_judge or args.calibrate or args.injection
    if needs_llm:
        settings.require_anthropic_key()
    db = _prepare_db(args, settings)
    batch = BatchBudget(args.max_total_usd)
    model = settings.strong_model if args.strong else settings.bulk_model
    engine = get_engine(db)
    init_db(engine)
    extras = Extras()
    skipped_numbers: set[str] = set()
    client = services.client

    with Session(engine) as session:
        if args.score_only:
            companies = {
                c.company_number: c for c in session.exec(select(Company)).all() if c.company_number
            }
        else:
            assert client is not None
            companies = _resolve_all(session, entries, services.ch)  # fail before any spend
            deps = Deps(client, services.fetcher, services.ch, settings, model, args.effort)
            for i, entry in enumerate(entries, 1):
                if batch.exhausted:
                    skipped_numbers.add(entry.company_number)
                    continue
                budget = Budget(
                    DEFAULT_BUDGET.max_iterations,
                    batch.clamp(args.max_usd),
                    max_repairs=DEFAULT_BUDGET.max_repairs,
                )
                cost = _enrich_entry(session, companies[entry.company_number], deps, budget)
                batch.charge(cost)
                print(f"[{i}/{len(entries)}] {entry.name}: USD {cost:.4f}", file=sys.stderr)

        pairs = [
            (e, o)
            for e in entries
            if e.company_number not in skipped_numbers and (o := read_observed(session, e)).has_run
        ]
        scored_numbers = {e.company_number for e, _ in pairs}
        not_run = [e for e in entries if e.company_number not in scored_numbers]
        scores = score_all(pairs, fetched_page_text(services.fetcher))
        extras.enrich_usd = sum((e.cost_usd or Decimal(0) for e in scores.entries), Decimal(0))
        ch_names = {n: c.name for n, c in companies.items()}

        spent_before = batch.spent
        if args.calibrate:
            assert client is not None
            extras.calibration = calibrate(client, model=settings.strong_model, batch=batch)
        if not args.skip_judge:
            assert client is not None
            judge, notes = judge_entries(client, settings.strong_model, batch, pairs)
            extras.judge, extras.judge_notes = judge, tuple(notes)
        extras.judge_usd = batch.spent - spent_before

        if args.check_urls:
            extras.liveness = check_liveness(services.fetcher, pairs)

        if args.injection:
            assert client is not None
            extras.injection = []
            for case in load_cases():
                if batch.exhausted:
                    break
                case_budget = Budget(
                    DEFAULT_BUDGET.max_iterations,
                    batch.clamp(args.max_usd),
                    max_repairs=DEFAULT_BUDGET.max_repairs,
                )
                result = run_case(client, case, model=model, effort=args.effort, budget=case_budget)
                batch.charge(result.cost_usd)
                extras.injection.append(result)
                extras.injection_usd += result.cost_usd

    complete = not not_run
    subset = bool(args.only) or args.limit is not None
    full = complete and not subset
    evaluate = full and args.split == "all"
    previous = previous_counts(RESULTS_PATH, args.split)
    table = render_table(scores, extras, previous, evaluate)
    failed = [k for k, g in GATES.items() if evaluate and not g.passes(scores.ratios[k])]

    calibrated = extras.calibration is not None and extras.calibration.calibrated
    judge_note = (
        "" if extras.judge is None else f" (judge {'calibrated' if calibrated else 'uncalibrated'})"
    )
    if not complete:
        reason = "skipped_budget" if not args.score_only else "no run in the eval database"
        gate_line = f"Gates not evaluated: incomplete run, {len(not_run)} entries {reason}."
    elif not evaluate:
        gate_line = "Gates not evaluated: subset run (only a complete --split all run is gated)."
    else:
        gate_line = f"Gates: {'FAIL (' + ', '.join(failed) + ')' if failed else 'pass'}."
    # A calibration-only invocation (--calibrate --skip-judge) scores nothing new: the full
    # table and misses list would just repeat the last real run's numbers verbatim.
    calibration_only = args.calibrate and args.skip_judge
    sections = [
        f"## {datetime.now(UTC):%Y-%m-%d} | {_git_short_sha()} | {model} | effort {args.effort} "
        f"| prompt {hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:8]} | split {args.split} "
        f"| scored {scores.scored} of {len(entries)}",
        f"Labels: verified, dated {_label_dates(entries)}. Judge model {settings.strong_model}"
        f"{judge_note}. Eval database {db}.",
    ]
    if not calibration_only:
        sections += [
            table,
            gate_line,
            "### Misses\n" + render_misses(pairs, scores, ch_names, extras.judge_notes),
        ]
    if extras.calibration is not None:
        cells = sorted(extras.calibration.confusion.items())
        sections.append(
            "Calibration (human grade -> judge verdict): "
            + (", ".join(f"{h}->{j} {n}" for (h, j), n in cells) or "no verdicts")
        )
    if not_run:
        label = "skipped_budget" if not args.score_only else "no run"
        sections.append("### Not run\n" + "\n".join(f"- {e.name}: {label}" for e in not_run))
    if extras.injection:
        bad = [r for r in extras.injection if not r.passed]
        sections.append(
            "### Injection failures\n"
            + ("\n".join(f"- {r.case_id}: {r.leaked + r.forbidden_calls}" for r in bad) or "None.")
        )
    if extras.liveness and extras.liveness.dead_urls:
        sections.append("### Dead URLs\n" + "\n".join(f"- {u}" for u in extras.liveness.dead_urls))
    report = "\n\n".join(sections)
    print(report)

    if args.record:
        counts = {"split": args.split, "full": full, **scores.counts()}
        counts.update({m.key: [m.ratio.num, m.ratio.den] for m in _metrics(scores, extras)})
        append_record(RESULTS_PATH, report, counts)
    if not complete:
        return 2
    return 1 if failed else 0


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    open_services_fn: Callable[[Settings, ExitStack], Services] = open_services,
) -> int:
    args = _parse_args(argv)
    try:
        settings = settings or get_settings()
        with ExitStack() as stack:
            return run(args, settings, open_services_fn(settings, stack))
    except (RunnerError, GoldenError, MissingConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
