"""Per-company pipeline status, derived from `run_log` (D32). Reads `db` only, no agent imports.

A step's state comes from the company's rows of one `RunKind`; nothing is stored. The orchestrator
selects work from it, and later phases (Phase 7 reads, Phase 8 cooldown) reuse it.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import func
from sqlmodel import Session, col, select

from sprout.db import Company, CompanyNewsRow, Fact, FundingRoundRow, RunKind, RunLog, winning_facts
from sprout.failures import FailureCategory

STEPS = ("enrich", "classify", "news", "verify")

STEP_KINDS: dict[str, RunKind] = {
    "enrich": RunKind.COMPANY_ENRICH,
    "classify": RunKind.COMPANY_CLASSIFICATION,
    "news": RunKind.COMPANY_NEWS_MATCH,
    "verify": RunKind.COMPANY_VERIFICATION,
}

StepState = Literal["fresh", "stale", "never", "failed", "exhausted", "running"]

ABANDONED_MESSAGE = "abandoned: process ended before the run finished"
_NEEDED_STATES: frozenset[str] = frozenset({"never", "stale", "failed"})


@dataclass(frozen=True)
class StatusPolicy:
    enrich_stale_days: int = 30
    news_stale_days: int = 7
    max_attempts: int = 3
    running_timeout: timedelta = timedelta(minutes=30)


@dataclass(frozen=True)
class StepStatus:
    step: str
    state: StepState
    last_ok_at: datetime | None
    failed_attempts: int
    last_failure_category: str | None


@dataclass(frozen=True)
class CompanyStatus:
    company_id: int
    steps: dict[str, StepStatus]
    cost_usd: Decimal

    def needs(self, *, force: bool = False) -> list[str]:
        """Steps to run, in `STEPS` order. `force` ignores freshness and the attempt cap but never
        takes over a step another process is running."""
        wanted = {"fresh", "exhausted", *_NEEDED_STATES} if force else _NEEDED_STATES
        return [s for s in STEPS if self.steps[s].state in wanted]


def _utc(value: datetime) -> datetime:
    """SQLite returns naive UTC datetimes; make every comparison aware."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class _Row:
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    failure_category: str | None

    @property
    def order(self) -> tuple[datetime, int]:
        return (self.started_at, self.id)

    @property
    def is_ok(self) -> bool:
        return self.status == "ok" and self.failure_category is None

    def is_abandoned(self, now: datetime, policy: StatusPolicy) -> bool:
        return self.status == "running" and now - self.started_at >= policy.running_timeout

    def is_failed(self, now: datetime, policy: StatusPolicy) -> bool:
        return not self.is_ok and (self.status != "running" or self.is_abandoned(now, policy))

    @property
    def counted_failure_category(self) -> str:
        # An abandoned row is what `reap_abandoned` will turn into internal_error.
        return self.failure_category or FailureCategory.INTERNAL_ERROR.value


def _step_status(
    step: str,
    rows: Sequence[_Row],
    *,
    enrich_ok_at: datetime | None,
    policy: StatusPolicy,
    now: datetime,
) -> StepStatus:
    rows = sorted(rows, key=lambda r: r.order)
    ok_rows = [r for r in rows if r.is_ok]
    newest_ok = ok_rows[-1] if ok_rows else None
    last_ok_at = (newest_ok.finished_at or newest_ok.started_at) if newest_ok else None

    failed = [
        r
        for r in rows
        if r.is_failed(now, policy)
        and (newest_ok is None or r.order > newest_ok.order)
        and r.counted_failure_category != FailureCategory.API_ERROR.value
    ]
    newest = rows[-1] if rows else None
    last_failure = (
        newest.counted_failure_category if newest and newest.is_failed(now, policy) else None
    )

    def status(state: StepState) -> StepStatus:
        return StepStatus(step, state, last_ok_at, len(failed), last_failure)

    if newest is not None and newest.status == "running" and not newest.is_abandoned(now, policy):
        return status("running")
    if newest is not None and newest.is_failed(now, policy):
        return status("exhausted" if len(failed) >= policy.max_attempts else "failed")
    if last_ok_at is None:
        return status("never")
    if step == "enrich":
        stale = now - last_ok_at > timedelta(days=policy.enrich_stale_days)
    elif step == "news":
        stale = now - last_ok_at > timedelta(days=policy.news_stale_days)
    else:  # classify and verify describe the enrichment they ran on
        stale = enrich_ok_at is not None and last_ok_at < enrich_ok_at
    return status("stale" if stale else "fresh")


