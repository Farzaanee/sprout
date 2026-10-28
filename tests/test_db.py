import json
from datetime import UTC, date, datetime

import pytest
from sqlmodel import Session, select

from sprout.candidates import assess
from sprout.db import (
    Company,
    Fact,
    FundingRoundRow,
    NewsItemRow,
    RunLog,
    RunLogStep,
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
    save_fact(session, company.id, "hq", Sourced[str]())
    save_fact(session, company.id, "hq", Sourced[str]())  # NULL source: still one row
    session.commit()
    by_field = {f.field: f for f in session.exec(select(Fact)).all()}
    assert by_field["hq"].value_json is None
    assert len(by_field) == 1 and len(session.exec(select(Fact)).all()) == 1


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
        funding_rounds=[
            Sourced[FundingRound](
                value=FundingRound(stage=Stage.SEED, amount_million_usd=1),
                source_url="https://example.com/r",
                note="GBP 1m as reported",
            )
        ],
    )
    save_profile(session, company, profile)
    session.commit()

    rows = {f.field: f for f in session.exec(select(Fact))}
    assert set(rows) == {
        "website", "hq", "founded", "stage", "total_raised_million_usd", "valuation_million_usd",
        "business_model", "target_user", "one_liner",
    }  # fmt: skip
    assert rows["hq"].value_json == '"London"' and rows["hq"].source_url == "https://example.com/a"
    assert rows["website"].value_json is None and rows["website"].source_url is None
    session.refresh(company)
    assert company.category == "Robotics and automation"


def test_save_profile_stores_each_round_with_its_own_source_and_replaces_on_resave(session):
    from sprout.db import save_profile
    from sprout.models import CompanyProfile

    company, _ = upsert_company(session, hit(), assess("Acme AI Ltd", ["62012"]))

    def rounds(*specs):
        return [
            Sourced[FundingRound](
                value=FundingRound(stage=stage, amount_million_usd=amount, investors=["Seedcamp"]),
                source_url=url,
                confidence=Confidence.MEDIUM,
                note=note,
            )
            for stage, amount, url, note in specs
        ]

    first = rounds(
        (Stage.SEED, 1, "https://example.com/seed", "GBP 1m"),
        (Stage.SERIES_A, 9, "https://example.com/a", "US$9m"),
    )
    save_profile(session, company, CompanyProfile(name="Acme", funding_rounds=first))
    session.commit()
    stored = session.exec(select(FundingRoundRow).order_by(FundingRoundRow.id)).all()
    assert [(r.stage, r.amount_million_usd, r.source_url, r.note) for r in stored] == [
        ("Seed", 1, "https://example.com/seed", "GBP 1m"),
        ("Series A", 9, "https://example.com/a", "US$9m"),
    ]
    assert stored[0].investors == ["Seedcamp"] and stored[0].confidence == "medium"
    assert stored[0].company_id == company.id and stored[0].announced is None

    second = rounds((Stage.SEED, 2, "https://example.com/other", "GBP 2m"))
    second.append(Sourced[FundingRound]())  # undisclosed entry is not stored
    save_profile(session, company, CompanyProfile(name="Acme", funding_rounds=second))
    session.commit()
    (only,) = session.exec(select(FundingRoundRow)).all()
    assert (only.amount_million_usd, only.source_url) == (2, "https://example.com/other")


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
        funding_rounds=[
            Sourced[FundingRound](
                value=FundingRound(stage=Stage.SEED), source_url="https://example.com/r"
            )
        ],
    )
    save_profile(session, company, profile)
    session.commit()
    assert len(session.exec(select(FundingRoundRow)).all()) == 1

    deleted = delete_profile(session, company)
    session.commit()

    assert deleted == 9  # every single-valued Sourced field (rounds live in funding_round)
    assert session.exec(select(Fact).where(Fact.company_id == company.id)).all() == []
    assert session.exec(select(FundingRoundRow)).all() == []
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


