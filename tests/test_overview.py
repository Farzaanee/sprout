from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
from sqlmodel import Session
from streamlit.testing.v1 import AppTest

from sprout.candidates import assess
from sprout.db import FundingRoundRow, get_engine, init_db, save_fact, upsert_company
from sprout.models import Confidence, Sourced
from sprout.sources.companies_house import CompanyHit

ENTRY_POINT = Path(__file__).parents[1] / "streamlit_app.py"
HOSTILE = "![x](https://evil.example/p.png) [l](https://evil.example) <script>alert(1)</script>"


def _round(company_id, stage, amount, announced, **extra) -> FundingRoundRow:
    return FundingRoundRow(
        company_id=company_id,
        stage=stage,
        amount_million_usd=amount,
        announced=announced,
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        confidence="high",
        source_url="https://example.com/round",
        **extra,
    )


def _seed(path):
    engine = get_engine(path)
    init_db(engine)
    with Session(engine) as session:
        ids = []
        for number, (name, category) in enumerate(
            [("Alpha Robotics Ltd", "Robotics"), ("Beta Health Ltd", "Healthcare")], start=1
        ):
            hit = CompanyHit(str(number), name, "active", date(2020, 1, 1), ("62012",), "London")
            company, _ = upsert_company(session, hit, assess(hit.name, hit.sic_codes))
            company.category = category
            session.add(company)
            ids.append(company.id)
        stage = Sourced(value="Seed", source_url="https://example.com", confidence=Confidence.HIGH)
        save_fact(session, ids[0], "stage", stage)
        session.add(_round(ids[0], "Seed", 5, date(2026, 1, 10)))
        session.add(
            _round(
                ids[0],
                "Series A",
                20,
                date(2026, 5, 1),
                investors=[HOSTILE],
                note=HOSTILE,
            )
        )
        session.add(_round(ids[1], "Series B", None, None))
        session.commit()
    engine.dispose()


def _overview(monkeypatch, tmp_path, *, seed=True) -> AppTest:
    if seed:
        _seed(tmp_path / "app.db")
    monkeypatch.setenv("SPROUT_DB_PATH", str(tmp_path / "app.db"))
    return AppTest.from_file(str(ENTRY_POINT), default_timeout=30).run()


def _metrics(at) -> dict[str, str]:
    return {m.label: m.value for m in at.tabs[0].metric}


def _chart_data(at) -> dict[str, pd.DataFrame]:
    """Chart data frames keyed by their category column, read from the Arrow payload."""
    frames = [
        pa.ipc.open_stream(dataset.data.data).read_pandas()
        for chart in at.tabs[0].get("vega_lite_chart")
        for dataset in chart.proto.datasets
    ]
    return {frame.columns[0]: frame for frame in frames}


def _overview_text(at) -> str:
    tab = at.tabs[0]
    return " ".join(e.value for e in [*tab.markdown, *tab.caption, *tab.info])


def test_coverage_strip_counts_over_the_matched_companies(monkeypatch, tmp_path):
    at = _overview(monkeypatch, tmp_path)
    assert not at.exception
    assert _metrics(at) == {
        "Companies matched": "2",
        "Stage known": "1 of 2",
        "Funding known": "1 of 2",
        "Rounds with an amount": "2 of 3",
    }


def test_funding_chart_states_its_coverage(monkeypatch, tmp_path):
    at = _overview(monkeypatch, tmp_path)
    text = _overview_text(at)
    assert "Funding known for 1 of 2 companies. 2 of 3 rounds have an amount" in text
    assert "Funding known for 1 of 2" in " ".join(c.value for c in at.sidebar.caption)


def test_category_counts_say_they_are_a_classification(monkeypatch, tmp_path):
    at = _overview(monkeypatch, tmp_path)
    assert "model classification, not a sourced fact" in _overview_text(at)


def test_a_stage_without_an_amount_is_not_disclosed_and_not_charted_as_zero(monkeypatch, tmp_path):
    at = _overview(monkeypatch, tmp_path)
    assert "Series B: Not disclosed (0 of 1 with an amount)" in _overview_text(at).replace("\\", "")
    charted = _chart_data(at)["stage"]
    assert list(charted["stage"]) == ["Seed", "Series A"]
    assert list(charted["total US$m"]) == [5, 20]
    assert list(charted["amount_known"]) == [1, 1]


def test_rounds_table_and_recent_list_show_each_round(monkeypatch, tmp_path):
    at = _overview(monkeypatch, tmp_path)
    table = at.tabs[0].dataframe[0].value
    assert list(table["stage"]) == ["Series A", "Seed", "Series B"]
    assert list(table["amount"]) == ["US$20m", "US$5m", "Not disclosed"]
    assert list(table["announced"]) == ["2026-05-01", "2026-01-10", "Date not found"]
    assert set(table["source"]) == {"https://example.com/round"}
    text = _overview_text(at)
    assert "Date not found" in text and "Not disclosed" in text


def test_a_round_without_investors_or_a_source_link_says_so(monkeypatch, tmp_path):
    _seed(tmp_path / "app.db")
    engine = get_engine(tmp_path / "app.db")
    with Session(engine) as session:
        session.add(
            FundingRoundRow(
                company_id=1,
                stage="Series C",
                amount_million_usd=50,
                announced=date(2027, 1, 1),
                retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
                confidence="low",
            )
        )
        session.commit()
    engine.dispose()
    at = _overview(monkeypatch, tmp_path, seed=False)
    assert not at.exception
    table = at.tabs[0].dataframe[0].value
    assert "" not in set(table["investors"])
    assert table["investors"].iloc[0] == "not listed"
    assert "No source link" in _overview_text(at)


def test_hostile_round_text_does_not_become_markdown(monkeypatch, tmp_path):
    at = _overview(monkeypatch, tmp_path)
    assert not at.exception
    text = _overview_text(at)
    assert "![x](" not in text
    assert "[l](" not in text
    assert "<script>" not in text
    assert "\\!\\[x\\]" in text  # escaped, so it renders as literal text
    assert not at.tabs[0].get("imgs")
    # The raw value only ever sits in a dataframe cell.
    assert HOSTILE in list(at.tabs[0].dataframe[0].value["note"])


def test_no_matching_company_shows_a_message_not_an_unfiltered_page(monkeypatch, tmp_path):
    at = _overview(monkeypatch, tmp_path)
    at.sidebar.text_input[0].set_value("zzz").run()
    assert not at.exception
    assert any("No companies match" in i.value for i in at.tabs[0].info)
    assert not at.tabs[0].metric
    assert not at.tabs[0].dataframe


def test_filter_narrows_the_overview(monkeypatch, tmp_path):
    at = _overview(monkeypatch, tmp_path)
    at.sidebar.selectbox[1].select("Healthcare").run()
    assert not at.exception
    assert _metrics(at)["Companies matched"] == "1"
    assert _metrics(at)["Rounds with an amount"] == "0 of 1"
    assert any("No round has a disclosed amount" in i.value for i in at.tabs[0].info)


def test_empty_database_shows_the_no_match_message(monkeypatch, tmp_path):
    at = _overview(monkeypatch, tmp_path, seed=False)
    assert not at.exception
    assert any("No companies match" in i.value for i in at.tabs[0].info)
