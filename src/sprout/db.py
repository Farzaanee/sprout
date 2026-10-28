"""SQLite schema (SQLModel) and the small set of write helpers ingestion needs.

Tables: `company`, `fact` (provenance), `funding_round`, `news_item`, `run_log`, `run_log_step`. All
timestamps are
timezone-aware UTC.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic_core import to_json
from sqlalchemy import JSON, Column, Engine, UniqueConstraint, inspect, text
from sqlmodel import Field, Session, SQLModel, col, create_engine, select

from sprout.candidates import Assessment, assess
from sprout.failures import FailureCategory, failure_category_for
from sprout.models import CompanyProfile, FundingRound, NewsItem, Sourced
from sprout.sources.companies_house import CompanyHit, parse_hit
from sprout.sources.rss import normalise_url


def utcnow() -> datetime:
    return datetime.now(UTC)


class Company(SQLModel, table=True):
    __tablename__ = "company"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    # Companies House number: the identity used to dedupe (names change, numbers do not).
    company_number: str | None = Field(default=None, unique=True, index=True)
    name: str
    status: str | None = None
    incorporated: date | None = None
    sic_codes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    locality: str | None = None
    # A models.Category value; NULL means not yet classified (distinct from "Other").
    category: str | None = None
    candidate_score: int = 0
    matched_keywords: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)


class Fact(SQLModel, table=True):
    """One `Sourced` value for one field of a company; `value_json` is NULL if not disclosed."""

    __tablename__ = "fact"
    __table_args__ = (
        UniqueConstraint("company_id", "field", "source_url"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    field: str = Field(index=True)
    value_json: str | None = None
    source_url: str | None = None
    retrieved_at: datetime
    confidence: str
    note: str | None = None


class FundingRoundRow(SQLModel, table=True):
    """One funding round with its own provenance (a company has many, each cited separately)."""

    __tablename__ = "funding_round"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    stage: str
    amount_million_usd: int | None = None
    announced: date | None = None
    investors: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    source_url: str | None = None
    retrieved_at: datetime
    confidence: str
    note: str | None = None


class NewsItemRow(SQLModel, table=True):
    __tablename__ = "news_item"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    url: str = Field(unique=True, index=True)
    title: str
    published: date | None = None
    summary: str = ""
    source: str | None = None
    first_seen_at: datetime = Field(default_factory=utcnow)


class RunLog(SQLModel, table=True):
    __tablename__ = "run_log"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    kind: str
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str = "running"  # running | ok | error
    stats: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    error: str | None = None
    # A failures.FailureCategory value; NULL means success (or not applicable). No index: create_all
    # would not add it to an existing table.
    failure_category: str | None = None


class RunLogStep(SQLModel, table=True):
    """One model response (loop iteration) of a run: tools called, usage, cost, truncated text."""

    __tablename__ = "run_log_step"
    __table_args__ = (
        UniqueConstraint("run_log_id", "iteration"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    run_log_id: int = Field(foreign_key="run_log.id")
    iteration: int  # 1-based
    created_at: datetime  # stamped by the loop when the response arrived
    stop_reason: str | None = None
    tool_name: str | None = None  # comma-joined in call order, duplicates kept; NULL when none
    tool_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_searches: int = 0
    cost_usd: float = 0.0
    response_text: str | None = None  # untrusted model text, capped by the loop


def get_engine(path: Path | str) -> Engine:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def init_db(engine: Engine) -> None:
    """Create missing tables, add missing nullable columns (no data loss), backfill categories."""
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    with Session(engine) as session:
        _backfill_failure_categories(session)


def _backfill_failure_categories(session: Session) -> None:
    """Classify historical failed `enrich` runs; idempotent (only touches NULL categories)."""
    rows = session.exec(
        select(RunLog).where(RunLog.kind == "enrich", col(RunLog.failure_category).is_(None))
    ).all()
    for row in rows:
        stop = (row.stats or {}).get("stop")
        if stop is None and row.status == "error":
            category = FailureCategory.INTERNAL_ERROR
        elif stop is None:
            continue
        else:
            category = failure_category_for(stop)
        if category is not None:
            row.failure_category = category.value
            session.add(row)
    session.commit()


def _add_missing_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    quote = engine.dialect.identifier_preparer.quote
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable or column.primary_key or column.unique:
                    raise RuntimeError(
                        f"cannot add column {table.name}.{column.name} to an existing database "
                        "(not a plain nullable column); delete the database file and re-ingest"
                    )
                ddl_type = column.type.compile(dialect=engine.dialect)
                conn.execute(
                    text(
                        f"ALTER TABLE {quote(table.name)} ADD COLUMN {quote(column.name)} "
                        f"{ddl_type}"
                    )
                )


def upsert_company(
    session: Session, hit: CompanyHit, assessment: Assessment
) -> tuple[Company, bool]:
    """Insert or refresh a company by Companies House number. Returns (row, created)."""
    company = session.exec(select(Company).where(Company.company_number == hit.number)).first()
    created = company is None
    if company is None:
        company = Company(company_number=hit.number, name=hit.name)
        session.add(company)
    company.name = hit.name
    company.status = hit.status
    company.incorporated = hit.incorporated
    company.sic_codes = list(hit.sic_codes)
    company.locality = hit.locality
    company.candidate_score = assessment.score
    company.matched_keywords = list(assessment.matched_keywords)
    company.last_seen_at = utcnow()
    session.flush()
    return company, created


def save_fact(session: Session, company_id: int, field: str, fact: Sourced[Any]) -> Fact:
    """Store a `Sourced` value, replacing an earlier row for the same (company, field, source)."""
    source_url = str(fact.source_url) if fact.source_url else None
    existing = session.exec(
        select(Fact).where(
            Fact.company_id == company_id,
            Fact.field == field,
            Fact.source_url == source_url,
        )
    ).first()
    row = existing or Fact(
        company_id=company_id, field=field, retrieved_at=utcnow(), confidence=fact.confidence
    )
    row.value_json = None if fact.value is None else to_json(fact.value).decode()
    row.source_url = source_url
    row.retrieved_at = fact.retrieved_at
    row.confidence = fact.confidence.value
    row.note = fact.note
    session.add(row)
    session.flush()
    return row


def add_news_items(session: Session, items: Iterable[NewsItem]) -> int:
    """Insert items whose URL is not stored yet (also within `items`). Returns the new count."""
    seen: set[str] = set()
    new = 0
    for item in items:
        url = normalise_url(str(item.url))
        if url in seen:
            continue
        seen.add(url)
        if session.exec(select(NewsItemRow.id).where(NewsItemRow.url == url)).first() is not None:
            continue
        session.add(
            NewsItemRow(
                url=url,
                title=item.title,
                published=item.published,
                summary=item.summary,
                source=item.source,
            )
        )
        new += 1
    session.flush()
    return new


@contextmanager
def logged_run(session: Session, kind: str) -> Iterator[RunLog]:
    """Record a run in `run_log` (assign `run.stats` in the block). Errors are logged, re-raised."""
    run = RunLog(kind=kind)
    session.add(run)
    session.commit()
    try:
        yield run
        run.status = "ok"
        run.finished_at = utcnow()
        session.add(run)
        session.commit()
    except BaseException as exc:
        session.rollback()
        run.status = "error"
        if run.failure_category is None:  # rollback reloads the committed value, so this is it
            run.failure_category = FailureCategory.INTERNAL_ERROR.value
        run.error = f"{type(exc).__name__}: {exc}"[:1000]
        run.finished_at = utcnow()
        session.add(run)
        session.commit()
        raise


def add_company_by_number(session: Session, number: str, data: dict[str, Any]) -> Company:
    """Create or refresh a company from a Companies House `/company/{number}` response."""
    hit = parse_hit({**data, "company_number": data.get("company_number") or number})
    if hit is None:
        raise ValueError(f"Companies House data for {number!r} has no company number or name")
    assessment = assess(hit.name, hit.sic_codes, status=hit.status, incorporated=hit.incorporated)
    company, _ = upsert_company(session, hit, assessment)
    return company


def _delete_funding_rounds(session: Session, company_id: int) -> int:
    rows = session.exec(select(FundingRoundRow).where(FundingRoundRow.company_id == company_id))
    count = 0
    for row in rows:
        session.delete(row)
        count += 1
    return count


def save_funding_rounds(
    session: Session, company_id: int, rounds: Iterable[Sourced[FundingRound]]
) -> None:
    """Replace a company's funding rounds with `rounds`; value-less entries are skipped.

    Replaced wholesale, not per source: a re-run returns the full list, and keeping rounds from an
    earlier run would duplicate any round now cited from a different page.
    """
    _delete_funding_rounds(session, company_id)
    for item in rounds:
        if item.value is None:
            continue
        session.add(
            FundingRoundRow(
                company_id=company_id,
                stage=item.value.stage.value,
                amount_million_usd=item.value.amount_million_usd,
                announced=item.value.announced,
                investors=list(item.value.investors),
                source_url=str(item.source_url) if item.source_url else None,
                retrieved_at=item.retrieved_at,
                confidence=item.confidence.value,
                note=item.note,
            )
        )
    session.flush()


def delete_profile(session: Session, company: Company) -> int:
    """Delete every stored `fact` and `funding_round` row for a company and clear its `category`.

    Returns the number of `fact` rows deleted.

    For re-enriching a company under a changed schema: old facts persist under their old field
    names (`save_fact` never deletes), so a straight re-run leaves them alongside the new ones.
    """
    if company.id is None:
        raise ValueError("company must be saved before its profile can be deleted")
    rows = session.exec(select(Fact).where(Fact.company_id == company.id)).all()
    for row in rows:
        session.delete(row)
    _delete_funding_rounds(session, company.id)
    company.category = None
    session.add(company)
    session.flush()
    return len(rows)


def save_profile(session: Session, company: Company, profile: CompanyProfile) -> None:
    """Persist a validated profile: one fact per `Sourced` field, its funding rounds, and category.

    Not-disclosed fields are stored as NULL-value facts (researched, nothing found). `news` is not
    stored here (news items have no company link yet) and `name` is ignored (the row is the
    identity).
    """
    if company.id is None:
        raise ValueError("company must be saved before its profile")
    for name in CompanyProfile.model_fields:
        value = getattr(profile, name)
        if isinstance(value, Sourced):
            save_fact(session, company.id, name, value)
    save_funding_rounds(session, company.id, profile.funding_rounds)
    company.category = profile.category.value
    session.add(company)
    session.flush()
