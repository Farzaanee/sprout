import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from sqlmodel import Session
from streamlit.testing.v1 import AppTest

from sprout.candidates import assess
from sprout.db import (
    CompanyNewsRow,
    RunLog,
    RunLogStep,
    get_engine,
    init_db,
    save_fact,
    upsert_company,
)
from sprout.models import Sourced
from sprout.sources.companies_house import CompanyHit

ENTRY_POINT = Path(__file__).parents[1] / "streamlit_app.py"
TABS = ["Overview", "Companies", "News feed", "Candidate pool", "Progress", "Observability"]


def _app(monkeypatch, tmp_path) -> AppTest:
    monkeypatch.setenv("SPROUT_DB_PATH", str(tmp_path / "app.db"))
    return AppTest.from_file(str(ENTRY_POINT), default_timeout=30)


def test_dashboard_imports_no_spend_path():
    code = (
        "import sys, sprout.app.dashboard\n"
        "roots = ('sprout.ingest', 'sprout.hello', 'sprout.agents', 'anthropic')\n"
        "print([m for m in sys.modules if any(m == r or m.startswith(r + '.') for r in roots)])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


def test_renders_with_empty_database_read_only(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == TABS
    assert any("database is empty" in i.value for i in at.info)


def test_shows_counts_read_only(monkeypatch, tmp_path):
    engine = get_engine(tmp_path / "app.db")
    init_db(engine)
    hit = CompanyHit("1", "Neural Robotics Ltd", "active", date(2020, 1, 1), ("62012",), "London")
    with Session(engine) as session:
        upsert_company(session, hit, assess(hit.name, hit.sic_codes, incorporated=hit.incorporated))
        session.commit()

    at = _app(monkeypatch, tmp_path).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == TABS
    assert not at.button
    assert {m.label: m.value for m in at.metric}["Candidate companies"] == "1"


def test_candidate_pool_explains_discovery_and_enrichment_screens(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path).run()

    captions = "\n".join(c.value for c in at.caption)
    assert "discovery heuristic" in captions
    assert all(
        term in captions
        for term in ("artificial intelligence", "machine learning", "robotics", "AI", "analytics")
    )
    assert "tech SIC code" in captions
    assert "official website" in captions
    assert "profile enrichment runs only after that check succeeds" in captions


def test_candidate_pool_metrics_count_enriched_subset(monkeypatch, tmp_path):
    engine = get_engine(tmp_path / "app.db")
    init_db(engine)
    with Session(engine) as session:
        for number, name, category in (
            ("1", "Neural Robotics Ltd", "Other"),
            ("2", "Plain AI Ltd", None),
        ):
            hit = CompanyHit(number, name, "active", date(2020, 1, 1), ("62012",), "London")
            company, _ = upsert_company(
                session, hit, assess(hit.name, hit.sic_codes, incorporated=hit.incorporated)
            )
            company.category = category
            if category:
                session.add(
                    RunLog(
                        kind="company_website_screen",
                        company_id=company.id,
                        started_at=datetime.now(UTC),
                        status="ok",
                        stats={"website": "https://example.com"},
                    )
                )
                session.add(
                    RunLog(
                        kind="company_enrich",
                        company_id=company.id,
                        started_at=datetime.now(UTC),
                        status="ok",
                    )
                )
            save_fact(
                session,
                company.id,
                "hq",
                Sourced(value="London", source_url="https://example.com"),
            )
            session.add(
                CompanyNewsRow(
                    company_id=company.id, url=f"https://example.com/{number}", title="News"
                )
            )
        session.commit()

    at = _app(monkeypatch, tmp_path).run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Facts (candidate pool)"] == "2"
    assert metrics["Facts (enriched)"] == "1"
    assert metrics["News items (enriched)"] == "1"
    assert "Enrichment status (pool)" in _subheaders(at)
    assert "Website search outcome (pool)" in _subheaders(at)
    assert "Candidate score distribution (pool)" in _subheaders(at)


def test_candidate_table_follows_the_sidebar_filters(monkeypatch, tmp_path):
    engine = get_engine(tmp_path / "app.db")
    init_db(engine)
    with Session(engine) as session:
        for number, name in (("1", "Neural Robotics Ltd"), ("2", "Plain AI Ltd")):
            hit = CompanyHit(number, name, "active", date(2020, 1, 1), ("62012",), "London")
            upsert_company(
                session, hit, assess(hit.name, hit.sic_codes, incorporated=hit.incorporated)
            )
        session.commit()

    at = _app(monkeypatch, tmp_path).run()
    assert "2 companies match" in " ".join(c.value for c in at.caption)

    at.sidebar.text_input[0].set_value("neural").run()
    captions = " ".join(c.value for c in at.caption)
    assert "1 companies match the sidebar filters" in captions


def test_progress_and_observability_follow_the_sidebar_filters(monkeypatch, tmp_path):
    engine = get_engine(tmp_path / "app.db")
    init_db(engine)
    with Session(engine) as session:
        for number, name in (("1", "Neural Robotics Ltd"), ("2", "Plain AI Ltd")):
            hit = CompanyHit(number, name, "active", date(2020, 1, 1), ("62012",), "London")
            company, _ = upsert_company(
                session, hit, assess(hit.name, hit.sic_codes, incorporated=hit.incorporated)
            )
            session.add(
                RunLog(
                    kind="company_enrich",
                    company_id=company.id,
                    started_at=datetime.now(UTC),
                    status="error",
                    failure_category="refusal",
                )
            )
        session.commit()

    at = _app(monkeypatch, tmp_path).run()
    metrics = {m.label: m.value for m in at.metric}
    assert (metrics["Candidate companies"], metrics["Logged runs"], metrics["Runs"]) == (
        "2",
        "2",
        "2",
    )

    at.sidebar.text_input[0].set_value("neural").run()
    metrics = {m.label: m.value for m in at.metric}
    assert (metrics["Candidate companies"], metrics["Logged runs"], metrics["Runs"]) == (
        "1",
        "1",
        "1",
    )

    at.sidebar.text_input[0].set_value("zzz").run()
    assert "No pipeline runs for the companies matching the sidebar filters." in [
        i.value for i in at.info
    ]


def test_observability_limits_runs_to_the_sidebar_run_dates(monkeypatch, tmp_path):
    engine = get_engine(tmp_path / "app.db")
    init_db(engine)
    with Session(engine) as session:
        hit = CompanyHit("1", "Neural Robotics Ltd", "active", date(2020, 1, 1), ("62012",), "L")
        company, _ = upsert_company(
            session, hit, assess(hit.name, hit.sic_codes, incorporated=hit.incorporated)
        )
        for day in (1, 20):
            session.add(
                RunLog(
                    kind="company_enrich",
                    company_id=company.id,
                    started_at=datetime(2026, 11, day, 9, tzinfo=UTC),
                    status="ok",
                )
            )
        session.commit()

    at = _app(monkeypatch, tmp_path).run()
    assert {m.label: m.value for m in at.metric}["Runs"] == "2"

    day = date(2026, 11, 20)
    at.sidebar.date_input[0].set_value((day, day)).run()
    metrics = {m.label: m.value for m in at.metric}
    assert (metrics["Runs"], metrics["Logged runs"]) == ("1", "1")


HOSTILE = (
    "![x](https://evil.example/p.png) <script>alert(1)</script> **bold** [l](https://evil.example)"
)
STATS = {"stop": "submitted", "cost_usd": 0.05, "web_searches": 1}


def _seed(
    tmp_path,
    *,
    category=None,
    stats=None,
    steps=0,
    error_message=None,
    status=None,
    iterations=2,
    model="m",
):
    stats = STATS if stats is None else stats
    engine = get_engine(tmp_path / "app.db")
    init_db(engine)
    with Session(engine) as session:
        run = RunLog(
            kind="company_enrich",
            started_at=datetime(2026, 10, 20, 12, tzinfo=UTC),
            status=status or ("error" if category else "ok"),
            failure_category=category,
            stats=stats,
            iterations=iterations,
            model=model,
            error_message=error_message,
        )
        session.add(run)
        session.commit()
        for i in range(1, steps + 1):
            session.add(
                RunLogStep(
                    run_log_id=run.id,
                    iteration=i,
                    created_at=datetime.now(UTC),
                    tool_name="web_search,fetch_page",
                    response_text=HOSTILE,
                )
            )
        session.commit()


def _observability(monkeypatch, tmp_path) -> AppTest:
    at = _app(monkeypatch, tmp_path).run()
    assert not at.exception
    return at


def _subheaders(at) -> list[str]:
    return [s.value for s in at.subheader]


def test_observability_empty_points_at_the_pipeline_commands(monkeypatch, tmp_path):
    at = _observability(monkeypatch, tmp_path)
    assert any("uv run python -m sprout.orchestrator" in i.value for i in at.info)
    assert "Failures" not in _subheaders(at)
    assert "Runs" not in {m.label for m in at.metric}


def test_observability_populated_with_failed_run(monkeypatch, tmp_path):
    _seed(tmp_path, category="invalid_output", steps=2, error_message="validation failed")
    at = _observability(monkeypatch, tmp_path)
    assert {m.label: m.value for m in at.metric}["Failed"] == "1"
    assert {"Failures", "Trends", "Runs", "Run detail"} <= set(_subheaders(at))
    assert [s.value for s in at.success] == [
        "No tool calls in this range",
        "No failed runs with tool calls",
    ]
    # cost per day, cost by kind, category, rate by kind, cost trend, failures per day (no tool
    # calls seeded)
    assert len(at.get("vega_lite_chart")) == 6
    assert [e.label for e in at.expander] == ["Step 1 response", "Step 2 response"]
    assert "validation failed" in [c.value for c in at.code]


def test_observability_no_failed_runs_omits_category_chart(monkeypatch, tmp_path):
    _seed(tmp_path, steps=1)
    at = _observability(monkeypatch, tmp_path)
    assert [s.value for s in at.success] == [
        "No tool calls in this range",
        "No failed runs",
        "No failed runs with tool calls",
    ]
    # cost per day, cost by kind, rate by kind, cost trend, failures per day; no category or
    # tool-call chart
    assert len(at.get("vega_lite_chart")) == 5


def test_observability_run_without_steps_that_ran_is_pre_phase_3(monkeypatch, tmp_path):
    _seed(tmp_path)
    at = _observability(monkeypatch, tmp_path)
    at.toggle[0].set_value(False).run()
    assert any("predates step logging (Phase 3), run-level stats only" in i.value for i in at.info)
    assert not at.expander


def test_observability_run_without_steps_or_response_shows_error(monkeypatch, tmp_path):
    stats = {"stop": "error", "cost_usd": 0.0}
    _seed(
        tmp_path,
        category="api_error",
        stats=stats,
        error_message="RateLimitError: slow down",
        iterations=0,
    )
    at = _observability(monkeypatch, tmp_path)
    assert any("No model response was received" in i.value for i in at.info)
    assert "RateLimitError: slow down" in [c.value for c in at.code]


def test_observability_tolerates_run_with_empty_stats(monkeypatch, tmp_path):
    _seed(tmp_path, stats={}, status="running", iterations=None, model=None)
    at = _observability(monkeypatch, tmp_path)
    at.toggle[0].set_value(False).run()
    assert {m.label: m.value for m in at.metric}["Runs"] == "1"
    assert any("No model response was received" in i.value for i in at.info)


def test_observability_renders_logged_text_only_in_code(monkeypatch, tmp_path):
    _seed(tmp_path, category="invalid_output", steps=1, error_message=HOSTILE)
    at = _observability(monkeypatch, tmp_path)
    assert HOSTILE in [c.value for c in at.code]
    rendered = " ".join(e.value for e in [*at.markdown, *at.caption, *at.info, *at.success])
    assert "evil.example" not in rendered
    assert not [e for e in at.expander if "evil" in e.label]


def test_progress_shows_a_message_when_the_docs_are_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("sprout.app.dashboard.DOCS_DIR", tmp_path / "no-docs")
    at = _app(monkeypatch, tmp_path).run()
    assert not at.exception
    assert any("needs the project docs" in i.value for i in at.info)
    assert {m.label for m in at.metric} >= {"Candidate companies"}