def company_statuses(
    session: Session, company_ids: Iterable[int], policy: StatusPolicy, now: datetime
) -> dict[int, CompanyStatus]:
    """Status of every id in `company_ids` (companies with no runs are `never` throughout), from
    one query over the pipeline kinds of `run_log`."""
    ids = list(dict.fromkeys(company_ids))
    if not ids:
        return {}
    now = _utc(now)
    kinds = {kind.value: step for step, kind in STEP_KINDS.items()}
    cost = func.json_extract(col(RunLog.stats), "$.cost_usd")
    query = select(
        RunLog.id,
        RunLog.company_id,
        RunLog.kind,
        RunLog.status,
        RunLog.started_at,
        RunLog.finished_at,
        RunLog.failure_category,
        cost,
    ).where(col(RunLog.company_id).in_(ids), col(RunLog.kind).in_(kinds))

    rows: dict[int, dict[str, list[_Row]]] = {i: {s: [] for s in STEPS} for i in ids}
    costs: dict[int, Decimal] = {i: Decimal(0) for i in ids}
    for run_id, company_id, kind, status, started, finished, category, run_cost in session.exec(
        query
    ):
        rows[company_id][kinds[kind]].append(
            _Row(run_id, status, _utc(started), _utc(finished) if finished else None, category)
        )
        if run_cost is not None:
            costs[company_id] += Decimal(str(run_cost))

    result: dict[int, CompanyStatus] = {}
    for company_id in ids:
        steps: dict[str, StepStatus] = {}
        for step in STEPS:  # enrich first: classify and verify are judged against it
            enrich = steps.get("enrich")
            steps[step] = _step_status(
                step,
                rows[company_id][step],
                enrich_ok_at=enrich.last_ok_at if enrich else None,
                policy=policy,
                now=now,
            )
        result[company_id] = CompanyStatus(company_id, steps, costs[company_id])
    return result


def reap_abandoned(session: Session, now: datetime, policy: StatusPolicy) -> int:
    """Turn per-company `running` rows older than the timeout into failed attempts. Batch rows
    (no `company_id`) are left alone. Returns the number reaped."""
    now = _utc(now)
    running = session.exec(
        select(RunLog).where(RunLog.status == "running", col(RunLog.company_id).is_not(None))
    ).all()
    reaped = 0
    for row in running:
        if now - _utc(row.started_at) < policy.running_timeout:
            continue
        row.status = "error"
        row.failure_category = FailureCategory.INTERNAL_ERROR.value
        row.error_message = ABANDONED_MESSAGE
        row.finished_at = now
        session.add(row)
        reaped += 1
    session.commit()
    return reaped


def profile_gaps(session: Session, company_id: int) -> list[str]:
    """Acceptance check: which expected parts of a company's profile are missing."""
    company = session.get(Company, company_id)
    if company is None:
        raise ValueError(f"no company with id {company_id}")
    facts = winning_facts(session.exec(select(Fact).where(Fact.company_id == company_id)).all())

    def has_value(field: str) -> bool:
        return field in facts and facts[field].value_json is not None

    has_funding = session.exec(
        select(FundingRoundRow.id).where(FundingRoundRow.company_id == company_id).limit(1)
    ).first()
    has_news = session.exec(
        select(CompanyNewsRow.news_item_id).where(CompanyNewsRow.company_id == company_id).limit(1)
    ).first()

    checks = {
        "category": company.category is not None,
        "stage": has_value("stage"),
        "funding": has_funding is not None,
        "business_model": has_value("business_model"),
        "target_user": has_value("target_user"),
        "news": has_news is not None,
    }
    return [name for name, present in checks.items() if not present]
