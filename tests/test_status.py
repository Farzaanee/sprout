from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import Session

from sprout.db import (
    Company,
    CompanyNewsRow,
    Fact,
    FundingRoundRow,
    NewsItemRow,
    RunKind,
    RunLog,
    get_engine,
    init_db,
)
from sprout.status import (
    ABANDONED_MESSAGE,
    STEPS,
    StatusPolicy,
    _utc,
    company_statuses,
    profile_gaps,
    reap_abandoned,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
POLICY = StatusPolicy()
ENRICH = RunKind.COMPANY_ENRICH.value
CLASSIFY = RunKind.COMPANY_CLASSIFICATION.value
NEWS = RunKind.COMPANY_NEWS_MATCH.value
VERIFY = RunKind.COMPANY_VERIFICATION.value


@pytest.fixture
def session(tmp_path):
    engine = get_engine(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def company(session):
    row = Company(company_number="12345678", name="ACME AI LTD", incorporated=date(2019, 3, 4))
    session.add(row)
    session.commit()
    return row


def add_run(session, company, kind, *, days_ago=1.0, status="ok", category=None, cost=None):
    started = NOW - timedelta(days=days_ago)
    run = RunLog(
        kind=kind,
        company_id=company.id,
        started_at=started,
        finished_at=None if status == "running" else started + timedelta(minutes=1),
        status=status,
        failure_category=category,
        stats={} if cost is None else {"cost_usd": cost},
    )
    session.add(run)
    session.commit()
    return run


def step(session, company, name, policy=POLICY):
    return company_statuses(session, [company.id], policy, NOW)[company.id].steps[name]


def test_never_when_no_runs(session, company):
    status = company_statuses(session, [company.id], POLICY, NOW)[company.id]
    assert {s.state for s in status.steps.values()} == {"never"}
    assert status.needs() == list(STEPS)
    assert status.cost_usd == 0


def test_fresh_within_ttl(session, company):
    add_run(session, company, ENRICH, days_ago=29)
    add_run(session, company, NEWS, days_ago=6)
    assert step(session, company, "enrich").state == "fresh"
    assert step(session, company, "news").state == "fresh"


def test_stale_past_ttl(session, company):
    add_run(session, company, ENRICH, days_ago=31)
    add_run(session, company, NEWS, days_ago=8)
    assert step(session, company, "enrich").state == "stale"
    assert step(session, company, "news").state == "stale"


def test_naive_and_aware_datetimes_compare_as_utc(session, company):
    add_run(session, company, ENRICH, days_ago=1)
    got = step(session, company, "enrich")
    assert got.state == "fresh"
    assert got.last_ok_at is not None and got.last_ok_at.tzinfo is not None
    assert _utc(datetime(2026, 11, 7, 12, 0)) == datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    # a naive `now` is read as UTC too
    statuses = company_statuses(session, [company.id], POLICY, NOW.replace(tzinfo=None))
    assert statuses[company.id].steps["enrich"].state == "fresh"


def test_failed_below_cap(session, company):
    add_run(session, company, ENRICH, days_ago=3, category="invalid_output", status="error")
    add_run(session, company, ENRICH, days_ago=2, category="iteration_cap", status="error")
    got = step(session, company, "enrich")
    assert got.state == "failed"
    assert got.failed_attempts == 2
    assert got.last_failure_category == "iteration_cap"
    assert "enrich" in company_statuses(session, [company.id], POLICY, NOW)[company.id].needs()


def test_exhausted_at_cap(session, company):
    for days in (4, 3, 2):
        add_run(session, company, ENRICH, days_ago=days, category="invalid_output", status="error")
    status = company_statuses(session, [company.id], POLICY, NOW)[company.id]
    assert status.steps["enrich"].state == "exhausted"
    assert "enrich" not in status.needs()
    assert "enrich" in status.needs(force=True)


def test_running_younger_than_timeout(session, company):
    add_run(session, company, ENRICH, days_ago=0.01, status="running")  # about 14 minutes
    status = company_statuses(session, [company.id], POLICY, NOW)[company.id]
    assert status.steps["enrich"].state == "running"
    assert "enrich" not in status.needs()
    assert "enrich" not in status.needs(force=True)


def test_running_older_than_timeout_counts_as_failed_before_reaping(session, company):
    add_run(session, company, ENRICH, days_ago=1, status="running")
    got = step(session, company, "enrich")
    assert got.state == "failed"
    assert got.failed_attempts == 1
    assert got.last_failure_category == "internal_error"


def test_api_error_excluded_from_attempts(session, company):
    for days in (5, 4, 3, 2):
        add_run(session, company, ENRICH, days_ago=days, category="api_error", status="error")
    got = step(session, company, "enrich")
    assert got.state == "failed"  # still retried, never exhausted
    assert got.failed_attempts == 0
    assert got.last_failure_category == "api_error"


def test_failed_attempts_only_count_rows_after_newest_ok(session, company):
    add_run(session, company, ENRICH, days_ago=10, category="invalid_output", status="error")
    add_run(session, company, ENRICH, days_ago=9, category="invalid_output", status="error")
    add_run(session, company, ENRICH, days_ago=8)
    add_run(session, company, ENRICH, days_ago=2, category="invalid_output", status="error")
    got = step(session, company, "enrich")
    assert got.state == "failed"
    assert got.failed_attempts == 1
    assert got.last_ok_at is not None


def test_ok_after_failures_is_fresh(session, company):
    add_run(session, company, ENRICH, days_ago=3, category="invalid_output", status="error")
    add_run(session, company, ENRICH, days_ago=2)
    got = step(session, company, "enrich")
    assert got.state == "fresh"
    assert got.failed_attempts == 0


@pytest.mark.parametrize("kind,name", [(CLASSIFY, "classify"), (VERIFY, "verify")])
def test_classify_and_verify_freshness_follows_newest_ok_enrich(session, company, kind, name):
    add_run(session, company, ENRICH, days_ago=10)
    add_run(session, company, kind, days_ago=9)
    assert step(session, company, name).state == "fresh"
    # no TTL of their own: still fresh long after, as long as the enrichment did not change
    add_run(session, company, ENRICH, days_ago=0.5)  # re-enrich after the step ran
    assert step(session, company, name).state == "stale"
    add_run(session, company, kind, days_ago=0.4)
    assert step(session, company, name).state == "fresh"


def test_classify_without_ttl_stays_fresh_when_enrich_is_stale(session, company):
    add_run(session, company, ENRICH, days_ago=40)
    add_run(session, company, CLASSIFY, days_ago=39)
    status = company_statuses(session, [company.id], POLICY, NOW)[company.id]
    assert status.steps["enrich"].state == "stale"
    assert status.steps["classify"].state == "fresh"


def test_failed_enrich_attempt_does_not_make_classify_stale(session, company):
    add_run(session, company, ENRICH, days_ago=10)
    add_run(session, company, CLASSIFY, days_ago=9)
    add_run(session, company, ENRICH, days_ago=1, category="invalid_output", status="error")
    assert step(session, company, "classify").state == "fresh"


def test_cost_sums_stats_over_all_pipeline_rows(session, company):
    add_run(session, company, ENRICH, cost=0.12)
    add_run(session, company, CLASSIFY, cost=0.004)
    add_run(session, company, NEWS)  # no cost recorded
    other = Company(company_number="87654321", name="OTHER LTD")
    session.add(other)
    session.commit()
    add_run(session, other, ENRICH, cost=1.0)
    statuses = company_statuses(session, [company.id, other.id], POLICY, NOW)
    assert str(statuses[company.id].cost_usd) == "0.124"
    assert str(statuses[other.id].cost_usd) == "1.0"


def test_empty_ids(session):
    assert company_statuses(session, [], POLICY, NOW) == {}


def test_reaper_marks_old_running_rows_and_spares_young_and_batch(session, company):
    old = add_run(session, company, ENRICH, days_ago=1, status="running")
    young = add_run(session, company, CLASSIFY, days_ago=0.01, status="running")
    batch = RunLog(
        kind=RunKind.NEWS_INGEST.value, started_at=NOW - timedelta(days=2), status="running"
    )
    session.add(batch)
    session.commit()

    assert reap_abandoned(session, NOW, POLICY) == 1

    session.expire_all()
    reaped = session.get(RunLog, old.id)
    assert reaped.status == "error"
    assert reaped.failure_category == "internal_error"
    assert reaped.error_message == ABANDONED_MESSAGE
    assert reaped.finished_at is not None
    assert session.get(RunLog, young.id).status == "running"
    assert session.get(RunLog, batch.id).status == "running"
    assert reap_abandoned(session, NOW, POLICY) == 0
    assert step(session, company, "enrich").failed_attempts == 1


def test_profile_gaps(session, company):
    assert profile_gaps(session, company.id) == [
        "category",
        "stage",
        "funding",
        "business_model",
        "target_user",
        "news",
    ]

    def fact(field, value_json, source="https://a.example"):
        return Fact(
            company_id=company.id,
            field=field,
            value_json=value_json,
            source_url=source,
            retrieved_at=NOW,
            confidence="high",
        )

    company.category = "Other"
    session.add_all(
        [
            fact("stage", None),  # researched, nothing found: still a gap
            fact("business_model", '"B2B SaaS"'),
            FundingRoundRow(
                company_id=company.id, stage="Seed", retrieved_at=NOW, confidence="high"
            ),
        ]
    )
    session.commit()
    assert profile_gaps(session, company.id) == ["stage", "target_user", "news"]

    item = NewsItemRow(url="https://n.example/1", title="ACME raises")
    session.add_all(
        [fact("stage", '"Seed"', "https://b.example"), fact("target_user", '"SMEs"'), item]
    )
    session.commit()
    session.add(CompanyNewsRow(company_id=company.id, news_item_id=item.id))
    session.commit()
    assert profile_gaps(session, company.id) == []


def test_profile_gaps_unknown_company(session):
    with pytest.raises(ValueError):
        profile_gaps(session, 999)
