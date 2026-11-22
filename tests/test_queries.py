import ast
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlmodel import Session

from sprout import queries
from sprout.candidates import assess
from sprout.db import (
    Company,
    CompanyNewsRow,
    FundingRoundRow,
    OfficerRow,
    RunLog,
    get_engine,
    init_db,
    save_fact,
    upsert_company,
    utcnow,
)
from sprout.models import Confidence, Sourced
from sprout.sources.companies_house import CompanyHit

QUERIES_SOURCE = Path(queries.__file__)
URL = "https://example.com"


def test_queries_module_imports_only_db_models_and_status():
    allowed = {"sprout.db", "sprout.models", "sprout.status"}
    imported: set[str] = set()
    for node in ast.walk(ast.parse(QUERIES_SOURCE.read_text())):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)
    assert {m for m in imported if m.split(".")[0] in {"sprout", "streamlit"}} <= allowed


@pytest.fixture
def session(tmp_path):
    engine = get_engine(tmp_path / "test.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def _add(session, number, name, incorporated, sic=("62012",), status="active"):
    hit = CompanyHit(number, name, status, incorporated, sic, "London")
    company, _ = upsert_company(session, hit, assess(name, sic, incorporated=incorporated))
    session.commit()
    return company


def _fact(
    session, company, field, value, *, url=URL, confidence=Confidence.HIGH, retrieved_at=None
):
    kwargs = {} if retrieved_at is None else {"retrieved_at": retrieved_at}
    save_fact(
        session,
        company.id,
        field,
        Sourced(value=value, source_url=url, confidence=confidence, **kwargs),
    )
    session.commit()


def _round(session, company, stage, amount, announced):
    session.add(
        FundingRoundRow(
            company_id=company.id,
            stage=stage,
            amount_million_usd=amount,
            announced=announced,
            source_url=URL,
            retrieved_at=utcnow(),
            confidence=Confidence.HIGH.value,
        )
    )
    session.commit()


def _names(result):
    return [c.name for c in result.companies]


def test_search_companies_filters_and_orders_by_score(session):
    _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    _add(session, "2", "Plain AI Ltd", date(2015, 6, 1))
    _add(session, "3", "100% Robotics Ltd", date(2021, 1, 1))

    assert _names(queries.search_companies(session))[0] == "Neural Robotics Ltd"
    assert _names(queries.search_companies(session, text="plain")) == ["Plain AI Ltd"]
    assert _names(queries.search_companies(session, text="100%")) == [
        "100% Robotics Ltd"
    ]  # LIKE wildcards are escaped
    recent = queries.search_companies(session, incorporated_from=date(2020, 1, 1))
    assert set(_names(recent)) == {"Neural Robotics Ltd", "100% Robotics Ltd"}
    assert _names(queries.search_companies(session, keyword="neural")) == ["Neural Robotics Ltd"]
    assert queries.search_companies(session, min_score=99).companies == []


def test_search_companies_filters_by_status(session):
    _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1), status="active")
    _add(session, "2", "Old Robotics Ltd", date(2015, 1, 1), status="dissolved")

    assert queries.status_options(session) == ["active", "dissolved"]
    assert _names(queries.search_companies(session, status="dissolved")) == ["Old Robotics Ltd"]


@pytest.fixture
def pool(session):
    """Three enriched companies with different known and unknown values, one unenriched."""
    a = _add(session, "1", "Alpha Robotics Ltd", date(2020, 1, 1))
    b = _add(session, "2", "Beta Robotics Ltd", date(2018, 1, 1))
    c = _add(session, "3", "Gamma Robotics Ltd", date(2019, 1, 1))
    d = _add(session, "4", "Delta Robotics Ltd", date(2021, 1, 1))
    a.category, b.category, c.category = "Robotics and automation", "Fintech and payments", "Other"
    session.add_all([a, b, c])
    session.commit()
    _fact(session, a, "stage", "Seed")
    _fact(session, a, "founded", 2019)
    _round(session, a, "Seed", 5, date(2024, 1, 1))
    _round(session, a, "Series A", 30, date(2025, 1, 1))  # the latest round
    _fact(session, b, "stage", "Series A")
    _fact(session, b, "founded", 2017)
    _round(session, b, "Series A", None, date(2025, 6, 1))  # latest round, amount unknown
    _round(session, b, "Seed", 50, date(2023, 1, 1))
    _fact(session, c, "stage", None)  # researched, not disclosed
    _fact(session, c, "founded", 2018)
    return a, b, c, d


