import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

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
    dedupe_near_duplicates,
    match_name,
    match_rss_candidates,
)
from sprout.config import Settings
from sprout.db import (
    Company,
    CompanyNewsRow,
    Fact,
    NewsItemRow,
    RunKind,
    RunLog,
    get_engine,
    init_db,
    link_company_news,
)
from sprout.sources.web import WebFetcher

BULK = "claude-haiku-5-5"
NOW = datetime(2026, 11, 1, tzinfo=UTC)
FUNDING = "https://news.example/acme-ai-raises-series-a"
HIRING = "https://news.example/acme-ai-hires-a-cto"
NAMESAKE = "https://news.example/acme-bakery-opens"
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


def add_item(session, url, title, *, summary="", published=date(2026, 10, 1)):
    row = NewsItemRow(url=url, title=title, summary=summary, published=published, source="UKTN")
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def feed(session):
    add_item(session, FUNDING, "Acme AI raises US$20m Series A", published=date(2026, 10, 20))
    add_item(session, HIRING, "Acme AI hires a new CTO from Google", published=date(2026, 9, 1))
    add_item(session, NAMESAKE, "Acme Bakery opens in Leeds, says Acme AI Hub partner")
    add_item(session, "https://news.example/other", "Unrelated: Beta Corp news")


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


def links(session):
    return session.exec(select(CompanyNewsRow)).all()


# ---- name normalisation and matching ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ACME AI LTD", "acme ai"),
        ("Acme Robotics Limited", "acme robotics"),
        ("Acme Holdings PLC", "acme holdings"),
        ("Acme & Sons (UK) LLP", "acme sons uk"),
        ("Foo Ltd Limited", "foo"),
        ("LTD", None),  # nothing but a suffix
        ("AI LTD", None),  # fewer than 3 characters
        ("DATA LIMITED", None),  # a single generic word
        ("Cloud", None),
        ("Data Labs Ltd", "data labs"),  # two words is specific enough
    ],
)
def test_match_name(name, expected):
    assert match_name(name) == expected


def test_match_requires_the_whole_name_as_whole_words(session, company, feed):
    add_item(session, "https://news.example/a", "Acme AIM fund launches")
    add_item(session, "https://news.example/b", "Superacme AI wins", published=date(2026, 10, 2))
    add_item(session, "https://news.example/c", "Trends", summary="ACME-AI's chatbot, reviewed")
    urls = {r.url for r in match_rss_candidates(session, company, now=NOW)}
    assert urls == {FUNDING, HIRING, "https://news.example/c"} | {NAMESAKE}
    # The namesake is only filtered out by the model: the matcher cannot tell.


def test_generic_company_names_match_nothing(session, feed):
    company = Company(company_number="99999999", name="DATA LTD")
    session.add(company)
    session.commit()
    add_item(session, "https://news.example/d", "Data breach hits retailer")
    assert match_rss_candidates(session, company, now=NOW) == []


def test_window_cutoff_uses_published_or_first_seen(session, company):
    add_item(
        session, "https://n.example/new", "Acme AI new", published=NOW.date() - timedelta(days=364)
    )
    add_item(
        session, "https://n.example/old", "Acme AI old", published=NOW.date() - timedelta(days=366)
    )
    undated = add_item(session, "https://n.example/undated", "Acme AI undated", published=None)
    undated.first_seen_at = NOW - timedelta(days=10)
    stale = add_item(session, "https://n.example/stale", "Acme AI stale", published=None)
    stale.first_seen_at = NOW - timedelta(days=400)
    session.commit()
    urls = [r.url for r in match_rss_candidates(session, company, now=NOW)]
    assert urls == ["https://n.example/undated", "https://n.example/new"]  # newest first
    short = match_rss_candidates(session, company, window_days=30, now=NOW)
    assert [r.url for r in short] == ["https://n.example/undated"]


# ---- near-duplicate collapse -----------------------------------------------------------------


def titled(*titles):
    return [SimpleNamespace(title=t) for t in titles]


