"""Read-only data access for the Streamlit app, kept free of Streamlit so it can be tested."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlmodel import Session, col, func, select

from sprout.db import Company, Fact, FundingRoundRow, NewsItemRow, RunLog
from sprout.sources.companies_house import PUBLIC_COMPANY_URL


@dataclass(frozen=True)
class PhaseStatus:
    number: int
    title: str
    hours: str
    state: str  # "Done" | "In progress" | "Not started"
    status_line: str
    criteria_done: int
    criteria_total: int


_PLAN_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(\d+)\s*\|$", re.MULTILINE)
_HEADING = re.compile(r"^## .+$", re.MULTILINE)
_NOTES_SECTION = re.compile(r"^## Phase (\d+) - .+$", re.MULTILINE)
_STATUS = re.compile(r"^\*\*Status:\s*(.+?)\*\*", re.MULTILINE)


def parse_phases(plan_text: str, notes_text: str) -> list[PhaseStatus]:
    """Combine the plan's timeline table with the per-phase sections of the implementation notes.

    A phase with no notes section is "Not started"; one whose status starts with "done" and has no
    unticked acceptance criterion is "Done"; anything else is "In progress".
    """
    headings = [h.start() for h in _HEADING.finditer(notes_text)] + [len(notes_text)]
    sections: dict[int, str] = {}
    for match in _NOTES_SECTION.finditer(notes_text):
        end = next(pos for pos in headings if pos >= match.end())
        sections[int(match.group(1))] = notes_text[match.end() : end]

    phases = []
    for number, title, hours in _PLAN_ROW.findall(plan_text):
        number = int(number)
        body = sections.get(number)
        if body is None:
            phases.append(PhaseStatus(number, title, hours, "Not started", "", 0, 0))
            continue
        status = _STATUS.search(body)
        status_line = status.group(1) if status else ""
        done = len(re.findall(r"^- \[x\]", body, re.MULTILINE))
        open_ = len(re.findall(r"^- \[ \]", body, re.MULTILINE))
        finished = status_line.lower().startswith("done") and open_ == 0
        state = "Done" if finished else "In progress"
        phases.append(PhaseStatus(number, title, hours, state, status_line, done, done + open_))
    return phases


def count_rows(session: Session) -> dict[str, int]:
    def count(model: Any) -> int:
        return session.exec(select(func.count()).select_from(model)).one()

    return {
        "companies": count(Company),
        "facts": count(Fact),
        "news_items": count(NewsItemRow),
        "runs": count(RunLog),
    }


def recent_runs(session: Session, limit: int = 20) -> list[RunLog]:
    return list(session.exec(select(RunLog).order_by(col(RunLog.id).desc()).limit(limit)).all())


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


def search_companies(
    session: Session,
    *,
    text: str = "",
    min_score: int = 0,
    keyword: str | None = None,
    incorporated_from: date | None = None,
    limit: int = 500,
) -> list[Company]:
    query = select(Company).where(Company.candidate_score >= min_score)
    if text.strip():
        query = query.where(col(Company.name).icontains(text.strip(), autoescape=True))
    if incorporated_from is not None:
        query = query.where(col(Company.incorporated) >= incorporated_from)
    query = query.order_by(col(Company.candidate_score).desc(), col(Company.name))
    rows = session.exec(query).all()
    if keyword:
        rows = [row for row in rows if keyword in row.matched_keywords]
    return list(rows[:limit])


def company_facts(session: Session, company_id: int) -> list[Fact]:
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


def search_news(session: Session, *, text: str = "", limit: int = 200) -> list[NewsItemRow]:
    query = select(NewsItemRow)
    if text.strip():
        pattern = text.strip()
        query = query.where(
            col(NewsItemRow.title).icontains(pattern, autoescape=True)
            | col(NewsItemRow.summary).icontains(pattern, autoescape=True)
        )
    query = query.order_by(col(NewsItemRow.published).desc(), col(NewsItemRow.id).desc())
    return list(session.exec(query.limit(limit)).all())


def companies_house_url(company_number: str | None) -> str | None:
    return PUBLIC_COMPANY_URL.format(number=company_number) if company_number else None
