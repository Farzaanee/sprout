from datetime import UTC, date, datetime
from pathlib import Path

from sqlmodel import Session
from streamlit.testing.v1 import AppTest

from sprout.candidates import assess
from sprout.db import RunLog, RunLogStep, get_engine, init_db, upsert_company
from sprout.sources.companies_house import CompanyHit

ENTRY_POINT = Path(__file__).parents[1] / "streamlit_app.py"


def _app(monkeypatch, tmp_path, *, actions: bool) -> AppTest:
    monkeypatch.setenv("SPROUT_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("SPROUT_ENABLE_ACTIONS", "1" if actions else "0")
    return AppTest.from_file(str(ENTRY_POINT), default_timeout=30)


def test_renders_with_empty_database_read_only(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path, actions=False).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Progress", "Companies", "News", "Observability"]
    assert any("database is empty" in i.value for i in at.info)


def test_shows_counts_and_action_tab_when_enabled(monkeypatch, tmp_path):
    engine = get_engine(tmp_path / "app.db")
    init_db(engine)
    hit = CompanyHit("1", "Neural Robotics Ltd", "active", date(2020, 1, 1), ("62012",), "London")
    with Session(engine) as session:
        upsert_company(session, hit, assess(hit.name, hit.sic_codes, incorporated=hit.incorporated))
        session.commit()

    at = _app(monkeypatch, tmp_path, actions=True).run()
    assert not at.exception
    assert [t.label for t in at.tabs][-2:] == ["Observability", "Run & test"]
    assert any(b.label == "Both" for b in at.tabs[-1].button)
    assert {m.label: m.value for m in at.metric}["Candidate companies"] == "1"


HOSTILE = (
    "![x](https://evil.example/p.png) <script>alert(1)</script> **bold** [l](https://evil.example)"
)
STATS = {"stop": "submitted", "iterations": 2, "cost_usd": 0.05, "web_searches": 1, "model": "m"}


def _seed(tmp_path, *, category=None, stats=STATS, steps=0, error=None, status=None):
    engine = get_engine(tmp_path / "app.db")
    init_db(engine)
    with Session(engine) as session:
        run = RunLog(
            kind="enrich",
            started_at=datetime(2026, 10, 20, 12, tzinfo=UTC),
            status=status or ("error" if category else "ok"),
            failure_category=category,
            stats=stats,
            error=error,
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
    at = _app(monkeypatch, tmp_path, actions=False).run()
    assert not at.exception
    return at


def _subheaders(at) -> list[str]:
    return [s.value for s in at.subheader]


def test_observability_empty_points_at_the_enrich_command(monkeypatch, tmp_path):
    at = _observability(monkeypatch, tmp_path)
    assert any("uv run python -m sprout.enrich NAME_OR_NUMBER" in i.value for i in at.info)
    assert "Failures by category" not in _subheaders(at)
    assert "Runs" not in {m.label for m in at.metric}


def test_observability_populated_with_failed_run(monkeypatch, tmp_path):
    _seed(tmp_path, category="invalid_output", steps=2, error="validation failed")
    at = _observability(monkeypatch, tmp_path)
    assert {m.label: m.value for m in at.metric}["Failed"] == "1"
    assert {"Failures by category", "Trends", "Runs", "Run detail"} <= set(_subheaders(at))
    assert not at.success
    assert len(at.get("vega_lite_chart")) == 3  # category, cost trend, failures per day
    assert [e.label for e in at.expander] == ["Step 1 response", "Step 2 response"]
    assert "validation failed" in [c.value for c in at.code]


def test_observability_no_failed_runs_omits_category_chart(monkeypatch, tmp_path):
    _seed(tmp_path, steps=1)
    at = _observability(monkeypatch, tmp_path)
    assert [s.value for s in at.success] == ["No failed runs"]
    assert len(at.get("vega_lite_chart")) == 2  # trends only, no category chart


def test_observability_run_without_steps_that_ran_is_pre_phase_3(monkeypatch, tmp_path):
    _seed(tmp_path)
    at = _observability(monkeypatch, tmp_path)
    assert any("predates step logging (Phase 3), run-level stats only" in i.value for i in at.info)
    assert not at.expander


def test_observability_run_without_steps_or_response_shows_error(monkeypatch, tmp_path):
    stats = {"stop": "error", "iterations": 0, "cost_usd": 0.0}
    _seed(tmp_path, category="api_error", stats=stats, error="RateLimitError: slow down")
    at = _observability(monkeypatch, tmp_path)
    assert any("No model response was received" in i.value for i in at.info)
    assert "RateLimitError: slow down" in [c.value for c in at.code]


def test_observability_tolerates_run_with_empty_stats(monkeypatch, tmp_path):
    _seed(tmp_path, stats={}, status="running")
    at = _observability(monkeypatch, tmp_path)
    assert {m.label: m.value for m in at.metric}["Runs"] == "1"
    assert any("No model response was received" in i.value for i in at.info)


def test_observability_renders_logged_text_only_in_code(monkeypatch, tmp_path):
    _seed(
        tmp_path,
        category="invalid_output",
        steps=1,
        error=HOSTILE,
        stats={**STATS, "error": HOSTILE},
    )
    at = _observability(monkeypatch, tmp_path)
    assert HOSTILE in [c.value for c in at.code]
    rendered = " ".join(e.value for e in [*at.markdown, *at.caption, *at.info, *at.success])
    assert "evil.example" not in rendered
    assert not [e for e in at.expander if "evil" in e.label]