def _old_run_log_db(tmp_path):
    """A pre-Phase-3 database: run_log without `failure_category`, no run_log_step table."""
    from sqlalchemy import text

    engine = get_engine(tmp_path / "old_runs.db")
    rows = [  # (id, kind, status, stats)
        (1, "enrich", "ok", {"stop": "submitted"}),
        (2, "enrich", "ok", {"stop": "invalid"}),
        (3, "enrich", "error", {"stop": "refused"}),
        (4, "enrich", "error", {}),  # crashed before any stats: internal_error
        (5, "enrich", "running", {}),  # unfinished: left alone
        (6, "ingest_ch", "error", {"stop": "invalid"}),  # not an enrich run: left alone
        (7, "enrich", "ok", {"stop": "brand_new_stop"}),
    ]
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE run_log (id INTEGER PRIMARY KEY, kind VARCHAR NOT NULL, "
                "started_at DATETIME NOT NULL, finished_at DATETIME, status VARCHAR NOT NULL, "
                "stats JSON NOT NULL, error VARCHAR)"
            )
        )
        for id_, kind, status, stats in rows:
            conn.execute(
                text(
                    "INSERT INTO run_log (id, kind, started_at, status, stats) "
                    "VALUES (:id, :kind, '2026-01-01 00:00:00', :status, :stats)"
                ),
                {"id": id_, "kind": kind, "status": status, "stats": json.dumps(stats)},
            )
    return engine


def test_init_db_migrates_old_run_log_keeping_rows_and_adds_step_table(tmp_path):
    from sqlalchemy import inspect

    engine = _old_run_log_db(tmp_path)
    init_db(engine)
    assert inspect(engine).has_table("run_log_step")
    with Session(engine) as s:
        runs = s.exec(select(RunLog).order_by(RunLog.id)).all()
        assert [r.id for r in runs] == [1, 2, 3, 4, 5, 6, 7]
        assert runs[1].stats == {"stop": "invalid"}
        s.add(RunLogStep(run_log_id=1, iteration=1, created_at=datetime.now(UTC), tool_name=None))
        s.commit()
        step = s.exec(select(RunLogStep)).one()
        assert (step.tool_errors, step.cost_usd, step.response_text) == (0, 0.0, None)


def test_backfill_classifies_failed_enrich_runs_and_is_idempotent(tmp_path):
    engine = _old_run_log_db(tmp_path)
    init_db(engine)
    expected = [None, "invalid_output", "refusal", "internal_error", None, None, "internal_error"]
    with Session(engine) as s:
        runs = s.exec(select(RunLog).order_by(RunLog.id)).all()
        assert [r.failure_category for r in runs] == expected
        runs[1].failure_category = "budget_cap"  # a later manual or newer value must survive
        s.commit()
    init_db(engine)
    with Session(engine) as s:
        runs = s.exec(select(RunLog).order_by(RunLog.id)).all()
        assert [r.failure_category for r in runs] == [
            None, "budget_cap", "refusal", "internal_error", None, None, "internal_error",
        ]  # fmt: skip


def test_logged_run_failure_defaults_to_internal_error(session):
    with pytest.raises(RuntimeError):
        with logged_run(session, "demo"):
            raise RuntimeError("boom")
    with logged_run(session, "demo"):
        pass
    failed, ok = session.exec(select(RunLog).order_by(RunLog.id)).all()
    assert failed.failure_category == "internal_error"
    assert ok.failure_category is None


def test_logged_run_keeps_a_category_committed_before_the_failure(session):
    with pytest.raises(RuntimeError):
        with logged_run(session, "enrich") as run:
            run.failure_category = "refusal"
            session.add(run)
            session.commit()
            raise RuntimeError("boom")
    (row,) = session.exec(select(RunLog)).all()
    assert row.failure_category == "refusal"


def test_logged_run_drops_an_uncommitted_category_on_failure(session):
    with pytest.raises(RuntimeError):
        with logged_run(session, "enrich") as run:
            run.failure_category = "refusal"  # never committed: rolled back
            raise RuntimeError("boom")
    (row,) = session.exec(select(RunLog)).all()
    assert row.failure_category == "internal_error"


def test_run_log_step_is_unique_per_run_and_iteration(session):
    from sqlalchemy.exc import IntegrityError

    with logged_run(session, "enrich") as run:
        pass
    now = datetime.now(UTC)
    session.add(RunLogStep(run_log_id=run.id, iteration=1, created_at=now))
    session.add(RunLogStep(run_log_id=run.id, iteration=2, created_at=now))
    session.commit()
    session.add(RunLogStep(run_log_id=run.id, iteration=2, created_at=now))
    with pytest.raises(IntegrityError):
        session.commit()
