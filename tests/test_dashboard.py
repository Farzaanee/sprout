from datetime import date
from pathlib import Path

from sqlmodel import Session
from streamlit.testing.v1 import AppTest

from sprout.candidates import assess
from sprout.db import get_engine, init_db, upsert_company
from sprout.sources.companies_house import CompanyHit

ENTRY_POINT = Path(__file__).parents[1] / "streamlit_app.py"


def _app(monkeypatch, tmp_path, *, actions: bool) -> AppTest:
    monkeypatch.setenv("SPROUT_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("SPROUT_ENABLE_ACTIONS", "1" if actions else "0")
    return AppTest.from_file(str(ENTRY_POINT), default_timeout=30)


def test_renders_with_empty_database_read_only(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path, actions=False).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Progress", "Companies", "News"]
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
    assert "Run & test" in [t.label for t in at.tabs]
    assert {m.label: m.value for m in at.metric}["Candidate companies"] == "1"
