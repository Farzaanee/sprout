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
    round_ = FundingRound(stage=Stage.SEED, amount_million_usd=1)
    save_fact(
        session,
        company.id,
        "funding_rounds",
        Sourced[list[FundingRound]](value=[round_], source_url="https://example.com/r"),
    )
    save_fact(session, company.id, "hq", Sourced[str]())
    save_fact(session, company.id, "hq", Sourced[str]())  # NULL source: still one row
    session.commit()
    by_field = {f.field: f for f in session.exec(select(Fact)).all()}
    assert '"amount_million_usd":1' in by_field["funding_rounds"].value_json
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


def test_save_profile_flattens_sourced_fields_and_sets_category(session):
    from sprout.db import save_profile
    from sprout.models import Category, CompanyProfile

    company, _ = upsert_company(session, hit(), assess("Acme AI Ltd", ["62012"]))
    profile = CompanyProfile(
        name="Acme AI Ltd",
        category=Category.ROBOTICS_AND_AUTOMATION,
        hq=Sourced[str](value="London", source_url="https://example.com/a"),
        funding_rounds=Sourced[list[FundingRound]](
            value=[FundingRound(stage=Stage.SEED, amount_million_usd=1)],
            source_url="https://example.com/r",
            note="GBP 1m as reported",
        ),
    )
    save_profile(session, company, profile)
    session.commit()

    rows = {f.field: f for f in session.exec(select(Fact))}
    assert set(rows) == {
        "website", "hq", "founded", "stage", "total_raised_million_usd", "valuation_million_usd",
        "funding_rounds", "business_model", "target_user", "one_liner",
    }  # fmt: skip
    assert rows["hq"].value_json == '"London"' and rows["hq"].source_url == "https://example.com/a"
    assert '"amount_million_usd":1' in rows["funding_rounds"].value_json
    assert rows["website"].value_json is None and rows["website"].source_url is None
    session.refresh(company)
    assert company.category == "Robotics and automation"


def test_save_profile_requires_a_saved_company(session):
    from sprout.db import save_profile
    from sprout.models import CompanyProfile

    with pytest.raises(ValueError):
        save_profile(session, Company(name="x"), CompanyProfile(name="x"))


def test_delete_profile_clears_facts_and_category(session):
    from sprout.db import delete_profile, save_profile
    from sprout.models import Category, CompanyProfile

    company, _ = upsert_company(session, hit(), assess("Acme AI Ltd", ["62012"]))
    profile = CompanyProfile(
        name="Acme AI Ltd",
        category=Category.ROBOTICS_AND_AUTOMATION,
        hq=Sourced[str](value="London", source_url="https://example.com/a"),
    )
    save_profile(session, company, profile)
    session.commit()

    deleted = delete_profile(session, company)
    session.commit()

    assert deleted == 10  # every Sourced field on CompanyProfile (name, category, news excluded)
    assert session.exec(select(Fact).where(Fact.company_id == company.id)).all() == []
    session.refresh(company)
    assert company.category is None


def test_delete_profile_requires_a_saved_company(session):
    from sprout.db import delete_profile

    with pytest.raises(ValueError):
        delete_profile(session, Company(name="x"))


def test_add_company_by_number_creates_a_row_from_a_company_profile(session):
    from sprout.db import add_company_by_number

    data = {
        "company_number": "10924127",
        "company_name": "WAYVE TECHNOLOGIES LTD",
        "company_status": "active",
        "date_of_creation": "2017-08-21",
        "sic_codes": ["62012"],
        "registered_office_address": {"locality": "London"},
    }
    company = add_company_by_number(session, "10924127", data)
    again = add_company_by_number(session, "10924127", {**data, "company_name": "WAYVE LTD"})
    session.commit()
    assert company.id == again.id and again.name == "WAYVE LTD"
    assert company.incorporated == date(2017, 8, 21) and company.locality == "London"
    with pytest.raises(ValueError):
        add_company_by_number(session, "10924127", {"company_number": "10924127"})


def test_init_db_adds_new_columns_to_an_old_database_and_keeps_rows(tmp_path):
    from sqlalchemy import text

    engine = get_engine(tmp_path / "old.db")
    with engine.begin() as conn:  # the Phase 1 company table, before `category` existed
        conn.execute(
            text(
                "CREATE TABLE company (id INTEGER PRIMARY KEY, company_number VARCHAR, "
                "name VARCHAR NOT NULL, status VARCHAR, incorporated DATE, "
                "sic_codes JSON NOT NULL, locality VARCHAR, candidate_score INTEGER NOT NULL, "
                "matched_keywords JSON NOT NULL, "
                "first_seen_at DATETIME NOT NULL, last_seen_at DATETIME NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO company VALUES (1, '12345678', 'Old Co', 'active', NULL, '[]', "
                "'Leeds', 3, '[]', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
    init_db(engine)
    init_db(engine)  # idempotent
    with Session(engine) as s:
        (row,) = s.exec(select(Company)).all()
        assert (row.name, row.locality, row.candidate_score, row.category) == (
            "Old Co", "Leeds", 3, None,
        )  # fmt: skip
        row.category = "Other"
        s.commit()