def test_search_companies_filters_by_category_stage_and_business_status(session, pool):
    assert _names(queries.search_companies(session, category="Fintech and payments")) == [
        "Beta Robotics Ltd"
    ]
    assert _names(queries.search_companies(session, stage="Seed")) == ["Alpha Robotics Ltd"]
    assert queries.search_companies(session, stage="Series B").companies == []
    _fact(session, pool[0], "business_status", "Acquired or merged")
    found = queries.search_companies(session, business_status="Acquired or merged")
    assert _names(found) == ["Alpha Robotics Ltd"]


def test_search_companies_filters_by_founded_year_range(session, pool):
    def names(**kwargs):
        return set(_names(queries.search_companies(session, **kwargs)))

    assert names(founded_from=2018) == {"Alpha Robotics Ltd", "Gamma Robotics Ltd"}
    assert names(founded_to=2018) == {"Beta Robotics Ltd", "Gamma Robotics Ltd"}
    assert names(founded_from=2018, founded_to=2018) == {"Gamma Robotics Ltd"}


def test_search_companies_funding_filter_uses_the_latest_round(session, pool):
    over_20 = queries.search_companies(session, min_funding_million_usd=20)
    assert _names(over_20) == ["Alpha Robotics Ltd"]  # Beta's older $50m round is not the latest
    assert queries.search_companies(session, min_funding_million_usd=0).coverage.matched == 1
    assert queries.search_companies(session, min_funding_million_usd=31).companies == []


def test_search_companies_coverage_counts_known_values_over_all_matches(session, pool):
    coverage = queries.search_companies(session, limit=1).coverage
    assert len(queries.search_companies(session, limit=1).companies) == 1
    assert coverage == queries.Coverage(matched=4, stage_known=2, funding_known=1)

    enriched_only = queries.search_companies(session, founded_from=2000)
    assert enriched_only.coverage == queries.Coverage(matched=3, stage_known=2, funding_known=1)


def test_search_companies_loads_winning_facts_once(session, pool):
    statements: list[str] = []
    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))
    queries.search_companies(session, stage="Seed", business_status="x", founded_from=2000)
    assert sum("FROM fact" in s for s in statements) == 1


def test_search_companies_text_matches_name_or_winning_one_liner(session, pool):
    a, b, _, _ = pool
    _fact(session, a, "one_liner", "Warehouse robots for retailers", retrieved_at=utcnow())
    _fact(session, b, "one_liner", "Payments for SMEs")
    newer = utcnow() + timedelta(days=1)
    _fact(session, b, "one_liner", None, url="https://b.example", retrieved_at=newer)  # newer NULL

    def names(text):
        return set(_names(queries.search_companies(session, text=text)))

    assert names("WAREHOUSE") == {"Alpha Robotics Ltd"}
    assert names("payments") == {"Beta Robotics Ltd"}  # a valued fact beats the newer NULL (A6)
    assert names("gamma") == {"Gamma Robotics Ltd"}  # name still matches
    assert "100%" not in names("100%")  # wildcards are not special in the one-liner either
    assert queries.search_companies(session, text="100%").companies == []


