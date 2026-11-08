from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from evals.golden import GoldenEntry
from evals.metrics import (
    Observed,
    ObservedRound,
    Ratio,
    aggregate,
    amount_supported,
    business_status_correct,
    category_correct,
    check_claim,
    fetched_page_text,
    funding_claims,
    funding_match,
    last_round,
    parse_amounts_million,
    read_observed,
    score_all,
    score_entry,
    stage_correct,
    unexpected_disclosure,
)
from sprout.candidates import assess
from sprout.db import (
    RunKind,
    init_db,
    logged_run,
    save_fact,
    save_profile,
    upsert_company,
)
from sprout.models import CompanyProfile, FundingRound, Sourced, Stage
from sprout.sources.companies_house import CompanyHit
from sprout.sources.http import FetchError

NUMBER = "00000001"
URL = "https://example.com/news"
PAGE = "Acme raised US$12 million in a Series A led by Foo."


@pytest.fixture
def session():
    # An in-memory SQLite database is per connection, so pin one connection.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    with Session(engine) as s:
        yield s


def golden(**overrides) -> GoldenEntry:
    data = {
        "company_number": NUMBER,
        "name": "Acme AI",
        "category": "Cybersecurity",
        "one_liner": "Acme.",
        "status": "Active",
        "stage": "Series A",
        "stage_sources": [URL],
        "last_round": {
            "stage": "Series A",
            "amount_million_usd": 12,
            "sources": [URL],
        },
        "business_model": "SaaS.",
        "target_user": "Banks.",
        "split": "dev",
        "label": {"date": date(2026, 11, 5), "verified": True},
    }
    data.update(overrides)
    return GoldenEntry.model_validate(data)


def sourced(value, url=URL, note=None):
    return Sourced(value=value, source_url=url, note=note)


def round_(stage=Stage.SERIES_A, amount=12, announced=None, url=URL, note=None):
    return Sourced(
        value=FundingRound(stage=stage, amount_million_usd=amount, announced=announced),
        source_url=url,
        note=note,
    )


def seed(
    session,
    *,
    profile: CompanyProfile | None = None,
    stop="submitted",
    failure=None,
    repairs=0,
    cost=0.05,
    sources=((URL, True),),
    number=NUMBER,
):
    hit = CompanyHit(number, "Acme AI Ltd", "active", date(2020, 1, 1), ("62012",), "London")
    company, _ = upsert_company(session, hit, assess("Acme AI Ltd", ["62012"]))
    if profile is not None:
        save_profile(session, company, profile)
    stats = {"stop": stop, "cost_usd": cost}
    if sources is not None:
        stats["sources"] = [{"url": u, "fetched": f} for u, f in sources]
    with logged_run(session, RunKind.COMPANY_ENRICH.value) as run:
        run.stats = stats
        run.company_id = company.id
        run.failure_category = failure
        run.repairs = repairs
    session.commit()
    return company


def profile(**kwargs) -> CompanyProfile:
    base = {
        "name": "Acme AI",
        "category": "Cybersecurity",
        "stage": sourced(Stage.SERIES_A),
        "funding_rounds": [round_()],
    }
    base.update(kwargs)
    return CompanyProfile(**base)


def no_pages(url):
    return None


def pages(text=PAGE):
    return lambda url: text


# ---- parse_amounts_million ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("raised $5m today", ["5"]),
        ("US$12 million", ["12"]),
        ("GBP 7.5mn", ["7.5"]),
        ("£300 million and €1.2bn", ["300", "1200"]),
        ("a $1,250.5 million round", ["1250.5"]),
        ("valued at 2 billion", ["2000"]),
        ("US$3B", ["3000"]),
        ("raised $12,500,000 in total", ["12.5"]),
        ("5M", ["5"]),
        ("$5m then $10m", ["5", "10"]),
    ],
)
def test_parse_amounts(text, expected):
    assert parse_amounts_million(text) == [Decimal(e) for e in expected]


