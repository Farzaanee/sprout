from datetime import date
from pathlib import Path

from sqlmodel import Session
from streamlit.testing.v1 import AppTest

from sprout.app import ui
from sprout.candidates import assess
from sprout.db import CompanyNewsRow, get_engine, init_db, upsert_company
from sprout.sources.companies_house import CompanyHit

ENTRY_POINT = Path(__file__).parents[1] / "streamlit_app.py"
HOSTILE = "![x](https://evil.example/p.png) [l](https://evil.example) <script>alert(1)</script>"
ALPHA, BETA = 1, 2
TAB = 2


def _news(company_id, url, title, published, kind, summary="A summary.") -> CompanyNewsRow:
    return CompanyNewsRow(
        company_id=company_id,
        url=url,
        title=title,
        published=published,
        news_kind=kind,
        summary=summary,
    )


def _seed(path):
    engine = get_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for number, (name, category) in enumerate(
            [
                ("Alpha Robotics Ltd", "Robotics"),
                ("Beta Health Ltd", "Healthcare"),
                ("Gamma Energy Ltd", "Energy"),
            ],
            start=1,
        ):
            hit = CompanyHit(str(number), name, "active", date(2020, 1, 1), ("62012",), "London")
            company, _ = upsert_company(session, hit, assess(hit.name, hit.sic_codes))
            company.category = category
            session.add(company)
        session.flush()
        session.add(
            _news(ALPHA, "https://x.example/1", "Alpha raises", date(2026, 5, 2), "funding")
        )
        session.add(_news(ALPHA, "https://x.example/2", "Alpha undated", None, "launch"))
        session.add(_news(ALPHA, "https://x.example/3", "Alpha minor", date(2026, 6, 1), None))
        session.add(_news(BETA, "https://x.example/4", "Beta hires", date(2026, 3, 1), "launch"))
        session.add(
            _news(BETA, "javascript:alert(1)", HOSTILE, date(2026, 2, 1), "funding", HOSTILE)
        )
        session.commit()
    engine.dispose()


def _app(monkeypatch, tmp_path, *, seed=True) -> AppTest:
    if seed:
        _seed(tmp_path / "app.db")
    monkeypatch.setenv("SPROUT_DB_PATH", str(tmp_path / "app.db"))
    at = AppTest.from_file(str(ENTRY_POINT), default_timeout=30).run()
    assert not at.exception
    return at


def _text(at: AppTest) -> str:
    def walk(block) -> list[str]:
        out = []
        for child in block.children.values():
            if hasattr(child, "children"):
                out.extend(walk(child))
            elif type(child).__name__ in {"Markdown", "Caption", "Info"}:
                out.append(child.value)
        return out

    return " ".join(walk(at.tabs[TAB]))


def test_default_shows_only_big_news_latest_first_undated_last(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    text = _text(at)
    assert "4 big-news items across 2 companies; 1 undated" in text
    assert "Alpha minor" not in text
    order = [text.index(t) for t in ("Alpha raises", "Beta hires", "Alpha undated")]
    assert order == sorted(order)
    assert "Date not found" in text
    assert any(m.value.startswith(":blue-badge") for m in at.tabs[TAB].markdown)


def test_toggle_includes_items_that_are_not_big_news(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    at.tabs[TAB].toggle[0].set_value(True).run()
    assert not at.exception
    text = _text(at)
    assert "Alpha minor" in text
    assert "5 items across 2 companies; 1 undated" in text


def test_kind_filter_narrows_the_feed(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    kind = at.tabs[TAB].selectbox[0]
    assert kind.options == ["Any kind", "funding", "launch"]
    kind.set_value("launch").run()
    text = _text(at)
    assert "Alpha undated" in text and "Beta hires" in text
    assert "Alpha raises" not in text
    assert "2 big-news items across 2 companies; 1 undated" in text


def test_sidebar_filter_narrows_the_feed(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    at.sidebar.selectbox[1].set_value("Healthcare").run()
    assert not at.exception
    text = _text(at)
    assert "Beta hires" in text
    assert "Alpha" not in text
    assert "2 big-news items across 1 company; 0 undated" in text


def test_no_matching_company_shows_a_message(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    at.sidebar.text_input[0].set_value("zzz").run()
    assert not at.exception
    assert any("No companies match" in i.value for i in at.tabs[TAB].info)
    assert not any(m.value.startswith(":blue-badge") for m in at.tabs[TAB].markdown)


def test_empty_database_says_so_instead_of_a_blank_page(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path, seed=False)
    assert any("No companies match" in i.value for i in at.tabs[TAB].info)


def test_companies_without_big_news_show_the_short_feed_message(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    at.sidebar.selectbox[1].set_value("Energy").run()
    assert not at.exception
    assert any("feed is short" in i.value for i in at.tabs[TAB].info)


def test_hostile_text_is_never_markdown_and_a_bad_url_gets_no_link(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    markdown = [m.value for m in at.tabs[TAB].markdown]
    hostile_lines = [v for v in markdown if "evil" in v]
    assert len(hostile_lines) == 2  # title and summary
    assert set(hostile_lines) == {ui.plain(HOSTILE)}  # escaped, so no image, link or tag
    links = [b.proto.url for b in at.tabs[TAB].get("link_button")]
    assert "javascript:alert(1)" not in links
    assert links == [f"https://x.example/{n}" for n in (1, 4, 2)]


def test_summaries_are_labelled_as_model_written(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    assert "Summary (model-written)" in _text(at)
