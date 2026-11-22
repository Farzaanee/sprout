from datetime import UTC, date, datetime
from pathlib import Path

from sqlmodel import Session
from streamlit.testing.v1 import AppTest

from sprout.candidates import assess
from sprout.db import (
    CompanyNewsRow,
    FundingRoundRow,
    OfficerRow,
    get_engine,
    init_db,
    save_fact,
    upsert_company,
)
from sprout.models import Confidence, Sourced
from sprout.sources.companies_house import CompanyHit

ENTRY_POINT = Path(__file__).parents[1] / "streamlit_app.py"
HOSTILE = "![x](https://evil.example/p.png) [l](https://evil.example) <script>alert(1)</script>"
ALPHA, BETA = 1, 2


def _fact(
    value, url="https://example.com/fact", *, retrieved=(2026, 6, 1), confidence="high", note=None
):
    return Sourced(
        value=value,
        source_url=url if value is not None else None,
        retrieved_at=datetime(*retrieved, tzinfo=UTC),
        confidence=Confidence(confidence),
        note=note,
    )


def _round(company_id, stage, amount, announced, **extra) -> FundingRoundRow:
    return FundingRoundRow(
        company_id=company_id,
        stage=stage,
        amount_million_usd=amount,
        announced=announced,
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        confidence="high",
        source_url=f"https://example.com/round-{stage.replace(' ', '-')}",
        **extra,
    )


def _news(url, title, published, kind, summary) -> CompanyNewsRow:
    return CompanyNewsRow(
        company_id=ALPHA, url=url, title=title, published=published, news_kind=kind, summary=summary
    )


def _seed(path):
    """Alpha has a full profile; Beta is enriched (has a category) but has no facts at all."""
    engine = get_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for number, (name, category) in enumerate(
            [("Alpha Robotics Ltd", "Robotics"), ("Beta Health Ltd", "Healthcare")], start=1
        ):
            hit = CompanyHit(str(number), name, "active", date(2020, 1, 1), ("62012",), "London")
            company, _ = upsert_company(session, hit, assess(hit.name, hit.sic_codes))
            company.category = category
            session.add(company)
        session.flush()
        facts = {
            "one_liner": _fact(HOSTILE),
            "business_model": _fact(None),
            "hq": _fact("London"),
            "founded": _fact(2019),
            "total_raised_million_usd": _fact(25, note="US$31m converted at 0.8"),
            "valuation_million_usd": _fact(None),
            "business_status": _fact("Active"),
            "website": _fact("https://alpha.example"),
        }
        for field, fact in facts.items():
            save_fact(session, ALPHA, field, fact)
        # Two sources for one field: the newer retrieval wins (A6).
        save_fact(
            session,
            ALPHA,
            "stage",
            _fact("Seed", "https://example.com/old", retrieved=(2025, 1, 1)),
        )
        save_fact(session, ALPHA, "stage", _fact("Series A", "https://example.com/new"))
        session.add(_round(ALPHA, "Seed", 5, None))
        session.add(
            _round(ALPHA, "Series A", 20, date(2026, 5, 1), investors=[HOSTILE], note=HOSTILE)
        )
        session.add(_news("https://example.com/n1", HOSTILE, date(2026, 5, 2), "funding", HOSTILE))
        session.add(_news("https://example.com/n2", "Undated item", None, None, "Plain summary."))
        session.add(_news("javascript:alert(1)", "Bad link item", date(2026, 1, 1), None, None))
        session.add(
            OfficerRow(
                company_id=ALPHA,
                name=HOSTILE,
                role="director",
                birth_year=1980,
                nationality=None,
                source_url="https://example.com/officers",
                retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
                confidence="medium",
            )
        )
        session.commit()
    engine.dispose()


def _app(monkeypatch, tmp_path, *, seed=True) -> AppTest:
    if seed:
        _seed(tmp_path / "app.db")
    monkeypatch.setenv("SPROUT_DB_PATH", str(tmp_path / "app.db"))
    return AppTest.from_file(str(ENTRY_POINT), default_timeout=30).run()