@pytest.mark.parametrize(
    "text", ["", "5 months ago", "founded in 2019", "Series B", "100 employees"]
)
def test_parse_amounts_ignores_non_money(text):
    assert parse_amounts_million(text) == []


def test_amount_supported_uses_note_and_tolerance():
    assert amount_supported(12, None, "US$12 million")
    assert amount_supported(12, None, "US$12.1m")  # within 1 percent
    assert not amount_supported(12, None, "US$13m")
    # stored value is a conversion; the page quotes the original currency
    assert amount_supported(15, "GBP 12m converted at 1.25", "raised £12 million")
    assert not amount_supported(15, None, "raised £12 million")
    assert not amount_supported(12, None, "no figures here")


# ---- last round (D22) -----------------------------------------------------------------------


def obs_round(stage="Seed", amount=1, announced=None):
    return ObservedRound(stage, amount, announced, URL, None)


def test_last_round_by_latest_date():
    older = obs_round("Series B", 50, date(2024, 1, 1))
    newer = obs_round("Seed", 5, date(2025, 1, 1))
    assert last_round([older, newer]) is newer


def test_last_round_tie_breaks_on_stage_order():
    seed_ = obs_round("Seed", 5, date(2025, 1, 1))
    a = obs_round("Series A", 20, date(2025, 1, 1))
    assert last_round([a, seed_]) is a
    assert last_round([seed_, a]) is a


def test_last_round_without_dates_uses_stage_and_dated_beats_undated():
    assert last_round([obs_round("Seed"), obs_round("Series B")]).stage == "Series B"
    dated = obs_round("Seed", 5, date(2020, 1, 1))
    assert last_round([obs_round("Series C"), dated]) is dated


def test_last_round_unknown_stage_ranks_lowest_and_null_amount_counts():
    unknown = obs_round("Unknown", None)
    assert last_round([unknown, obs_round("Pre-seed", 1)]).stage == "Pre-seed"
    latest = obs_round("Series A", None, date(2025, 6, 1))
    assert last_round([obs_round("Seed", 5, date(2024, 1, 1)), latest]) is latest
    assert last_round([]) is None


# ---- read_observed --------------------------------------------------------------------------


def test_read_observed_reads_company_facts_rounds_and_run(session):
    seed(session, profile=profile(), repairs=2, cost=0.0421, failure=None)
    observed = read_observed(session, golden())
    assert observed.has_run and observed.stop == "submitted"
    assert observed.repairs == 2
    assert observed.cost_usd == Decimal("0.0421")
    assert observed.category == "Cybersecurity"
    assert observed.value("stage") == "Series A"
    assert observed.facts["stage"].source_url == URL
    assert [(r.stage, r.amount_million_usd) for r in observed.rounds] == [("Series A", 12)]
    assert observed.sources == ((URL, True),)


def test_read_observed_uses_latest_run(session):
    company = seed(session, profile=profile(), stop="submitted")
    with logged_run(session, RunKind.COMPANY_ENRICH.value) as run:
        run.stats = {"stop": "budget", "cost_usd": 0.2}
        run.company_id = company.id
        run.failure_category = "budget_cap"
    session.commit()
    observed = read_observed(session, golden())
    assert observed.stop == "budget" and observed.failure_category == "budget_cap"


def test_read_observed_missing_company_and_missing_sources(session):
    assert read_observed(session, golden()) == Observed()
    seed(session, profile=None, stop="error", failure="api_error", sources=None)
    observed = read_observed(session, golden())
    assert observed.sources is None and observed.category is None and observed.rounds == ()


def test_read_observed_winning_fact_beats_null_fact(session):
    company = seed(session, profile=profile(stage=Sourced[Stage]()))
    save_fact(session, company.id, "stage", sourced(Stage.SEED, url="https://example.com/b"))
    session.commit()
    assert read_observed(session, golden()).value("stage") == "Seed"