def test_near_duplicate_titles_collapse_to_the_first():
    items = titled(
        "Acme AI raises 20m Series A led by Index",
        "Acme AI raises 20m Series A led by Index Ventures",  # syndicated copy
        "Acme AI raises 20m series a, led by INDEX!",  # case and punctuation only
        "Acme AI hires a new CTO",
    )
    kept = dedupe_near_duplicates(items)
    assert [i.title for i in kept] == [items[0].title, items[3].title]


def test_different_stories_and_empty_titles_are_kept():
    assert len(dedupe_near_duplicates(titled("Acme AI raises", "Acme AI acquires Widgets"))) == 2
    assert len(dedupe_near_duplicates(titled("!!!", "???"))) == 2  # no words: cannot compare
    assert dedupe_near_duplicates([]) == []


def test_threshold_is_adjustable():
    items = titled("a b c d", "a b c e")  # Jaccard 3/5
    assert len(dedupe_near_duplicates(items)) == 2
    assert len(dedupe_near_duplicates(items, threshold=0.6)) == 1


# ---- the run ---------------------------------------------------------------------------------


def test_run_stores_links_and_records_stats(session, company, feed, deps_for):
    client = ScriptedClient(
        server_search_turn(
            [SEARCHED],
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
    rows = {r.url: r for r in session.exec(select(NewsItemRow)).all()}
    searched = rows[SEARCHED]
    assert (searched.source, searched.summary, searched.title) == (
        "web_search",
        "",
        "Acme AI acquires Widgets",
    )
    assert rows[FUNDING].source == "UKTN"  # an RSS row is linked, not rewritten

    by_item = {link.news_item_id: link for link in links(session)}
    assert {rows[u].id for u in (FUNDING, HIRING, SEARCHED)} == set(by_item)
    funding = by_item[rows[FUNDING].id]
    assert funding.news_kind == "funding" and len(funding.summary) == 300
    assert by_item[rows[SEARCHED].id].news_kind == "other"  # big without a kind
    hiring = by_item[rows[HIRING].id]
    assert hiring.news_kind is None and hiring.company_id == company.id

    (run,) = session.exec(
        select(RunLog).where(RunLog.kind == RunKind.COMPANY_NEWS_MATCH.value)
    ).all()
    assert run.status == "ok" and run.model == BULK and run.company_id == company.id
    assert run.max_iterations == NEWS_BUDGET.max_iterations and run.max_usd == 0.05
    assert run.stats["rss_candidates"] == 3 and run.stats["stored"] == 3 and run.stats["big"] == 2
    seeded = {s["url"]: s["fetched"] for s in run.stats["sources"]}
    assert seeded[FUNDING] is False and seeded[SEARCHED] is False


def test_request_has_one_search_the_allowed_tools_and_untrusted_candidates(
    session, company, feed, deps_for
):
    client = ScriptedClient(submit())
    collect(session, company, client, deps_for)
    request = client.requests[0]
    assert request["system"][0]["text"] == SYSTEM_PROMPT
    assert [t["name"] for t in request["tools"]] == ["web_search", "fetch_page", "submit_news"]
    assert request["tools"][0]["max_uses"] == 1
    assert request["model"] == BULK
    text = request["messages"][0]["content"]
    assert "12345678" in text and "https://acme.example" in text and "Acme builds LLMs" in text
    assert f'<untrusted_content source="{FUNDING}">' in text
    assert "Beta Corp" not in text  # not a candidate
    assert "untrusted data" in SYSTEM_PROMPT and "NOT big" in SYSTEM_PROMPT


def test_without_candidates_the_message_says_to_search(session, company, deps_for):
    client = ScriptedClient(submit())
    assert collect(session, company, client, deps_for) == []
    assert "no candidate articles" in client.requests[0]["messages"][0]["content"]
    (run,) = session.exec(select(RunLog)).all()
    assert run.stats["rss_candidates"] == 0 and run.stats["stored"] == 0
    assert links(session) == []


def test_near_duplicate_candidates_are_shown_once(session, company, deps_for):
    add_item(session, "https://a.example/1", "Acme AI raises 20m Series A")
    add_item(session, "https://b.example/1", "Acme AI raises 20m Series A")
    client = ScriptedClient(submit())
    collect(session, company, client, deps_for)
    (run,) = session.exec(select(RunLog)).all()
    assert run.stats["rss_candidates"] == 1


def test_unreturned_url_is_rejected_then_dropped_after_the_repair(session, company, feed, deps_for):
    invented = "https://invented.example/acme-ai-wins"
    client = ScriptedClient(
        submit(entry(FUNDING, big=True, kind="funding"), entry(invented)),
        submit(entry(FUNDING, big=True, kind="funding"), entry(invented)),
    )
    entries = collect(session, company, client, deps_for)
    assert [str(e.url) for e in entries] == [FUNDING]
    feedback = json.dumps(client.requests[1]["messages"][-1])
    assert "was not returned by any tool" in feedback and invented in feedback
    assert session.exec(select(NewsItemRow).where(NewsItemRow.url == invented)).first() is None
    assert len(links(session)) == 1
    (run,) = session.exec(select(RunLog)).all()
    assert run.repairs == 1


def test_a_trailing_slash_or_tracking_param_still_matches_the_ledger(
    session, company, feed, deps_for
):
    client = ScriptedClient(submit(entry(FUNDING + "/?utm_source=x")))
    (found,) = collect(session, company, client, deps_for)
    assert str(found.url) == FUNDING
    assert len(session.exec(select(NewsItemRow)).all()) == 4  # no duplicate row
    assert len(links(session)) == 1


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


def test_rerunning_is_idempotent(session, company, feed, deps_for):
    script = [entry(FUNDING, big=True, kind="funding"), entry(SEARCHED)]

    def run():
        client = ScriptedClient(
            server_search_turn([SEARCHED], ("submit_news", {"entries": script}))
        )
        collect(session, company, client, deps_for)

    run()
    snapshot = (
        len(session.exec(select(NewsItemRow)).all()),
        [(link.news_item_id, link.news_kind, link.summary) for link in links(session)],
    )
    run()
    assert snapshot == (
        len(session.exec(select(NewsItemRow)).all()),
        [(link.news_item_id, link.news_kind, link.summary) for link in links(session)],
    )
    assert len(session.exec(select(RunLog)).all()) == 2


def test_link_company_news_upserts_and_never_deletes(session, company, feed):
    item_a, item_b = session.exec(select(NewsItemRow.id).limit(2)).all()
    link_company_news(session, company.id, item_a, news_kind=None, summary="first")
    link_company_news(session, company.id, item_b, news_kind="funding", summary="b")
    link_company_news(session, company.id, item_a, news_kind="leadership", summary="second")
    state = {link.news_item_id: (link.news_kind, link.summary) for link in links(session)}
    assert state == {item_a: ("leadership", "second"), item_b: ("funding", "b")}


def test_failed_run_raises_and_writes_nothing(session, company, feed, deps_for):
    client = ScriptedClient(tool_use_turn(("fetch_page", {"url": FUNDING})))
    with pytest.raises(AgentFailed):
        collect_news(
            session,
            company,
            deps_for(client),
            budget=NEWS_BUDGET.__class__(1, NEWS_BUDGET.max_usd, 1),
        )
    assert links(session) == []
    (run,) = session.exec(select(RunLog)).all()
    assert run.failure_category is not None


def test_offering_a_tool_outside_the_allow_list_fails_before_any_run(
    session, company, feed, deps_for, monkeypatch
):
    monkeypatch.setattr(news_mod, "ALLOWED_NEWS_TOOLS", frozenset({"submit_news"}))
    client = ScriptedClient()
    with pytest.raises(RuntimeError, match="ALLOWED_NEWS_TOOLS"):
        collect(session, company, client, deps_for)
    assert client.calls == 0
    assert session.exec(select(RunLog)).all() == []
