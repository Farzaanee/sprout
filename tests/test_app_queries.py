from datetime import date

import pytest
from sqlmodel import Session

from sprout.app import queries
from sprout.candidates import assess
from sprout.db import add_news_items, get_engine, init_db, save_fact, upsert_company
from sprout.models import Confidence, NewsItem, Sourced
from sprout.sources.companies_house import CompanyHit

PLAN = """
| Phase | Title | Hours |
|---|---|---|
| 0 | Setup & definitions | 2 |
| 1 | Data foundations | 6 |
| 2 | First agent | 8 |
| 3 | Pipeline | 8 |
"""

NOTES = """
## Phase 0 — Setup

**Status: done except two items that need your credentials**

- [x] one
- [ ] two

## Phase 1 — Data

**Status: done.** Verified.

- [x] a
- [x] b

## Phase 2 — Agent

**Status: in progress.**

- [ ] a
"""


def test_parse_phases_states_and_criteria():
    phases = {p.number: p for p in queries.parse_phases(PLAN, NOTES)}
    assert [phases[n].state for n in range(4)] == [
        "In progress",  # "done except…" with an unticked criterion is not Done
        "Done",
        "In progress",
        "Not started",
    ]
    assert (phases[0].criteria_done, phases[0].criteria_total) == (1, 2)
    assert (phases[1].criteria_done, phases[1].criteria_total) == (2, 2)
    assert phases[1].title == "Data foundations" and phases[1].hours == "6"
    assert phases[3].status_line == ""


def test_parse_phases_handles_empty_notes():
    assert {p.state for p in queries.parse_phases(PLAN, "")} == {"Not started"}


@pytest.fixture
def session(tmp_path):
    engine = get_engine(tmp_path / "test.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def _add(session, number, name, incorporated, sic=("62012",)):
    hit = CompanyHit(number, name, "active", incorporated, sic, "London")
    company, _ = upsert_company(session, hit, assess(name, sic, incorporated=incorporated))
    session.commit()
    return company


def test_search_companies_filters_and_orders_by_score(session):
    _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    _add(session, "2", "Plain AI Ltd", date(2015, 6, 1))
    _add(session, "3", "100% Robotics Ltd", date(2021, 1, 1))

    names = [c.name for c in queries.search_companies(session)]
    assert names[0] == "Neural Robotics Ltd"  # highest score first
    assert [c.name for c in queries.search_companies(session, text="plain")] == ["Plain AI Ltd"]
    assert [c.name for c in queries.search_companies(session, text="100%")] == [
        "100% Robotics Ltd"
    ]  # LIKE wildcards are escaped
    recent = queries.search_companies(session, incorporated_from=date(2020, 1, 1))
    assert {c.name for c in recent} == {"Neural Robotics Ltd", "100% Robotics Ltd"}
    assert [c.name for c in queries.search_companies(session, keyword="neural")] == [
        "Neural Robotics Ltd"
    ]
    assert queries.search_companies(session, min_score=99) == []


def test_counts_facts_keywords_and_years(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    save_fact(
        session,
        company.id,
        "hq",
        Sourced[str](value="London", source_url="https://example.com", confidence=Confidence.LOW),
    )
    session.commit()
    assert queries.count_rows(session) == {"companies": 1, "facts": 1, "news_items": 0, "runs": 0}
    assert [f.field for f in queries.company_facts(session, company.id)] == ["hq"]
    assert queries.keyword_counts(session)["neural"] == 1
    assert queries.incorporation_year_counts(session) == {2020: 1}


def test_search_news_matches_title_or_summary_newest_first(session):
    add_news_items(
        session,
        [
            NewsItem(url="https://a.example/1", title="Seed round", published=date(2026, 1, 1)),
            NewsItem(
                url="https://a.example/2",
                title="Other",
                summary="a seed deal",
                published=date(2026, 2, 1),
            ),
            NewsItem(url="https://a.example/3", title="Unrelated", published=date(2026, 3, 1)),
        ],
    )
    session.commit()
    assert [n.title for n in queries.search_news(session, text="seed")] == ["Other", "Seed round"]
    assert len(queries.search_news(session)) == 3


def test_companies_house_url():
    assert queries.companies_house_url("123").endswith("/company/123")
    assert queries.companies_house_url(None) is None