# ---- category and stage ---------------------------------------------------------------------


def test_category_accuracy_single_and_list():
    obs = Observed(category="Cybersecurity")
    assert category_correct(obs, golden())
    assert not category_correct(Observed(category="Other"), golden())
    both = golden(category=["Cybersecurity", "Other"])
    assert category_correct(Observed(category="Other"), both)
    assert not category_correct(Observed(category=None), golden())


def test_business_status_correct():
    from evals.metrics import ObservedFact

    matching = Observed(
        facts={"business_status": ObservedFact("business_status", "Active", URL, None)}
    )
    mismatching = Observed(
        facts={"business_status": ObservedFact("business_status", "Closed", URL, None)}
    )
    assert business_status_correct(matching, golden())
    assert not business_status_correct(mismatching, golden())
    assert not business_status_correct(Observed(), golden())


def test_stage_correct_unknown_matches_null_or_unknown():
    unknown = golden(stage="Unknown", last_round=None)
    assert stage_correct(Observed(), unknown)
    assert stage_correct(_with_stage("Unknown"), unknown)
    assert not stage_correct(_with_stage("Seed"), unknown)
    assert stage_correct(_with_stage("Series A"), golden())
    assert not stage_correct(Observed(), golden())
    assert not stage_correct(_with_stage("Seed"), golden())


def _with_stage(value):
    from evals.metrics import ObservedFact

    return Observed(facts={"stage": ObservedFact("stage", value, URL, None)})


# ---- funding --------------------------------------------------------------------------------


def _rounds(*amounts):
    return Observed(rounds=tuple(obs_round("Series A", a, date(2025, 1, 1)) for a in amounts))


def test_funding_exact_within_and_miss():
    assert funding_match(_rounds(12), golden()) == (True, True)
    assert funding_match(_rounds(13), golden()) == (False, True)  # 8.3 percent off
    assert funding_match(_rounds(14), golden()) == (False, False)
    assert funding_match(_rounds(None), golden()) == (False, False)
    assert funding_match(Observed(), golden()) == (False, False)


def test_funding_within_boundary_is_inclusive():
    gold = golden(last_round={"stage": "Series A", "amount_million_usd": 100, "sources": [URL]})
    assert funding_match(_rounds(110), gold) == (False, True)
    assert funding_match(_rounds(111), gold) == (False, False)


def test_funding_not_applicable_without_golden_round_and_null_gold_amount():
    no_round = golden(stage="Unknown", last_round=None)
    assert funding_match(_rounds(12), no_round) is None
    undisclosed = golden(
        last_round={"stage": "Series A", "amount_million_usd": None, "sources": [URL]}
    )
    assert funding_match(_rounds(None), undisclosed) == (True, True)
    assert funding_match(_rounds(5), undisclosed) == (False, False)


def test_funding_uses_last_round_not_largest():
    obs = Observed(
        rounds=(
            obs_round("Series B", 100, date(2023, 1, 1)),
            obs_round("Series A", 12, date(2025, 1, 1)),
        )
    )
    assert funding_match(obs, golden()) == (True, True)


def test_unexpected_disclosure():
    no_round = golden(stage="Unknown", last_round=None)
    assert unexpected_disclosure(_rounds(5), no_round) is True
    assert unexpected_disclosure(_rounds(None), no_round) is False
    assert unexpected_disclosure(Observed(), no_round) is False
    assert unexpected_disclosure(_rounds(5), golden()) is None


# ---- fabricated / unverifiable / supported --------------------------------------------------


def test_claims_cover_totals_valuation_and_rounds(session):
    seed(
        session,
        profile=profile(
            total_raised_million_usd=sourced(30),
            valuation_million_usd=sourced(None, url=None),
            funding_rounds=[round_(amount=12), round_(Stage.SEED, None)],
        ),
    )
    claims = funding_claims(read_observed(session, golden()))
    assert sorted((c.label, c.amount_million_usd) for c in claims) == [
        ("round:Series A", 12),
        ("total_raised_million_usd", 30),
    ]