def test_search_companies_one_liner_ignores_superseded_value(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    old = datetime.now(UTC) - timedelta(days=30)
    _fact(session, company, "one_liner", "Old pitch", url="https://a.example", retrieved_at=old)
    _fact(session, company, "one_liner", "New pitch", url="https://b.example")

    assert queries.search_companies(session, text="old pitch").companies == []
    assert _names(queries.search_companies(session, text="new pitch")) == ["Neural Robotics Ltd"]


def test_search_companies_enriched_only_and_max_funding(session, pool):
    assert len(queries.search_companies(session).companies) == 4
    enriched = queries.search_companies(session, enriched_only=True)
    assert set(_names(enriched)) == {
        "Alpha Robotics Ltd",
        "Beta Robotics Ltd",
        "Gamma Robotics Ltd",
    }
    assert enriched.coverage.matched == 3

    under_40 = queries.search_companies(session, max_funding_million_usd=40)
    assert _names(under_40) == ["Alpha Robotics Ltd"]  # unknown latest amounts never match
    assert queries.search_companies(session, max_funding_million_usd=29).companies == []
    band = queries.search_companies(
        session, min_funding_million_usd=30, max_funding_million_usd=30, enriched_only=True
    )
    assert _names(band) == ["Alpha Robotics Ltd"]


def test_company_cards_keep_order_and_carry_provenance(session, pool):
    a, b, c, d = pool
    _fact(session, a, "one_liner", "Warehouse robots", url="https://a.example")

    cards = queries.company_cards(session, [d.id, a.id, c.id, 999])

    assert [card.company.name for card in cards] == [
        "Delta Robotics Ltd",
        "Alpha Robotics Ltd",
        "Gamma Robotics Ltd",
    ]  # given order, unknown id skipped
    delta, alpha, gamma = cards
    assert (delta.one_liner, delta.stage, delta.founded, delta.latest_round) == (None,) * 4
    assert alpha.one_liner.value == "Warehouse robots"
    assert alpha.one_liner.source_url == "https://a.example/"
    assert alpha.stage.value == "Seed" and alpha.founded.value == 2019
    assert alpha.latest_round.stage == "Series A"
    assert gamma.stage.disclosed is False  # researched, nothing found
    assert gamma.stage.display_value == queries.NOT_DISCLOSED


def test_company_cards_empty_ids_and_query_count(session, pool):
    assert queries.company_cards(session, []) == []

    statements: list[str] = []
    event.listen(session.get_bind(), "before_cursor_execute", lambda *a: statements.append(a[2]))
    queries.company_cards(session, [company.id for company in pool])
    assert sum("FROM fact" in s for s in statements) == 1
    assert sum("FROM funding_round" in s for s in statements) == 1


def test_funding_by_stage_orders_stages_and_keeps_unknown_amounts_none(session, pool):
    a, b, c, _ = pool
    _round(session, c, "Pre-seed", None, date(2022, 1, 1))  # no amount anywhere in this stage

    result = queries.funding_by_stage(session)

    assert [s.stage for s in result.stages] == ["Pre-seed", "Seed", "Series A"]  # Stage order
    pre_seed, seed, series_a = result.stages
    assert (pre_seed.rounds, pre_seed.amount_known) == (1, 0)
    assert pre_seed.total_million_usd is None and pre_seed.median_million_usd is None
    assert (seed.rounds, seed.amount_known, seed.total_million_usd) == (2, 2, 55)
    assert seed.median_million_usd == 27.5
    assert (series_a.rounds, series_a.amount_known, series_a.total_million_usd) == (2, 1, 30)
    assert series_a.median_million_usd == 30
    assert (result.companies, result.companies_with_rounds) == (4, 3)


def test_funding_by_stage_puts_off_enum_stages_in_unknown_last(session, pool):
    a, b, _, _ = pool
    _round(session, a, "Mezzanine", 7, date(2025, 3, 1))
    _round(session, b, "", None, None)

    result = queries.funding_by_stage(session)

    assert [s.stage for s in result.stages] == ["Seed", "Series A", "Unknown"]
    assert (result.stages[-1].rounds, result.stages[-1].total_million_usd) == (2, 7)


def test_funding_by_stage_restricts_to_company_ids(session, pool):
    a, b, c, d = pool

    only_b = queries.funding_by_stage(session, [b.id, d.id])
    assert [(s.stage, s.rounds) for s in only_b.stages] == [("Seed", 1), ("Series A", 1)]
    assert (only_b.companies, only_b.companies_with_rounds) == (2, 1)

    nobody = queries.funding_by_stage(session, [])
    assert nobody == queries.FundingByStage(stages=[], companies=0, companies_with_rounds=0)
    assert queries.funding_by_stage(session, [c.id]).stages == []


def test_recent_rounds_newest_first_undated_last_and_keeps_null_amount(session, pool):
    a, b, c, d = pool
    _round(session, c, "Growth", 100, None)  # undated

    rounds = queries.recent_rounds(session)

    assert [(r.company_name, r.round.stage) for r in rounds] == [
        ("Beta Robotics Ltd", "Series A"),  # 2025-06, amount unknown but kept
        ("Alpha Robotics Ltd", "Series A"),  # 2025-01
        ("Alpha Robotics Ltd", "Seed"),  # 2024-01
        ("Beta Robotics Ltd", "Seed"),  # 2023-01
        ("Gamma Robotics Ltd", "Growth"),  # undated last
    ]
    assert rounds[0].round.amount_million_usd is None
    assert rounds[0].company_id == b.id
    assert len(queries.recent_rounds(session, limit=2)) == 2


def test_recent_rounds_restricts_to_company_ids(session, pool):
    a, b, _, _ = pool

    assert {r.company_id for r in queries.recent_rounds(session, [a.id])} == {a.id}
    assert queries.recent_rounds(session, []) == []


@pytest.fixture
def news(session):
    robotics = Company(company_number="1", name="ACME", category="Robotics and automation")
    fintech = Company(company_number="2", name="PayCo", category="Fintech and payments")
    session.add_all([robotics, fintech])
    session.commit()
    session.add_all(
        [
            CompanyNewsRow(
                company_id=robotics.id,
                url="https://a.example/1",
                title="Raises Series A",
                news_kind="funding",
                published=date(2026, 3, 1),
            ),
            CompanyNewsRow(
                company_id=robotics.id,
                url="https://a.example/2",
                title="Minor update",
                published=date(2026, 1, 1),
            ),
            CompanyNewsRow(
                company_id=fintech.id,
                url="https://a.example/3",
                title="Acquired",
                news_kind="acquisition",
                published=None,
            ),
            CompanyNewsRow(
                company_id=fintech.id,
                url="https://a.example/4",
                title="Launch",
                news_kind="product",
                published=date(2026, 2, 1),
            ),
        ]
    )
    session.commit()
    return robotics, fintech


def _titles(session, **kwargs):
    return [row.title for row, _ in queries.search_news(session, **kwargs)]


def test_search_news_big_only_category_and_since(session, news):
    robotics, fintech = news

    assert len(_titles(session)) == 4
    assert _titles(session, big_only=True) == [
        "Raises Series A",
        "Launch",
        "Acquired",
    ]  # undated last
    assert _titles(session, category="Fintech and payments") == ["Launch", "Acquired"]
    assert _titles(session, since=date(2026, 2, 1)) == ["Raises Series A", "Launch"]  # undated out
    assert _titles(
        session, big_only=True, category="Fintech and payments", since=date(2026, 1, 1)
    ) == ["Launch"]
    assert _titles(session, category="Nothing") == []


def test_search_news_company_ids_none_is_unrestricted_and_empty_is_nobody(session, news):
    robotics, fintech = news

    assert _titles(session, company_ids=[robotics.id]) == ["Raises Series A", "Minor update"]
    assert len(_titles(session, company_ids=None)) == 4
    assert _titles(session, company_ids=[]) == []
    assert _titles(session, company_ids=set()) == []


def _add_officer(session, company_id, name, role, birth_year=None, nationality=None):
    session.add(
        OfficerRow(
            company_id=company_id,
            name=name,
            role=role,
            birth_year=birth_year,
            nationality=nationality,
            source_url="https://example.com",
            retrieved_at=utcnow(),
            confidence=Confidence.MEDIUM.value,
        )
    )
    session.commit()


def test_officer_age_distribution_bins_by_birth_year(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    this_year = date.today().year
    _add_officer(session, company.id, "SMITH, Jane", "director", birth_year=this_year - 25)
    _add_officer(session, company.id, "DOE, John", "secretary", birth_year=this_year - 65)
    _add_officer(session, company.id, "LEE, Sam", "member")  # no birth year

    assert queries.officer_age_distribution(session) == {"<30": 1, "60+": 1, "Unknown": 1}


def test_officer_nationality_counts_groups_the_long_tail(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    _add_officer(session, company.id, "A", "director", nationality="British")
    _add_officer(session, company.id, "B", "director", nationality="British")
    _add_officer(session, company.id, "C", "director", nationality="French")
    _add_officer(session, company.id, "D", "director")  # no nationality

    assert queries.officer_nationality_counts(session, top_n=1) == {
        "British": 2,
        "Other": 2,  # French and Unknown pushed into the tail
    }


def test_company_officers_orders_by_role_then_name(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    _add_officer(session, company.id, "DOE, John", "director")
    _add_officer(session, company.id, "SMITH, Jane", "director")
    _add_officer(session, company.id, "ROBERTS, Pat", "secretary")

    assert [o.name for o in queries.company_officers(session, company.id)] == [
        "DOE, John",
        "SMITH, Jane",
        "ROBERTS, Pat",
    ]


def test_counts_facts_keywords_and_years(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    _fact(session, company, "hq", "London", confidence=Confidence.LOW)
    assert queries.count_rows(session) == {"companies": 1, "facts": 1, "news": 0, "runs": 0}
    assert [f.field for f in queries.company_facts(session, company.id)] == ["hq"]
    assert queries.keyword_counts(session)["neural"] == 1
    assert queries.incorporation_year_counts(session) == {2020: 1}


def test_latest_companies_ingest_run_skips_backfill_runs(session):
    def ingest_run(stats):
        run = RunLog(kind="companies_ingest", status="ok", stats=stats)
        session.add(run)
        session.commit()
        return run

    assert queries.latest_companies_ingest_run(session) is None
    first = ingest_run({"searched": 465, "new": 305})
    ingest_run({"checked": 314, "updated": 314})
    assert queries.latest_companies_ingest_run(session).id == first.id
    second = ingest_run({"searched": 2543, "new": 1358})
    assert queries.latest_companies_ingest_run(session).id == second.id


def test_pool_overview_counts(session):
    enriched = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    unclassified = _add(session, "2", "Plain AI Ltd", date(2015, 6, 1))
    no_data = _add(session, "3", "Mystery Startup Ltd", date(2021, 1, 1))
    enriched.category = "Robotics and automation"
    no_data.category = "Other"
    session.add(enriched)
    session.add(no_data)
    _fact(session, enriched, "stage", "Seed")
    _fact(session, enriched, "valuation_million_usd", 120)
    _fact(session, enriched, "founded", 2019)
    # researched but nothing found, as opposed to never researched
    _fact(session, no_data, "stage", None)
    _fact(session, no_data, "valuation_million_usd", None)
    # unclassified company still has a registry-sourced "founded" fact from ingestion, but it
    # must not count as "enriched"
    _fact(session, unclassified, "founded", 2015, confidence=Confidence.MEDIUM)

    assert queries.enriched_company_count(session) == 2
    assert queries.category_counts(session) == {
        "Robotics and automation": 1,
        "Other": 1,
    }
    assert queries.stage_counts(session) == {"Seed": 1, queries.NOT_DISCLOSED: 1}
    valuations = queries.valuation_bin_counts(session)
    assert valuations["$100-500m"] == 1
    assert valuations[queries.NOT_DISCLOSED] == 1
    assert queries.founded_year_counts(session) == {2019: 1}  # unclassified company excluded
    assert queries.locality_counts(session) == {"London": 2}  # unclassified company excluded


def test_winning_values_prefers_valued_then_newest_then_confidence(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    _fact(session, company, "stage", None, url="https://a.example", confidence=Confidence.LOW)
    _fact(session, company, "stage", "Series A", url="https://b.example", confidence=Confidence.LOW)

    assert queries.stage_counts(session) == {"Series A": 1}


def test_business_status_counts_uses_the_winning_fact_per_company(session):
    acquired = _add(session, "1", "Acquired Robotics Ltd", date(2019, 1, 1))
    unclear = _add(session, "2", "Unclear Robotics Ltd", date(2020, 1, 1))
    _fact(session, acquired, "business_status", "Acquired or merged", url="https://a.example")
    _fact(
        session,
        unclear,
        "business_status",
        None,
        url="https://b.example",
        confidence=Confidence.LOW,
    )

    assert queries.business_status_counts(session) == {
        "Acquired or merged": 1,
        queries.NOT_DISCLOSED: 1,
    }


def test_get_profile_returns_none_for_an_unknown_id(session):
    assert queries.get_profile(session, 999) is None


def test_get_profile_picks_the_winning_fact_per_field(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    now = datetime.now(UTC)
    old, new = now - timedelta(days=30), now
    # newest valued fact wins over an older valued one
    _fact(session, company, "stage", "Seed", url="https://a.example", retrieved_at=old)
    _fact(session, company, "stage", "Series A", url="https://b.example", retrieved_at=new)
    # a valued fact wins over a newer NULL one
    _fact(session, company, "hq", "London", url="https://a.example", retrieved_at=old)
    _fact(session, company, "hq", None, url="https://b.example", retrieved_at=new)

    profile = queries.get_profile(session, company.id)

    stage = profile.facts["stage"]
    assert (stage.value, stage.disclosed, stage.source_url) == (
        "Series A",
        True,
        "https://b.example/",
    )
    assert stage.confidence == "high" and stage.retrieved_at == new
    assert profile.facts["hq"].value == "London"
    assert profile.company.id == company.id


def test_get_profile_treats_a_null_winner_as_not_disclosed_and_absent_as_not_researched(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    _fact(session, company, "target_user", None)

    profile = queries.get_profile(session, company.id)

    fact = profile.facts["target_user"]
    assert fact.value is None and fact.disclosed is False
    assert fact.display_value == queries.NOT_DISCLOSED
    assert "business_model" not in profile.facts  # never researched


def test_get_profile_returns_rounds_latest_round_and_news(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    _round(session, company, "Seed", 5, date(2024, 1, 1))
    _round(session, company, "Series A", 30, date(2025, 1, 1))
    session.add_all(
        [
            CompanyNewsRow(
                company_id=company.id,
                url="https://n.example/1",
                title="Old",
                published=date(2025, 1, 1),
            ),
            CompanyNewsRow(
                company_id=company.id,
                url="https://n.example/2",
                title="New",
                news_kind="funding",
                published=date(2026, 1, 1),
            ),
        ]
    )
    session.commit()

    profile = queries.get_profile(session, company.id)

    assert [r.stage for r in profile.funding_rounds] == ["Series A", "Seed"]
    assert profile.latest_round.stage == "Series A"
    assert [n.title for n in profile.news] == ["New", "Old"]
    assert [n.news_kind is not None for n in profile.news] == [True, False]


def test_get_profile_for_a_company_with_nothing_researched(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))

    profile = queries.get_profile(session, company.id)

    assert profile.facts == {} and profile.funding_rounds == []
    assert profile.latest_round is None and profile.news == []


def test_search_news_matches_title_or_summary_newest_first(session):
    company = Company(company_number="1", name="ACME")
    session.add(company)
    session.commit()
    session.add_all(
        [
            CompanyNewsRow(
                company_id=company.id,
                url="https://a.example/1",
                title="Seed round",
                published=date(2026, 1, 1),
            ),
            CompanyNewsRow(
                company_id=company.id,
                url="https://a.example/2",
                title="Other",
                summary="a seed deal",
                published=date(2026, 2, 1),
            ),
            CompanyNewsRow(
                company_id=company.id,
                url="https://a.example/3",
                title="Unrelated",
                published=date(2026, 3, 1),
            ),
        ]
    )
    session.commit()
    found = queries.search_news(session, text="seed")
    assert [(n.title, name) for n, name in found] == [("Other", "ACME"), ("Seed round", "ACME")]
    assert len(queries.search_news(session)) == 3


def test_companies_house_url():
    assert queries.companies_house_url("123").endswith("/company/123")
    assert queries.companies_house_url(None) is None
