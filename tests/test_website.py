from types import SimpleNamespace

import httpx
import pytest
from sqlmodel import Session, select

from fakes import ScriptedClient, response, server_search_turn, tool_use_block, tool_use_turn
from sprout.agents.common import Deps
from sprout.agents.website import WebsiteScreenFailed, screen_website
from sprout.config import Settings
from sprout.db import Company, Fact, RunKind, RunLog, get_engine, init_db
from sprout.sources.web import WebFetcher

URL = "https://acme.example/about"
MODEL = "claude-haiku-5-5"
QUOTE = "ACME AI LTD is registered in England"


@pytest.fixture
def resources(tmp_path, make_http):
    engine = get_engine(tmp_path / "test.db")
    init_db(engine)
    session = Session(engine)
    company = Company(name="ACME AI LTD", company_number="12345678")
    session.add(company)
    session.commit()

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=QUOTE, headers={"content-type": "text/html"})

    fetcher = WebFetcher(make_http(handler), resolver=lambda _: ["1.1.1.1"])
    yield session, company, fetcher
    session.close()


def run(resources, client):
    session, company, fetcher = resources
    deps = Deps(client, fetcher, None, Settings(None, None, MODEL, MODEL), MODEL)
    return screen_website(session, company, deps)


def submit(url=URL, quote=QUOTE, reason="Official company page"):
    return tool_use_turn(("submit_website", {"url": url, "quote": quote, "reason": reason}))


def test_fetched_website_is_saved_with_provenance(resources):
    client = ScriptedClient(server_search_turn([URL], ("fetch_page", {"url": URL})), submit())
    result = run(resources, client)
    session, company, _ = resources
    fact = session.exec(select(Fact).where(Fact.company_id == company.id)).one()
    record = session.exec(select(RunLog).where(RunLog.kind == RunKind.COMPANY_WEBSITE_SCREEN)).one()
    assert result.payload["url"] == URL
    assert fact.value_json == f'"{URL}"' and fact.source_url == URL
    assert QUOTE in fact.note
    assert record.status == "ok" and record.stats["website"] == URL
    assert record.stats["quote"] == QUOTE


def test_no_website_is_recorded_as_a_skip_not_a_profile(resources):
    result = run(
        resources,
        ScriptedClient(server_search_turn([], ("submit_website", {
            "url": None, "quote": None, "reason": "No official site found",
        }))),
    )
    session, _, _ = resources
    assert result.payload["url"] == ""
    assert session.exec(select(Fact)).all() == []
    assert session.exec(select(RunLog)).one().stats["reason"] == "No official site found"


def test_missing_search_is_a_screen_failure_not_a_negative_result(resources):
    with pytest.raises(WebsiteScreenFailed, match="did not complete"):
        run(resources, ScriptedClient(submit(None, None, "No site")))
    session, _, _ = resources
    assert session.exec(select(RunLog)).one().status == "error"


def test_fetch_failure_is_a_screen_failure_not_a_negative_result(resources):
    session, _, fetcher = resources
    client = ScriptedClient(
        server_search_turn([URL], ("fetch_page", {"url": "http://localhost/private"})),
        submit(None, None, "No site"),
    )
    with pytest.raises(WebsiteScreenFailed, match="did not complete"):
        run((session, resources[1], fetcher), client)
    assert session.exec(select(RunLog)).one().status == "error"


def test_search_error_is_a_screen_failure_not_a_negative_result(resources):
    error = SimpleNamespace(type="web_search_tool_result", content={"error_code": "unavailable"})
    client = ScriptedClient(response([
        error, tool_use_block("submit_website", {"url": None, "quote": None, "reason": "No site"})
    ], "tool_use"))
    with pytest.raises(WebsiteScreenFailed, match="did not complete"):
        run(resources, client)
    session, _, _ = resources
    log = session.exec(select(RunLog)).one()
    assert log.status == "error" and log.failure_category == "api_error"
    assert log.stats["search_errors"] == 1


@pytest.mark.parametrize(
    "bad",
    [
        submit("https://invented.example", QUOTE),
        submit(URL, "invented company evidence"),
        submit(URL, URL),
    ],
)
def test_unfetched_url_or_invented_quote_is_rejected(resources, bad):
    client = ScriptedClient(
        server_search_turn([URL], ("fetch_page", {"url": URL})), bad, bad
    )
    with pytest.raises(WebsiteScreenFailed, match="invalid"):
        run(resources, client)
    session, _, _ = resources
    assert session.exec(select(Fact)).all() == []
    assert session.exec(select(RunLog)).one().status == "error"