def _select(at: AppTest, row: int) -> AppTest:
    table = at.tabs[1].dataframe[0]
    at.session_state[table.key] = {"selection": {"rows": [row], "columns": []}}
    return at.run()


def _text(at: AppTest) -> str:
    """Text of the Companies tab in page order."""

    def walk(block) -> list[str]:
        out = []
        for child in block.children.values():
            if hasattr(child, "children"):
                out.extend(walk(child))
            elif type(child).__name__ in {"Markdown", "Caption", "Subheader", "Info"}:
                out.append(child.value)
        return out

    return " ".join(walk(at.tabs[1]))


def _links(at: AppTest) -> list[str]:
    return [b.proto.url for b in at.tabs[1].get("link_button")]


def _detail(monkeypatch, tmp_path, row: int) -> AppTest:
    at = _select(_app(monkeypatch, tmp_path), row)
    assert not at.exception
    return at


def test_list_shows_not_disclosed_for_unknowns(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    assert not at.exception
    table = at.tabs[1].dataframe[0].value
    assert list(table["name"]) == ["Alpha Robotics Ltd", "Beta Health Ltd"]
    alpha, beta = (table.iloc[i] for i in (0, 1))
    assert (alpha["stage"], alpha["latest round"], alpha["founded"]) == (
        "Series A",
        "US$20m",
        "2019",
    )
    assert (beta["one-liner"], beta["stage"], beta["latest round"], beta["founded"]) == (
        ("Not disclosed",) * 4
    )
    assert "Select a row" in _text(at)
    assert not at.tabs[1].get("link_button")  # nothing open until a row is selected


def test_no_match_shows_a_message(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    at.sidebar.text_input[0].set_value("zzz").run()
    assert not at.exception
    assert any("No companies match" in i.value for i in at.tabs[1].info)
    assert not at.tabs[1].dataframe


def test_empty_database_shows_the_no_match_message(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path, seed=False)
    assert not at.exception
    assert any("No companies match" in i.value for i in at.tabs[1].info)


def test_null_winner_is_not_disclosed_and_no_fact_is_not_researched(monkeypatch, tmp_path):
    text = _text(_detail(monkeypatch, tmp_path, 0))
    business_model = text.split("**Business model**")[1].split("**Target user**")[0]
    assert "Not disclosed" in business_model and "Not researched" not in business_model
    target_user = text.split("**Target user**")[1].split("**Headquarters**")[0]
    assert "Not researched" in target_user and "Not disclosed" not in target_user
    valuation = text.split("**Valuation**")[1].split("**Business status**")[0]
    assert "Not disclosed" in valuation


def test_company_with_no_facts_reads_not_researched_and_has_empty_sections(monkeypatch, tmp_path):
    at = _detail(monkeypatch, tmp_path, 1)
    text = _text(at)
    assert text.count("Not researched") == 9
    assert "No funding rounds found" in text
    assert "No news found" in text
    assert "No officers recorded" in text
    assert _links(at) == ["https://find-and-update.company-information.service.gov.uk/company/2"]


def test_every_disclosed_fact_has_a_source_link_and_the_winner_is_the_newest(monkeypatch, tmp_path):
    at = _detail(monkeypatch, tmp_path, 0)
    text = _text(at)
    links = _links(at)
    assert "Series A" in text and "Seed" not in text.split("**Stage**")[1].split("**Total")[0]
    assert "https://example.com/new" in links and "https://example.com/old" not in links
    assert "No source link" not in text
    disclosed = [
        "one_liner",
        "hq",
        "founded",
        "stage",
        "total_raised_million_usd",
        "business_status",
    ]
    keys = [b.key for b in at.tabs[1].get("link_button")]
    assert all(f"fact_{field}" in keys for field in disclosed)
    assert "fact_business_model" not in keys and "fact_valuation_million_usd" not in keys


def test_money_fields_use_the_money_format_and_show_their_note(monkeypatch, tmp_path):
    text = _text(_detail(monkeypatch, tmp_path, 0)).replace("\\", "")
    assert "US$25m" in text
    assert "Note: US$31m converted at 0.8" in text


def test_header_links_are_http_only(monkeypatch, tmp_path):
    links = _links(_detail(monkeypatch, tmp_path, 0))
    assert "https://find-and-update.company-information.service.gov.uk/company/1" in links
    assert "https://alpha.example" in links
    assert not [u for u in links if not u.startswith(("http://", "https://"))]


def test_funding_history_newest_first_with_sources_and_missing_date(monkeypatch, tmp_path):
    at = _detail(monkeypatch, tmp_path, 0)
    text = _text(at)
    assert text.index("**Series A**") < text.index("**Seed**")
    assert "US\\$20m" in text and "US\\$5m" in text
    assert "2026-05-01" in text
    assert "Date not found" in text.split("**Seed**")[1].split("**News timeline**")[0]
    links = _links(at)
    assert (
        "https://example.com/round-Series-A" in links and "https://example.com/round-Seed" in links
    )


def test_a_funding_round_without_a_source_link_says_so(monkeypatch, tmp_path):
    _seed(tmp_path / "app.db")
    engine = get_engine(tmp_path / "app.db")
    with Session(engine) as session:
        round_ = _round(ALPHA, "Series C", 50, date(2027, 1, 1))
        round_.source_url = None
        session.add(round_)
        session.commit()
    engine.dispose()
    text = _text(_select(_app(monkeypatch, tmp_path, seed=False), 0))
    funding = text.split("**Funding history**")[1].split("**News timeline**")[0]
    assert funding.index("Series C") < funding.index("No source link") < funding.index("Series A")


def test_news_timeline_shows_kind_labelled_links_and_date_not_found(monkeypatch, tmp_path):
    at = _detail(monkeypatch, tmp_path, 0)
    tab = at.tabs[1]
    text = _text(at)
    assert any("funding" in m.value for m in tab.markdown if m.value.startswith(":blue-badge"))
    assert text.index("Series A") < text.index("Undated item")
    assert "Date not found" in text.split("Undated item")[1]
    assert "Plain summary" in text
    links = _links(at)
    assert "https://example.com/n1" in links and "https://example.com/n2" in links
    assert "javascript:alert(1)" not in links
    assert {
        b.label
        for b in tab.get("link_button")
        if b.proto.url in {"https://example.com/n1", "https://example.com/n2"}
    } == {"Read article"}


def test_officers_table_has_role_birth_year_nationality_and_source(monkeypatch, tmp_path):
    officers = _detail(monkeypatch, tmp_path, 0).tabs[1].dataframe[1].value
    row = officers.iloc[0]
    assert (row["role"], row["born"], row["nationality"]) == ("director", "1980", "Not disclosed")
    assert row["source"] == "https://example.com/officers"


def test_hostile_text_never_becomes_markdown(monkeypatch, tmp_path):
    at = _detail(monkeypatch, tmp_path, 0)
    tab = at.tabs[1]
    text = _text(at)
    for raw in ("![x](", "[l](", "<script>"):
        assert raw not in text
    assert "\\!\\[x\\]" in text  # escaped, so it shows as literal text
    assert not tab.get("imgs")
    assert not [u for u in _links(at) if "evil.example" in u]
    # The officer name only ever sits in a dataframe cell.
    assert HOSTILE in list(tab.dataframe[1].value["name"])


def test_changing_a_filter_clears_the_selection(monkeypatch, tmp_path):
    at = _detail(monkeypatch, tmp_path, 1)
    at.sidebar.selectbox[1].select("Healthcare").run()
    assert not at.exception
    assert len(at.tabs[1].dataframe[0].value) == 1
    assert "Select a row" in _text(at) and not at.tabs[1].get("link_button")
