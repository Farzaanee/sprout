from datetime import date

import httpx
import pytest
from sqlmodel import Session, func, select

from sprout import config, ingest
from sprout.db import Company, Fact, NewsItemRow, RunLog, get_engine, init_db
from sprout.sources.companies_house import CompaniesHouseClient
from sprout.sources.rss import Feed

ITEMS = [
    {
        "company_name": "ACME MACHINE LEARNING LTD",
        "company_number": "00000001",
        "company_status": "active",
        "date_of_creation": "2019-03-04",
        "sic_codes": ["62012"],
        "registered_office_address": {"locality": "London"},
    },
    {
        "company_name": "BETA ROBOTICS LTD",
        "company_number": "00000002",
        "company_status": "active",
        "date_of_creation": "2021-05-06",
        "sic_codes": ["28990"],
    },
    {  # matches the search term but is not an AI company
        "company_name": "AI RESTAURANT LTD",
        "company_number": "00000003",
        "company_status": "active",
        "date_of_creation": "2022-01-01",
        "sic_codes": ["56101"],
    },
]


@pytest.fixture
def session(tmp_path):
    engine = get_engine(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def rss(n: int) -> str:
    items = "".join(
        f"<item><title>Story {i}</title><link>https://n.example/{i}</link></item>" for i in range(n)
    )
    return (
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{items}</channel></rss>'
    )


def test_ingest_companies_filters_dedupes_and_records_facts(session, make_http):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.params["company_name_includes"])
        return httpx.Response(200, json={"items": ITEMS})

    client = CompaniesHouseClient(make_http(handler))
    terms = ["machine learning", "robotics", "ai"]
    stats = ingest.ingest_companies(session, client, terms=terms, max_per_term=100)
    assert stats == {"searched": 9, "new": 2, "updated": 0, "rejected": 1}

    names = sorted(session.exec(select(Company.name)).all())
    assert names == ["ACME MACHINE LEARNING LTD", "BETA ROBOTICS LTD"]
    facts = {(f.field, f.value_json, f.source_url) for f in session.exec(select(Fact)).all()}
    assert (
        "hq",
        '"London"',
        "https://find-and-update.company-information.service.gov.uk/company/00000001",
    ) in facts
    assert any(f[0] == "founded" and f[1] == "2019" for f in facts)

    # A second run changes nothing and makes no HTTP calls: every response is cached.
    before = len(requests)
    again = ingest.ingest_companies(session, client, terms=terms, max_per_term=100)
    assert again["new"] == 0 and again["updated"] == 2
    assert len(requests) == before
    assert session.exec(select(func.count()).select_from(Company)).one() == 2
    assert session.exec(select(func.count()).select_from(Fact)).one() == len(facts)


def test_ingest_news_dedupes_and_tolerates_a_broken_feed(session, make_http):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "bad.example":
            return httpx.Response(404)
        return httpx.Response(200, text=rss(3))

    http = make_http(handler)
    feeds = [
        Feed("A", "https://a.example/feed"),
        Feed("B", "https://b.example/feed"),  # carries the same story URLs as A
        Feed("Bad", "https://bad.example/feed"),
    ]
    stats = ingest.ingest_news(session, http, feeds)
    assert stats["fetched"] == 6
    assert stats["new"] == 3  # both feeds carry the same story URLs
    assert len(stats["failed_feeds"]) == 1 and "Bad" in stats["failed_feeds"][0]

    before = len(calls)
    assert ingest.ingest_news(session, http, feeds)["new"] == 0
    assert session.exec(select(func.count()).select_from(NewsItemRow)).one() == 3
    assert len(calls) == before  # every response, including the 404, is cached


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    monkeypatch.setenv("SPROUT_DB_PATH", str(tmp_path / "out" / "sprout.db"))
    monkeypatch.setenv("SPROUT_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


def test_main_reports_missing_companies_house_key(isolated_settings, capsys):
    assert ingest.main(["--skip-news"]) == 1
    captured = capsys.readouterr()
    assert "COMPANIES_HOUSE_API_KEY" in captured.err
    assert "0 candidate companies, 0 news items" in captured.out


def test_main_logs_failed_runs(isolated_settings, monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "bad-key")

    def reject(self, *args, **kwargs):
        raise ingest.FetchError("GET returned 401", status_code=401)

    monkeypatch.setattr(ingest.CompaniesHouseClient, "iter_advanced_search", reject)
    assert ingest.main(["--skip-news"]) == 1
    with Session(get_engine(isolated_settings / "out" / "sprout.db")) as s:
        run = s.exec(select(RunLog)).one()
    assert (run.kind, run.status) == ("ingest_companies", "error")
    assert "401" in run.error


def test_date_floor_is_passed_to_companies_house(session, make_http):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["incorporated_from"])
        return httpx.Response(200, json={"items": []})

    ingest.ingest_companies(
        session, CompaniesHouseClient(make_http(handler)), terms=["ai"], max_per_term=10
    )
    assert seen == [date(2012, 1, 1).isoformat()]
