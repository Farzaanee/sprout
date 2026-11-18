import json
from datetime import UTC, date, datetime

import httpx
import pytest
from sqlmodel import Session, select

from fakes import ScriptedClient, server_search_turn, tool_use_turn
from sprout.agents import news as news_mod
from sprout.agents.common import AgentFailed, Deps
from sprout.agents.news import (
    MAX_NEWS_PER_COMPANY,
    NEWS_BUDGET,
    SYSTEM_PROMPT,
    collect_news,
)
from sprout.config import Settings
from sprout.db import (
    Company,
    CompanyNewsRow,
    Fact,
    RunKind,
    RunLog,
    get_engine,
    init_db,
    save_company_news,
)
from sprout.sources.web import WebFetcher

BULK = "claude-haiku-5-5"
NOW = datetime(2026, 11, 1, tzinfo=UTC)
FUNDING = "https://news.example/acme-ai-raises-series-a"
HIRING = "https://news.example/acme-ai-hires-a-cto"
SEARCHED = "https://search.example/acme-ai-acquires-widgets"


@pytest.fixture
def session(tmp_path):
    engine = get_engine(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def company(session):
    row = Company(company_number="12345678", name="ACME AI LTD", locality="London")
    session.add(row)
    session.commit()
    for field, value in (("website", "https://acme.example"), ("one_liner", "Acme builds LLMs")):
        session.add(
            Fact(
                company_id=row.id,
                field=field,
                value_json=json.dumps(value),
                source_url="https://acme.example/about",
                retrieved_at=NOW,
                confidence="high",
            )
        )
    session.commit()
    return row


@pytest.fixture
def deps_for(make_http):
    def build(client):
        def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(
                200, text="Acme AI acquires Widgets.", headers={"content-type": "text/html"}
            )

        fetcher = WebFetcher(make_http(handler), resolver=lambda h: ["1.1.1.1"])
        return Deps(client, fetcher, None, Settings(None, None, BULK, "strong"), BULK)

    return build


def entry(url, *, big=False, kind=None, title="Acme AI news", summary="Something happened."):
    return {
        "url": url,
        "title": title,
        "published": "2026-10-20",
        "big": big,
        "kind": kind,
        "summary": summary,
    }


def submit(*entries, **usage):
    return tool_use_turn(("submit_news", {"entries": list(entries)}), **usage)


def collect(session, company, client, deps_for, **kwargs):
    return collect_news(session, company, deps_for(client), **kwargs)


def news(session):
    return session.exec(select(CompanyNewsRow)).all()


# ---- the run ---------------------------------------------------------------------------------


def test_run_stores_news_and_records_stats(session, company, deps_for):
    client = ScriptedClient(
        server_search_turn(
            [FUNDING, HIRING, SEARCHED],
            (
                "submit_news",
                {
                    "entries": [
                        entry(HIRING, title="Acme AI hires a CTO"),
                        entry(FUNDING, big=True, kind="funding", summary="Raised US$20m. " * 40),
                        entry(SEARCHED, big=True, kind=None, title="Acme AI acquires Widgets"),
                    ]
                },
            ),
            input=2_000,
            output=300,
            searches=1,
        )
    )
    entries = collect(session, company, client, deps_for)

    assert [e.big for e in entries] == [True, True, False]  # big first, then the model's order
    rows = {r.url: r for r in news(session)}
    assert set(rows) == {FUNDING, HIRING, SEARCHED}
    assert all(r.company_id == company.id for r in rows.values())
    funding = rows[FUNDING]
    assert funding.news_kind == "funding" and len(funding.summary) == 300
    assert funding.published == date(2026, 10, 20)
    assert rows[SEARCHED].news_kind == "other"  # big without a kind
    assert rows[SEARCHED].title == "Acme AI acquires Widgets"
    assert rows[HIRING].news_kind is None

    (run,) = session.exec(
        select(RunLog).where(RunLog.kind == RunKind.COMPANY_NEWS_MATCH.value)
    ).all()
    assert run.status == "ok" and run.model == BULK and run.company_id == company.id
    assert run.max_iterations == NEWS_BUDGET.max_iterations and run.max_usd == 0.05
    assert run.stats["stored"] == 3 and run.stats["big"] == 2
    assert {s["url"] for s in run.stats["sources"]} == {FUNDING, HIRING, SEARCHED}


def test_request_has_one_search_the_allowed_tools_and_company_context(session, company, deps_for):
    client = ScriptedClient(submit())
    collect(session, company, client, deps_for)
    request = client.requests[0]
    assert request["system"][0]["text"] == SYSTEM_PROMPT
    assert [t["name"] for t in request["tools"]] == ["web_search", "fetch_page", "submit_news"]
    assert request["tools"][0]["max_uses"] == 1
    assert request["model"] == BULK
    text = request["messages"][0]["content"]
    assert "12345678" in text and "https://acme.example" in text and "Acme builds LLMs" in text
    assert "untrusted data" in SYSTEM_PROMPT and "NOT big" in SYSTEM_PROMPT


def test_rubric_limits_leadership_own_site_posts_and_events():
    assert "only for a CEO, founder or other C-suite change" in SYSTEM_PROMPT
    assert "board welcomes and advisory appointments are NOT big" in SYSTEM_PROMPT
    assert "own website is big only if it reports a funding round" in SYSTEM_PROMPT
    assert "partnership with a named third party" in SYSTEM_PROMPT
    assert "Events and conference listings are NOT big" in SYSTEM_PROMPT


def test_no_entries_stores_nothing(session, company, deps_for):
    client = ScriptedClient(submit())
    assert collect(session, company, client, deps_for) == []
    (run,) = session.exec(select(RunLog)).all()
    assert run.stats["stored"] == 0
    assert news(session) == []


def test_unreturned_url_is_rejected_then_dropped_after_the_repair(session, company, deps_for):
    invented = "https://invented.example/acme-ai-wins"
    client = ScriptedClient(
        server_search_turn(
            [FUNDING],
            (
                "submit_news",
                {"entries": [entry(FUNDING, big=True, kind="funding"), entry(invented)]},
            ),
        ),
        submit(entry(FUNDING, big=True, kind="funding"), entry(invented)),
    )
    entries = collect(session, company, client, deps_for)
    assert [str(e.url) for e in entries] == [FUNDING]
    feedback = json.dumps(client.requests[1]["messages"][-1])
    assert "was not returned by any tool" in feedback and invented in feedback
    assert [r.url for r in news(session)] == [FUNDING]
    (run,) = session.exec(select(RunLog)).all()
    assert run.repairs == 1


def test_a_tracking_param_still_matches_the_ledger_and_is_stored_normalised(
    session, company, deps_for
):
    client = ScriptedClient(
        server_search_turn(
            [FUNDING], ("submit_news", {"entries": [entry(FUNDING + "/?utm_source=x")]})
        )
    )
    (found,) = collect(session, company, client, deps_for)
    assert str(found.url) == FUNDING
    assert [r.url for r in news(session)] == [FUNDING]


def test_duplicate_urls_and_extra_entries_are_capped(session, company, deps_for):
    urls = [f"https://s.example/{i}" for i in range(8)]
    client = ScriptedClient(
        server_search_turn(
            urls,
            (
                "submit_news",
                {"entries": [entry(u, big=i == 7, kind="other") for i, u in enumerate(urls)]},
            ),
        ),
    )
    entries = collect(session, company, client, deps_for)
    assert len(entries) == MAX_NEWS_PER_COMPANY
    assert str(entries[0].url) == urls[7]  # the big one survives the cap


def test_rerunning_is_idempotent(session, company, deps_for):
    script = [entry(FUNDING, big=True, kind="funding"), entry(SEARCHED)]

    def run():
        client = ScriptedClient(
            server_search_turn([FUNDING, SEARCHED], ("submit_news", {"entries": script}))
        )
        collect(session, company, client, deps_for)

    def state():
        return [(r.url, r.news_kind, r.summary, r.first_seen_at) for r in news(session)]

    run()
    first = state()
    run()
    assert state() == first and len(first) == 2
    assert len(session.exec(select(RunLog)).all()) == 2


def test_save_company_news_upserts_on_company_and_url_and_never_deletes(session, company):
    def save(url, kind, summary, title="t"):
        save_company_news(
            session,
            company.id,
            url=url,
            title=title,
            published=None,
            news_kind=kind,
            summary=summary,
        )

    save(FUNDING, None, "first")
    save(HIRING, "funding", "b")
    save(FUNDING + "#top", "leadership", "second", title="newer")
    state = {r.url: (r.news_kind, r.summary, r.title) for r in news(session)}
    assert state == {FUNDING: ("leadership", "second", "newer"), HIRING: ("funding", "b", "t")}


def test_failed_run_raises_and_writes_nothing(session, company, deps_for):
    client = ScriptedClient(tool_use_turn(("fetch_page", {"url": FUNDING})))
    with pytest.raises(AgentFailed):
        collect_news(
            session,
            company,
            deps_for(client),
            budget=NEWS_BUDGET.__class__(1, NEWS_BUDGET.max_usd, 1),
        )
    assert news(session) == []
    (run,) = session.exec(select(RunLog)).all()
    assert run.failure_category is not None


def test_offering_a_tool_outside_the_allow_list_fails_before_any_run(
    session, company, deps_for, monkeypatch
):
    monkeypatch.setattr(news_mod, "ALLOWED_NEWS_TOOLS", frozenset({"submit_news"}))
    client = ScriptedClient()
    with pytest.raises(RuntimeError, match="ALLOWED_NEWS_TOOLS"):
        collect(session, company, client, deps_for)
    assert client.calls == 0
    assert session.exec(select(RunLog)).all() == []