@pytest.mark.parametrize(
    ("page", "sources", "expected"),
    [
        (PAGE, ((URL, True),), "supported"),
        ("Acme raised US$99 million.", ((URL, True),), "fabricated"),
        (PAGE, ((URL, False),), "unverifiable"),  # snippet only, never read
        (None, ((URL, True),), "unverifiable"),  # text no longer available
        (PAGE, (), "unverifiable"),  # URL not in the ledger
        (PAGE, None, "unverifiable"),  # run predates stats["sources"]
    ],
)
def test_check_claim(session, page, sources, expected):
    seed(session, profile=profile(), sources=sources)
    observed = read_observed(session, golden())
    entry = score_entry(observed, golden(), lambda url: page)
    assert [result for _, result in entry.claims] == [expected]


def test_check_claim_without_source_url_is_unverifiable():
    from evals.metrics import FundingClaim, rebuild_ledger

    claim = FundingClaim("round:Seed", 5, None, None)
    assert check_claim(claim, rebuild_ledger([(URL, True)]), pages()) == "unverifiable"


def test_supported_via_note_amount(session):
    seed(session, profile=profile(funding_rounds=[round_(amount=15, note="GBP 12m at 1.25")]))
    entry = score_entry(read_observed(session, golden()), golden(), pages("raised £12 million"))
    assert [r for _, r in entry.claims] == ["supported"]


def test_page_text_is_read_once_per_url(session):
    seed(
        session,
        profile=profile(total_raised_million_usd=sourced(12), funding_rounds=[round_()]),
    )
    calls = []

    def page_text(url):
        calls.append(url)
        return PAGE

    score_entry(read_observed(session, golden()), golden(), page_text)
    assert calls == [URL]


# ---- ledger membership ----------------------------------------------------------------------


def test_ledger_membership_uses_production_normalisation(session):
    seed(session, profile=profile(), sources=(("https://Example.com/news/", True),))
    entry = score_entry(read_observed(session, golden()), golden(), no_pages)
    assert entry.ledger_cited == entry.ledger_hits > 0  # trailing slash and host case ignored


def test_ledger_miss_and_missing_list(session):
    other = "https://other.example.org/x"
    seed(session, profile=profile(), sources=((other, True),))
    entry = score_entry(read_observed(session, golden()), golden(), no_pages)
    assert entry.ledger_hits == 0 and entry.ledger_cited > 0 and entry.ledger_unverifiable == 0


def test_ledger_missing_list_is_unverifiable_not_a_miss(session):
    seed(session, profile=profile(), sources=None)
    entry = score_entry(read_observed(session, golden()), golden(), no_pages)
    assert entry.ledger_cited == 0 and entry.ledger_unverifiable > 0


# ---- aggregate ------------------------------------------------------------------------------


def entry_pair(session, number, *, gold=None, **seed_kwargs):
    seed(session, number=number, **seed_kwargs)
    entry = gold or golden(company_number=number)
    return entry, read_observed(session, entry)


