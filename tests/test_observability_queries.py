from datetime import UTC, date, datetime

import pytest
from sqlmodel import Session

from sprout.app import observability_queries as obs
from sprout.candidates import assess
from sprout.db import RunLog, RunLogStep, get_engine, init_db, upsert_company
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
## Phase 0 - Setup

**Status: done except two items that need your credentials**

- [x] one
- [ ] two

## Phase 1 - Data

**Status: done.** Verified.

- [x] a
- [x] b

## Phase 2 - Agent

**Status: in progress.**

- [ ] a
"""


def test_parse_phases_states_and_criteria():
    phases = {p.number: p for p in obs.parse_phases(PLAN, NOTES)}
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
    assert {p.state for p in obs.parse_phases(PLAN, "")} == {"Not started"}


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


def _run(
    session,
    day,
    *,
    kind="company_enrich",
    category=None,
    status=None,
    stats=None,
    company_id=None,
    model=None,
    iterations=None,
    error_message=None,
):
    run = RunLog(
        kind=kind,
        started_at=datetime(2026, 10, day, 12, tzinfo=UTC),
        status=status or ("error" if category else "ok"),
        failure_category=category,
        stats={} if stats is None else stats,
        company_id=company_id,
        model=model,
        iterations=iterations,
        error_message=error_message,
    )
    session.add(run)
    session.commit()
    return run


def _step(session, run, iteration):
    session.add(RunLogStep(run_log_id=run.id, iteration=iteration, created_at=datetime.now(UTC)))
    session.commit()


@pytest.fixture
def runs(session):
    company = _add(session, "1", "Neural Robotics Ltd", date(2020, 1, 1))
    common = {"company_id": company.id, "model": "m", "iterations": 4}
    ok_stats = {"stop": "submitted", "cost_usd": 0.08}
    fail_stats = {"stop": "invalid", "cost_usd": 0.08, "web_searches": 2}
    return [
        _run(session, 1, stats=ok_stats, **common),
        _run(
            session, 1, category="invalid_output", stats=fail_stats, error_message="bad", **common
        ),
        _run(
            session,
            2,
            category="budget_cap",
            stats={"stop": "invalid", "cost_usd": 0.0},
            **{**common, "iterations": 0},
        ),  # fmt: skip
        _run(session, 2, category="invalid_output", stats=fail_stats, **common),
        _run(session, 3, category="refusal", status="running"),  # crashed before stats
        _run(session, 3, kind="companies_ingest", category="api_error", stats={"cost_usd": 9}),
    ]


def test_failure_counts_groups_failed_enrich_runs_largest_first(session, runs):
    counts = obs.failure_counts(session)
    assert counts == {"invalid_output": 2, "budget_cap": 1, "refusal": 1}
    assert list(counts) == ["invalid_output", "budget_cap", "refusal"]  # ties in enum order


def test_failure_counts_puts_unknown_categories_last_and_handles_empty(session):
    assert obs.failure_counts(session) == {}
    _run(session, 1, category="zzz_unknown")
    _run(session, 1, category="api_error")
    assert list(obs.failure_counts(session)) == ["api_error", "zzz_unknown"]


def test_enrich_runs_newest_first_with_company_and_steps(session, runs):
    _step(session, runs[1], 1)
    _step(session, runs[1], 2)
    result = obs.enrich_runs(session)
    assert [r.run_id for r in result] == [r.id for r in reversed(runs[:5])]  # ingest excluded
    failed = next(r for r in result if r.run_id == runs[1].id)
    assert failed.company_name == "Neural Robotics Ltd"
    assert (failed.stop, failed.failure_category) == ("invalid", "invalid_output")
    assert (failed.iterations, failed.cost_usd, failed.web_searches) == (4, 0.08, 2)
    assert failed.steps_logged == 2 and failed.error_message == "bad" and failed.model == "m"
    assert next(r for r in result if r.run_id == runs[0].id).steps_logged == 0


def test_enrich_runs_filters_and_limit(session, runs):
    only_cat = obs.enrich_runs(session, failure_category="invalid_output")
    assert [r.run_id for r in only_cat] == [runs[3].id, runs[1].id]
    failures = obs.enrich_runs(session, failures_only=True)
    assert [r.run_id for r in failures] == [runs[4].id, runs[3].id, runs[2].id, runs[1].id]
    assert [r.run_id for r in obs.enrich_runs(session, limit=2)] == [runs[4].id, runs[3].id]


def test_enrich_runs_tolerates_empty_stats(session, runs):
    blank = next(r for r in obs.enrich_runs(session) if r.run_id == runs[4].id)
    assert blank.status == "running" and blank.stats == {}
    assert blank.company_id is None and blank.company_name is None and blank.stop is None
    assert (blank.iterations, blank.cost_usd, blank.web_searches) == (0, 0.0, 0)


def test_cost_trend_oldest_first_with_per_iteration_cost(session, runs):
    trend = obs.cost_trend(session)
    assert [p.run_id for p in trend] == [r.id for r in runs[:5]]
    assert trend[0].cost_per_iteration == pytest.approx(0.02) and not trend[0].failed
    assert trend[1].failed
    assert trend[2].iterations == 0 and trend[2].cost_per_iteration is None
    assert trend[4].cost_per_iteration is None and trend[4].cost_usd == 0.0  # stats == {}
    assert [p.run_id for p in obs.cost_trend(session, limit=2)] == [runs[3].id, runs[4].id]


def test_daily_outcomes_per_utc_day(session, runs):
    assert obs.daily_outcomes(session) == [
        obs.DayOutcome(date(2026, 10, 1), 2, 1, pytest.approx(0.16)),
        obs.DayOutcome(date(2026, 10, 2), 2, 2, pytest.approx(0.08)),
        obs.DayOutcome(date(2026, 10, 3), 1, 1, 0.0),
    ]


def test_legacy_gbp_cost_is_converted_to_usd(session):
    legacy = {"iterations": 2, "cost_gbp": 0.0076, "usd_to_gbp": 0.76}
    run = _run(session, 1, stats=legacy)
    _run(session, 1, stats={"cost_gbp": 0.5, "usd_to_gbp": 0})  # unusable rate
    assert obs.enrich_runs(session, limit=2)[1].cost_usd == pytest.approx(0.01)
    assert obs.cost_trend(session)[0].cost_usd == pytest.approx(0.01)
    assert obs.cost_trend(session)[0].run_id == run.id
    assert obs.daily_outcomes(session)[0].cost_usd == pytest.approx(0.01)


def test_run_steps_ordered_by_iteration_and_scoped_to_run(session, runs):
    for iteration in (3, 1, 2):
        _step(session, runs[0], iteration)
    _step(session, runs[1], 1)
    assert [s.iteration for s in obs.run_steps(session, runs[0].id)] == [1, 2, 3]
    assert obs.run_steps(session, runs[2].id) == []


def test_run_queries_return_empty_without_runs(session):
    assert obs.enrich_runs(session) == []
    assert obs.cost_trend(session) == []
    assert obs.daily_outcomes(session) == []
