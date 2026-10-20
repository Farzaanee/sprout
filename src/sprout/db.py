"""SQLite schema (SQLModel) and the small set of write helpers ingestion needs.

Tables: `company`, `fact` (provenance), `news_item`, `run_log`. All timestamps are
timezone-aware UTC.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic_core import to_json
from sqlalchemy import JSON, Column, Engine, UniqueConstraint, inspect, text
from sqlmodel import Field, Session, SQLModel, create_engine, select

from sprout.candidates import Assessment, assess
from sprout.models import CompanyProfile, NewsItem, Sourced
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


def get_engine(path: Path | str) -> Engine:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def init_db(engine: Engine) -> None:
    """Create missing tables, then add missing nullable columns to existing ones (no data loss)."""
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)


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


def save_profile(session: Session, company: Company, profile: CompanyProfile) -> None:
    """Persist a validated profile: one fact per `Sourced` field plus `company.category`.

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
    company.category = profile.category.value
    session.add(company)
    session.flush()
