from datetime import date

import pytest
from sqlmodel import Session, select

from sprout.candidates import assess
from sprout.db import (
    Company,
    Fact,
    NewsItemRow,
    RunLog,
    add_news_items,
    get_engine,
    init_db,
    logged_run,
    save_fact,
    upsert_company,
)
from sprout.models import Confidence, FundingRound, NewsItem, Sourced, Stage
from sprout.sources.companies_house import CompanyHit


@pytest.fixture
def session(tmp_path):
    engine = get_engine(tmp_path / "nested" / "test.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def hit(number="12345678", name="Acme AI Ltd") -> CompanyHit:
    return CompanyHit(number, name, "active", date(2020, 1, 1), ("62012",), "London")


def test_upsert_company_dedupes_on_company_number(session):
    first, created = upsert_company(session, hit(), assess("Acme AI Ltd", ["62012"]))
    again, created_again = upsert_company(session, hit(name="Acme Intelligence Ltd"), assess("x"))
    session.commit()
    assert created and not created_again
    assert first.id == again.id
    rows = session.exec(select(Company)).all()
    assert len(rows) == 1 and rows[0].name == "Acme Intelligence Ltd"
    assert rows[0].sic_codes == ["62012"]


def test_save_fact_stores_provenance_and_is_idempotent(session):
    company, _ = upsert_company(session, hit(), assess("Acme AI Ltd", ["62012"]))
    fact = Sourced[int](
        value=2020, source_url="https://example.com/p", confidence=Confidence.HIGH, note="n"
    )
    save_fact(session, company.id, "founded", fact)
    save_fact(session, company.id, "founded", fact)
    session.commit()
    rows = session.exec(select(Fact)).all()
    assert len(rows) == 1
    row = rows[0]
    assert (row.value_json, row.source_url, row.confidence, row.note) == (
        "2020",
        "https://example.com/p",
        "high",
        "n",
    )
    assert row.retrieved_at.utcoffset().total_seconds() == 0


def test_save_fact_serialises_models_and_not_disclosed(session):
    company, _ = upsert_company(session, hit(), assess("Acme AI Ltd", ["62012"]))
    round_ = FundingRound(stage=Stage.SEED, amount_gbp=1_000_000)
    save_fact(
        session,
        company.id,
        "last_round",
        Sourced[FundingRound](value=round_, source_url="https://example.com/r"),
    )
    save_fact(session, company.id, "hq", Sourced[str]())
    save_fact(session, company.id, "hq", Sourced[str]())  # NULL source: still one row
    session.commit()
    by_field = {f.field: f for f in session.exec(select(Fact)).all()}
    assert '"amount_gbp":1000000' in by_field["last_round"].value_json
    assert by_field["hq"].value_json is None
    assert len(by_field) == 2 and len(session.exec(select(Fact)).all()) == 2


def test_add_news_items_dedupes_by_url(session):
    def item(url, title="t"):
        return NewsItem(title=title, url=url)

    batch = [
        item("https://n.example/a"),
        item("https://n.example/a?utm_source=x", "dup in batch"),
        item("https://n.example/b"),
    ]
    assert add_news_items(session, batch) == 2
    session.commit()
    assert add_news_items(session, batch) == 0
    assert len(session.exec(select(NewsItemRow)).all()) == 2


def test_logged_run_records_success_and_failure(session):
    with logged_run(session, "demo") as run:
        run.stats = {"n": 1}
    with pytest.raises(RuntimeError, match="boom"):
        with logged_run(session, "demo"):
            raise RuntimeError("boom")
    runs = session.exec(select(RunLog).order_by(RunLog.id)).all()
    assert [r.status for r in runs] == ["ok", "error"]
    assert runs[0].stats == {"n": 1}
    assert "boom" in runs[1].error
    assert all(r.finished_at for r in runs)
