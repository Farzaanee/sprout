import os
import shutil
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session
from streamlit.testing.v1 import AppTest

from sprout import queries
from sprout.app import ui
from sprout.app.filters import FUNDING_STEPS, Filters
from sprout.candidates import assess
from sprout.db import Company, get_engine, init_db, save_fact, upsert_company
from sprout.models import Confidence, Sourced
from sprout.sources.companies_house import CompanyHit

ENTRY_POINT = Path(__file__).parents[1] / "streamlit_app.py"
HOSTILE = "![x](https://evil.example/p.png) [l](https://evil.example) <script>alert(1)</script>"


def test_plain_neutralises_markdown_and_html():
    out = ui.plain(HOSTILE)
    for marker in ("![", "](", "<script>"):
        assert marker not in out
    assert out.startswith("\\!\\[x\\]\\(")


def test_plain_handles_none():
    assert ui.plain(None) == ""


def test_money_never_shows_a_missing_amount_as_zero():
    assert ui.money(None) == "Not disclosed"
    assert ui.money(0) == "US$0m"
    assert ui.money(12) == "US$12m"
    assert ui.money(1200) == "US$1.2bn"


def _link_buttons(url: str | None) -> list:
    def script() -> None:
        import streamlit as st

        from sprout.app import ui

        ui.source_link(st.session_state["url"])

    at = AppTest.from_function(script)
    at.session_state["url"] = url
    at.run()
    assert not at.exception
    return at.get("link_button")


@pytest.mark.parametrize(
    "url", [None, "", "javascript:alert(1)", "data:text/html,x", "ftp://x.example", "//x.example"]
)
def test_source_link_renders_nothing_for_non_http_urls(url):
    assert _link_buttons(url) == []


def test_source_link_renders_a_fixed_label_for_http_urls():
    (button,) = _link_buttons("https://example.com/page")
    assert button.proto.label == "Source"
    assert button.proto.url == "https://example.com/page"


def test_filters_default_to_nothing_passed_and_are_hashable():
    assert Filters() == Filters(None, None, None, None, None, None, None)
    assert Filters(category="Fintech") == Filters(category="Fintech")
    assert len({Filters(), Filters(), Filters(stage="Seed")}) == 2
    assert FUNDING_STEPS[0] == 0 and FUNDING_STEPS[-1] == 1000


def test_db_version_changes_when_the_file_does(tmp_path):
    path = tmp_path / "v.db"
    assert ui.db_version(str(path)) == 0.0
    path.write_text("a")
    first = ui.db_version(str(path))
    os.utime(path, (first + 10, first + 10))
    assert ui.db_version(str(path)) == first + 10


def _seed_enriched(path):
    engine = get_engine(path)
    init_db(engine)
    hit = CompanyHit("1", "Neural Robotics Ltd", "active", date(2020, 1, 1), ("62012",), "London")
    with Session(engine) as session:
        company, _ = upsert_company(session, hit, assess(hit.name, hit.sic_codes))
        company.category = "Robotics"
        session.add(company)
        stage = Sourced(value="Seed", source_url="https://example.com", confidence=Confidence.HIGH)
        save_fact(session, company.id, "stage", stage)
        session.commit()
    engine.dispose()  # closes the last connection, which checkpoints and removes the WAL


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_open_engine_reads_a_wal_database_from_a_read_only_directory(tmp_path):
    source = tmp_path / "source" / "snap.db"
    _seed_enriched(source)
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    copy = readonly_dir / "snap.db"
    shutil.copy(source, copy)
    readonly_dir.chmod(0o555)
    copy.chmod(0o444)
    try:
        engine = ui.open_engine(str(copy))
        with Session(engine) as session:
            assert queries.count_rows(session)["companies"] == 1
            assert session.exec(Company.__table__.select()).first() is not None
        engine.dispose()
        assert sorted(p.name for p in readonly_dir.iterdir()) == ["snap.db"]
    finally:
        readonly_dir.chmod(0o755)


def test_open_engine_initialises_a_writable_path(tmp_path):
    engine = ui.open_engine(str(tmp_path / "new.db"))
    with Session(engine) as session:
        assert queries.count_rows(session)["companies"] == 0


def test_sidebar_filters_the_enriched_companies(monkeypatch, tmp_path):
    _seed_enriched(tmp_path / "app.db")
    monkeypatch.setenv("SPROUT_DB_PATH", str(tmp_path / "app.db"))
    at = AppTest.from_file(str(ENTRY_POINT), default_timeout=30).run()
    assert not at.exception
    assert [s.label for s in at.sidebar.selectbox] == ["Database", "Category", "Stage"]
    assert at.sidebar.selectbox[1].options == ["Any", "Robotics (1)"]
    assert at.sidebar.selectbox[2].options == ["Any", "Seed"]
    sidebar_text = " ".join(c.value for c in at.sidebar.caption)
    assert "Apply to Overview, Companies and News feed" in sidebar_text
    assert "1 of 1 enriched companies match" in sidebar_text
    assert "Funding known for 0 of 1" in sidebar_text
    assert "Stage known for 1 of 1".lower() in sidebar_text.lower()
    assert {m.label: m.value for m in at.tabs[0].metric}["Companies matched"] == "1"

    at.sidebar.text_input[0].set_value("zzz").run()
    assert "0 of 1 enriched companies match" in " ".join(c.value for c in at.sidebar.caption)