def test_aggregate_denominators_and_counts(session):
    pairs = [
        # submitted, right category, right funding, supported
        entry_pair(session, "00000001", profile=profile(), repairs=1, cost=0.02),
        # submitted, wrong category, wrong stage, fabricated amount
        entry_pair(
            session,
            "00000002",
            profile=profile(
                category="Other",
                stage=sourced(Stage.SEED),
                funding_rounds=[round_(Stage.SEED, 99)],
            ),
            cost=0.06,
        ),
        # infrastructure error: counted in errors, excluded from the submitted denominator
        entry_pair(session, "00000003", stop="error", failure="api_error", sources=None, cost=0),
        # budget failure: not submitted, in the denominator
        entry_pair(session, "00000004", stop="budget", failure="budget_cap", cost=0.1),
        # golden has no round; the agent reported one
        entry_pair(
            session,
            "00000005",
            profile=profile(),
            gold=golden(company_number="00000005", stage="Unknown", last_round=None),
        ),
    ]
    result = score_all(pairs, pages())
    r = result.ratios
    assert result.scored == 5
    assert r["profiles_submitted"] == Ratio(3, 4)
    assert r["infrastructure_errors"] == Ratio(1, 5)
    assert r["repair_rate"] == Ratio(1, 5)
    assert r["category_accuracy"] == Ratio(2, 3)
    assert r["business_status_accuracy"] == Ratio(0, 3)  # no profile sets business_status
    assert r["stage_accuracy"] == Ratio(1, 3)  # entry 5 expects Unknown, got Series A
    assert r["funding_exact"] == Ratio(1, 2)  # only entries 1 and 2 have a golden round
    assert r["funding_within_10pct"] == Ratio(1, 2)
    assert r["unexpected_disclosure"] == Ratio(1, 1)
    assert r["fabricated_funding"] == Ratio(1, 3)
    assert r["unverifiable_funding"] == Ratio(0, 3)
    assert r["source_ledger"].num == r["source_ledger"].den > 0
    assert result.cost_mean == Decimal("0.046")  # (0.02 + 0.06 + 0 + 0.1 + 0.05) / 5
    assert result.cost_max == Decimal("0.1")


def test_aggregate_no_runs_and_empty():
    empty = aggregate([])
    assert empty.scored == 0 and empty.cost_mean is None and empty.cost_max is None
    assert all(r.den == 0 and r.percent is None for r in empty.ratios.values())
    assert str(empty.ratios["category_accuracy"]) == "0/0 (n/a)"

    only_errors = score_all([(golden(), Observed())], no_pages)
    assert only_errors.ratios["profiles_submitted"] == Ratio(0, 1)
    assert only_errors.ratios["category_accuracy"] == Ratio(0, 0)
    assert only_errors.ratios["fabricated_funding"] == Ratio(0, 0)


def test_all_api_errors_leave_no_submitted_denominator(session):
    pair = entry_pair(session, NUMBER, stop="error", failure="api_error", sources=None)
    result = score_all([pair], no_pages)
    assert result.ratios["profiles_submitted"] == Ratio(0, 0)
    assert result.ratios["infrastructure_errors"] == Ratio(1, 1)


def test_thin_other_counts_submitted_other_with_few_facts(session):
    thin = profile(category="Other", funding_rounds=[], stage=Sourced[Stage]())
    rich = profile(
        category="Other",
        stage=sourced(Stage.SEED),
        website=sourced("https://a.example"),
        hq=sourced("London"),
        founded=sourced(2020),
    )
    pairs = [
        entry_pair(session, "00000001", profile=thin),
        entry_pair(session, "00000002", profile=rich),
    ]
    assert score_all(pairs, no_pages).ratios["thin_other"] == Ratio(1, 2)


def test_counts_are_json_ready(session):
    import json

    result = score_all([entry_pair(session, NUMBER, profile=profile())], pages())
    counts = json.loads(json.dumps(result.counts()))
    assert counts["category_accuracy"] == [1, 1]
    assert counts["scored"] == 1 and counts["cost_max_usd"] == "0.05"


def test_ratio_formatting():
    assert str(Ratio(28, 32)) == "28/32 (87.5%)"


# ---- real page text helper ------------------------------------------------------------------


def test_fetched_page_text_returns_model_view_and_none_on_failure():
    from datetime import UTC, datetime

    from sprout.sources.web import Page

    class Fetcher:
        def fetch(self, url):
            if "bad" in url:
                raise FetchError("boom")
            return Page(url, "Title", "body text", datetime.now(UTC), False)

    text = fetched_page_text(Fetcher())
    assert text("https://a.example/") == "Title: Title\nbody text"
    assert text("https://bad.example/") is None
