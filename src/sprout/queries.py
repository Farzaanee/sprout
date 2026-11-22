"""Read-only company data access shared by the dashboard and the MCP server.

No UI imports: this module sees only `db`, `models` and `status`. The winning-value rule (A6) and
the "not disclosed" rule live here so every reader applies them the same way. A NULL winning fact
means not disclosed (researched, nothing found); no fact at all means not researched. Callers that
want caching (the dashboard) wrap these functions themselves.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlmodel import Session, col, func, select

from sprout.db import (
    Company,
    CompanyNewsRow,
    Fact,
    FundingRoundRow,
    OfficerRow,
    RunKind,
    RunLog,
    latest_round,
    winning_facts,
)
from sprout.models import PUBLIC_COMPANY_URL, BusinessStatus

NOT_DISCLOSED = "Not disclosed"


@dataclass(frozen=True)
class ProfileFact:
    """The winning fact for one field. `disclosed` is False when the winning value is NULL."""

    field: str
    value: Any
    disclosed: bool
    source_url: str | None
    confidence: str
    retrieved_at: datetime
    note: str | None

    @property
    def display_value(self) -> Any:
        return self.value if self.disclosed else NOT_DISCLOSED


@dataclass(frozen=True)
class Profile:
    """One company's profile as a reader should see it. A field absent from `facts` was never
    researched; one present with `disclosed=False` was researched and nothing was found."""

    company: Company
    facts: dict[str, ProfileFact]
    funding_rounds: list[FundingRoundRow]  # newest announced first
    latest_round: FundingRoundRow | None  # D22
    news: list[CompanyNewsRow]  # newest first; `news_kind` set means big news


@dataclass(frozen=True)
class Coverage:
    """How much of the matched companies have the data a filter or chart relies on, so a missing
    value is reported as unknown rather than plotted as zero."""

    matched: int
    stage_known: int
    funding_known: int


@dataclass(frozen=True)
class CompanySearch:
    companies: list[Company]  # at most `limit`
    coverage: Coverage  # over every match, not just the companies returned


def _parse(fact: Fact | None) -> Any:
    return None if fact is None or fact.value_json is None else json.loads(fact.value_json)


def _winning_by_company(session: Session, fields: Iterable[str]) -> dict[int, dict[str, Fact]]:
    """Winning fact per field per company (A6, see `winning_facts`), from one query."""
    by_company: dict[int, list[Fact]] = {}
    for fact in session.exec(select(Fact).where(col(Fact.field).in_(list(fields)))).all():
        by_company.setdefault(fact.company_id, []).append(fact)
    return {company_id: winning_facts(facts) for company_id, facts in by_company.items()}


def _winning_values(session: Session, field: str) -> dict[int, Any]:
    """Parsed winning `value_json` per company that has a row for this field (None where the
    agent researched the field and found nothing)."""
    return {
        company_id: _parse(winners[field])
        for company_id, winners in _winning_by_company(session, [field]).items()
    }


def _latest_funding_amounts(session: Session) -> dict[int, int | None]:
    """Amount of each company's latest round (D22); None when that round has no amount. Companies
    with no rounds are absent."""
    by_company: dict[int, list[FundingRoundRow]] = {}
    for funding_round in session.exec(select(FundingRoundRow)).all():
        by_company.setdefault(funding_round.company_id, []).append(funding_round)
    amounts: dict[int, int | None] = {}
    for company_id, rounds in by_company.items():
        latest = latest_round(rounds)
        if latest is not None:
            amounts[company_id] = latest.amount_million_usd
    return amounts


def get_profile(session: Session, company_id: int) -> Profile | None:
    """The winning facts, funding rounds and news for one company; None for an unknown id."""
    company = session.get(Company, company_id)
    if company is None:
        return None
    winners = winning_facts(session.exec(select(Fact).where(Fact.company_id == company_id)).all())
    facts = {
        field: ProfileFact(
            field=field,
            value=_parse(fact),
            disclosed=fact.value_json is not None,
            source_url=fact.source_url,
            confidence=fact.confidence,
            retrieved_at=fact.retrieved_at,
            note=fact.note,
        )
        for field, fact in sorted(winners.items())
    }
    rounds = company_funding_rounds(session, company_id)
    news = list(
        session.exec(
            select(CompanyNewsRow)
            .where(CompanyNewsRow.company_id == company_id)
            .order_by(col(CompanyNewsRow.published).desc(), col(CompanyNewsRow.url))
        ).all()
    )
    return Profile(
        company=company,
        facts=facts,
        funding_rounds=rounds,
        latest_round=latest_round(rounds),
        news=news,
    )


def count_rows(session: Session) -> dict[str, int]:
    def count(model: Any) -> int:
        return session.exec(select(func.count()).select_from(model)).one()

    return {
        "companies": count(Company),
        "facts": count(Fact),
        "news": count(CompanyNewsRow),
        "runs": count(RunLog),
    }


def latest_companies_ingest_run(session: Session) -> RunLog | None:
    """Most recent `companies_ingest` run that actually searched Companies House (as opposed to
    an `--backfill-officers` run logged under the same kind, whose stats have no `searched`)."""
    runs = session.exec(
        select(RunLog)
        .where(RunLog.kind == RunKind.COMPANIES_INGEST.value)
        .order_by(col(RunLog.id).desc())
        .limit(20)
    ).all()
    return next((run for run in runs if "searched" in run.stats), None)


def keyword_counts(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for keywords in session.exec(select(Company.matched_keywords)).all():
        for keyword in keywords:
            counts[keyword] = counts.get(keyword, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def incorporation_year_counts(session: Session) -> dict[int, int]:
    counts: dict[int, int] = {}
    for incorporated in session.exec(select(Company.incorporated)).all():
        if incorporated is not None:
            counts[incorporated.year] = counts.get(incorporated.year, 0) + 1
    return dict(sorted(counts.items()))


def enriched_company_count(session: Session) -> int:
    """Companies with a classification, i.e. a submitted enrichment profile."""
    return session.exec(
        select(func.count()).select_from(Company).where(col(Company.category).is_not(None))
    ).one()


def category_counts(session: Session) -> dict[str, int]:
    """Classified candidates only; a company with no category yet is left out."""
    counts: dict[str, int] = {}
    for category in session.exec(select(Company.category)).all():
        if category is None:
            continue
        counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def _field_counts(session: Session, field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in _winning_values(session, field).values():
        key = value if value is not None else NOT_DISCLOSED
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def stage_counts(session: Session) -> dict[str, int]:
    return _field_counts(session, "stage")


def business_status_counts(session: Session) -> dict[str, int]:
    return _field_counts(session, "business_status")


def founded_year_counts(session: Session) -> dict[int, int]:
    """Enriched companies only. `ingest` pre-populates every candidate's "founded" fact from its
    Companies House incorporation date, so a plain `_winning_values` scan would include
    unenriched companies too; restrict to companies with a category, i.e. a submitted profile."""
    enriched_ids = set(
        session.exec(select(Company.id).where(col(Company.category).is_not(None))).all()
    )
    counts: dict[int, int] = {}
    for company_id, value in _winning_values(session, "founded").items():
        if value is None or company_id not in enriched_ids:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


_VALUATION_BINS = [
    (0, 10, "<$10m"),
    (10, 50, "$10-50m"),
    (50, 100, "$50-100m"),
    (100, 500, "$100-500m"),
    (500, 1000, "$500m-1bn"),
    (1000, float("inf"), ">$1bn"),
]


def valuation_bin_counts(session: Session) -> dict[str, int]:
    counts = {label: 0 for *_, label in _VALUATION_BINS}
    counts[NOT_DISCLOSED] = 0
    for value in _winning_values(session, "valuation_million_usd").values():
        if value is None:
            counts[NOT_DISCLOSED] += 1
            continue
        for low, high, label in _VALUATION_BINS:
            if low <= value < high:
                counts[label] += 1
                break
    return counts


def officer_count_distribution(session: Session) -> dict[str, int]:
    """Active officer count across the whole candidate pool, binned (see D34 in `candidates.py`
    for why this count matters: fewer than 2 is overwhelmingly a shell or trade body)."""
    counts: dict[str, int] = {}
    for active_officers in session.exec(select(Company.active_officers)).all():
        if active_officers is None:
            key = "Unknown"
        elif active_officers >= 6:
            key = "6+"
        else:
            key = str(active_officers)
        counts[key] = counts.get(key, 0) + 1
    order = ["0", "1", "2", "3", "4", "5", "6+", "Unknown"]
    return {key: counts[key] for key in order if key in counts}


def status_options(session: Session) -> list[str]:
    """Distinct Companies House legal statuses (e.g. "active", "dissolved") across the pool."""
    statuses = session.exec(
        select(Company.status).where(col(Company.status).is_not(None)).distinct()
    ).all()
    return sorted(statuses)


def business_status_options() -> list[str]:
    """Researched business status values (see `BusinessStatus`), distinct from the Companies
    House legal status in `status_options`."""
    return [value.value for value in BusinessStatus]


_AGE_BINS = [(0, 30, "<30"), (30, 40, "30-39"), (40, 50, "40-49"), (50, 60, "50-59")]


def officer_age_distribution(session: Session) -> dict[str, int]:
    """Officer age (from `birth_year`), binned; `60+` and `Unknown` (no birth year) close the
    list, matching the ordering convention in `officer_count_distribution`."""
    this_year = date.today().year
    counts: dict[str, int] = {}
    for birth_year in session.exec(select(OfficerRow.birth_year)).all():
        if birth_year is None:
            key = "Unknown"
        else:
            age = this_year - birth_year
            key = next((label for low, high, label in _AGE_BINS if low <= age < high), "60+")
        counts[key] = counts.get(key, 0) + 1
    order = [label for *_, label in _AGE_BINS] + ["60+", "Unknown"]
    return {key: counts[key] for key in order if key in counts}


def officer_nationality_counts(session: Session, top_n: int = 10) -> dict[str, int]:
    """Top officer nationalities, grouping the long tail into "Other" (missing is "Unknown")."""
    counts: dict[str, int] = {}
    for nationality in session.exec(select(OfficerRow.nationality)).all():
        key = nationality or "Unknown"
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: -item[1])
    top = dict(ranked[:top_n])
    other = sum(count for _, count in ranked[top_n:])
    if other:
        top["Other"] = other
    return top


def locality_counts(session: Session, top_n: int = 12) -> dict[str, int]:
    """Top localities among enriched companies, grouping the long tail into "Other"."""
    counts: dict[str, int] = {}
    query = select(Company.locality).where(col(Company.category).is_not(None))
    for locality in session.exec(query).all():
        key = locality or "Unknown"
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: -item[1])
    top = dict(ranked[:top_n])
    other = sum(count for _, count in ranked[top_n:])
    if other:
        top["Other"] = other
    return top


def search_companies(
    session: Session,
    *,
    text: str = "",
    min_score: int = 0,
    keyword: str | None = None,
    incorporated_from: date | None = None,
    status: str | None = None,
    business_status: str | None = None,
    category: str | None = None,
    stage: str | None = None,
    min_funding_million_usd: int | None = None,
    founded_from: int | None = None,
    founded_to: int | None = None,
    limit: int = 500,
) -> CompanySearch:
    """Candidates ordered by score. `stage`, `business_status` and the founded years match the
    winning fact; funding is the amount of the latest round (D22). A company whose value is not
    known never matches a filter on it. Coverage counts known values among all matches."""
    query = select(Company).where(Company.candidate_score >= min_score)
    if text.strip():
        query = query.where(col(Company.name).icontains(text.strip(), autoescape=True))
    if incorporated_from is not None:
        query = query.where(col(Company.incorporated) >= incorporated_from)
    if status:
        query = query.where(Company.status == status)
    if category:
        query = query.where(Company.category == category)
    ordered = query.order_by(col(Company.candidate_score).desc(), col(Company.name))
    rows = list(session.exec(ordered).all())
    if keyword:
        rows = [row for row in rows if keyword in row.matched_keywords]

    winners = _winning_by_company(session, ("stage", "business_status", "founded"))
    funding = _latest_funding_amounts(session)

    def value(company: Company, field: str) -> Any:
        return _parse(winners.get(company.id, {}).get(field))

    if business_status:
        rows = [row for row in rows if value(row, "business_status") == business_status]
    if stage:
        rows = [row for row in rows if value(row, "stage") == stage]
    if founded_from is not None or founded_to is not None:
        low, high = founded_from or 0, founded_to or 9999
        rows = [
            row
            for row in rows
            if (year := value(row, "founded")) is not None and low <= year <= high
        ]
    if min_funding_million_usd is not None:
        rows = [
            row
            for row in rows
            if (amount := funding.get(row.id)) is not None and amount >= min_funding_million_usd
        ]

    coverage = Coverage(
        matched=len(rows),
        stage_known=sum(value(row, "stage") is not None for row in rows),
        funding_known=sum(funding.get(row.id) is not None for row in rows),
    )
    return CompanySearch(companies=rows[:limit], coverage=coverage)


def company_facts(session: Session, company_id: int) -> list[Fact]:
    """Every fact row, including superseded ones; use `get_profile` for the winning values."""
    return list(
        session.exec(
            select(Fact).where(Fact.company_id == company_id).order_by(col(Fact.field))
        ).all()
    )


def company_funding_rounds(session: Session, company_id: int) -> list[FundingRoundRow]:
    return list(
        session.exec(
            select(FundingRoundRow)
            .where(FundingRoundRow.company_id == company_id)
            .order_by(col(FundingRoundRow.announced).desc())
        ).all()
    )


def company_officers(session: Session, company_id: int) -> list[OfficerRow]:
    return list(
        session.exec(
            select(OfficerRow)
            .where(OfficerRow.company_id == company_id)
            .order_by(col(OfficerRow.role), col(OfficerRow.name))
        ).all()
    )


def search_news(
    session: Session, *, text: str = "", limit: int = 200
) -> list[tuple[CompanyNewsRow, str]]:
    """News rows with their company's name, newest first."""
    query = select(CompanyNewsRow, Company.name).join(
        Company, col(CompanyNewsRow.company_id) == col(Company.id)
    )
    if text.strip():
        pattern = text.strip()
        query = query.where(
            col(CompanyNewsRow.title).icontains(pattern, autoescape=True)
            | col(CompanyNewsRow.summary).icontains(pattern, autoescape=True)
        )
    query = query.order_by(col(CompanyNewsRow.published).desc(), col(CompanyNewsRow.url))
    return [(row, name) for row, name in session.exec(query.limit(limit)).all()]


def companies_house_url(company_number: str | None) -> str | None:
    return PUBLIC_COMPANY_URL.format(number=company_number) if company_number else None
