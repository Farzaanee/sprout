from datetime import date
from decimal import Decimal

import anthropic
import httpx
import pytest
from sqlmodel import Session, select

from fakes import ScriptedClient, response, server_search_turn, text_turn, tool_use_turn
from sprout import enrich as enrich_cli
from sprout.agents import enrich as enrich_mod
from sprout.agents.enrich import (
    CompanyNotFound,
    Deps,
    EnrichmentFailed,
    SubmissionError,
    enrich_company,
    resolve_company,
    submit_profile_schema,
    validate_submission,
)
from sprout.agents.loop import Budget
from sprout.agents.tools import SourceLedger
from sprout.config import Settings
from sprout.db import Company, Fact, RunLog, RunLogStep, get_engine, init_db
from sprout.models import Category, Confidence
from sprout.sources.companies_house import CompaniesHouseClient
from sprout.sources.web import WebFetcher

SEEN = "https://acme.example/about"
FUNDING_URL = "https://news.example/acme-raises"
MODEL = "claude-haiku-5-5"
NOT_DISCLOSED = {"value": None, "source_url": None, "confidence": "low", "note": None}


def fact(value, url=SEEN, confidence="high", note=None):
    return {"value": value, "source_url": url, "confidence": confidence, "note": note}


def submission(**overrides):
    base = {
        "website": fact("https://acme.example"),
        "hq": fact("London, UK"),
        "founded": NOT_DISCLOSED,
        "category": "AI models and applications",
        "stage": NOT_DISCLOSED,
        "total_raised_million_usd": NOT_DISCLOSED,
        "valuation_million_usd": NOT_DISCLOSED,
        "funding_rounds": [],
        "business_model": NOT_DISCLOSED,
        "target_user": NOT_DISCLOSED,
        "one_liner": fact("Builds AI agents."),
    }
    return {**base, **overrides}


def empty_submission():
    return submission(website=NOT_DISCLOSED, hq=NOT_DISCLOSED, one_liner=NOT_DISCLOSED)


@pytest.fixture
def session(tmp_path):
    engine = get_engine(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def company(session):
    row = Company(
        company_number="12345678",
        name="ACME AI LTD",
        status="active",
        incorporated=date(2019, 3, 4),
        sic_codes=["62012"],
        locality="London",
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def deps_for(make_http):
    def build(client, with_ch=False):
        fetcher = WebFetcher(
            make_http(lambda r: httpx.Response(404)), resolver=lambda h: ["1.1.1.1"]
        )
        ch = CompaniesHouseClient(make_http(lambda r: httpx.Response(404))) if with_ch else None
        settings = Settings(None, None, MODEL, "claude-sonnet-5-5")
        return Deps(client, fetcher, ch, settings, MODEL)

    return build


def ledger_with(*urls, fetched=True):
    ledger = SourceLedger()
    for url in urls:
        ledger.add(url, fetched=fetched)
    return ledger


def facts_of(session, company):
    return {f.field: f for f in session.exec(select(Fact).where(Fact.company_id == company.id))}


# ---- submit_profile schema and validation ---------------------------------------------------


def test_submit_schema_is_derived_from_the_model_without_code_stamped_fields():
    schema = submit_profile_schema()
    assert set(schema["properties"]) == {
        "website", "hq", "founded", "category", "stage",
        "total_raised_million_usd", "valuation_million_usd", "funding_rounds", "business_model",
        "target_user", "one_liner",
    }  # fmt: skip
    assert set(schema["required"]) == set(schema["properties"])
    text = str(schema)
    assert "retrieved_at" not in text and "$ref" not in text and "$defs" not in text
    rounds = schema["properties"]["funding_rounds"]
    assert rounds["type"] == "array"
    assert rounds["items"]["required"] == ["value", "source_url", "confidence", "note"]
    assert schema["properties"]["category"]["enum"] == [c.value for c in Category]
    assert schema["properties"]["hq"]["required"] == ["value", "source_url", "confidence", "note"]


def test_valid_submission_gets_name_and_ledger_time_stamped():
    ledger = ledger_with(SEEN)
    profile = validate_submission(submission(), "ACME AI LTD", ledger)
    assert profile.name == "ACME AI LTD" and profile.hq.value == "London, UK"
    assert profile.hq.retrieved_at == ledger.get(SEEN).first_seen_at
    assert profile.founded.value is None and profile.category is Category.AI_MODELS_AND_APPLICATIONS


def test_model_supplied_retrieved_at_and_news_are_ignored():
    ledger = ledger_with(SEEN)
    raw = submission(news=[{"title": "x", "url": "https://x.example"}])
    raw["hq"] = {**raw["hq"], "retrieved_at": "1999-01-01T00:00:00Z"}
    profile = validate_submission(raw, "ACME", ledger)
    assert profile.news == [] and profile.hq.retrieved_at.year != 1999


def test_invented_source_url_is_rejected_and_the_error_lists_citable_urls():
    with pytest.raises(SubmissionError) as err:
        validate_submission(
            submission(hq=fact("London", url="https://invented.example/x")),
            "ACME",
            ledger_with(SEEN),
        )
    message = str(err.value)
    assert "hq: source_url https://invented.example/x was not returned by any tool" in message
    assert SEEN in message


def test_trailing_slash_difference_is_not_an_invented_url():
    ledger = ledger_with("https://acme.example", SEEN)
    raw = submission(website=fact("https://acme.example", url="https://acme.example/"))
    assert validate_submission(raw, "ACME", ledger).website.value == "https://acme.example"


def test_funding_value_without_note_is_rejected_and_with_note_accepted():
    ledger = ledger_with(SEEN, FUNDING_URL)
    raw = submission(total_raised_million_usd=fact(12, FUNDING_URL, "medium"))
    with pytest.raises(
        SubmissionError, match="total_raised_million_usd: a funding value needs a note"
    ):
        validate_submission(raw, "ACME", ledger)
    raw["total_raised_million_usd"]["note"] = "Reported as GBP 9m, converted at about 1.25"
    assert validate_submission(raw, "ACME", ledger).total_raised_million_usd.value == 12


SEED = {"stage": "Seed", "amount_million_usd": 2, "announced": "2024-05-01", "investors": []}
SERIES_A = {**SEED, "stage": "Series A", "amount_million_usd": 9}


def test_each_funding_round_needs_its_own_note():
    raw = submission(
        funding_rounds=[fact(SEED, FUNDING_URL, "medium", "GBP 1.5m"), fact(SERIES_A, SEEN)]
    )
    with pytest.raises(SubmissionError, match=r"funding_rounds\[1\]: a funding value needs a note"):
        validate_submission(raw, "ACME", ledger_with(SEEN, FUNDING_URL))
    raw["funding_rounds"][1]["note"] = "US$9m"
    assert len(validate_submission(raw, "ACME", ledger_with(SEEN, FUNDING_URL)).funding_rounds) == 2


def test_funding_round_with_an_uncited_url_is_rejected_per_round():
    raw = submission(
        funding_rounds=[
            fact(SEED, FUNDING_URL, "medium", "GBP 1.5m"),
            fact(SERIES_A, "https://made.up/round", "medium", "US$9m"),
        ]
    )
    with pytest.raises(SubmissionError) as err:
        validate_submission(raw, "ACME", ledger_with(FUNDING_URL))
    assert r"funding_rounds[1]: source_url https://made.up/round" in str(err.value)
    assert r"funding_rounds[0]" not in str(err.value)


def test_funding_rounds_come_from_different_sources_with_ledger_time_and_downgrade():
    ledger = SourceLedger()
    ledger.add(FUNDING_URL, fetched=True)
    ledger.add(SEEN, fetched=False)
    raw = submission(
        funding_rounds=[
            fact(SEED, FUNDING_URL, "high", "GBP 1.5m"),
            fact(SERIES_A, SEEN, "high", "US$9m"),
        ]
    )
    seed, series_a = validate_submission(raw, "ACME", ledger).funding_rounds
    assert (seed.confidence, series_a.confidence) == (Confidence.HIGH, Confidence.MEDIUM)
    assert seed.retrieved_at == ledger.get(FUNDING_URL).first_seen_at
    assert str(series_a.source_url) == SEEN


def test_search_only_source_cannot_claim_high_confidence():
    ledger = ledger_with(SEEN, fetched=False)
    profile = validate_submission(submission(), "ACME", ledger)
    assert profile.hq.confidence is Confidence.MEDIUM


def test_schema_errors_are_compact_field_paths_without_echoing_input():
    raw = submission(founded=fact(1500), one_liner=fact("secret-ish text " * 50))
    raw["one_liner"]["confidence"] = "certain"
    with pytest.raises(SubmissionError) as err:
        validate_submission(raw, "ACME", ledger_with(SEEN))
    message = str(err.value)
    assert "founded.value" in message and "one_liner.confidence" in message
    assert "secret-ish" not in message and "input_value" not in message


def test_unknown_field_is_rejected():
    with pytest.raises(SubmissionError, match="surprise"):
        validate_submission({**submission(), "surprise": 1}, "ACME", ledger_with(SEEN))


# ---- enrich_company end to end (scripted model) ---------------------------------------------


def runs(session):
    return session.exec(select(RunLog).where(RunLog.kind == "enrich")).all()


def test_happy_path_persists_facts_category_and_run_stats(session, company, deps_for):
    client = ScriptedClient(
        server_search_turn(
            [SEEN],
            ("submit_profile", submission()),
            input=1200,
            output=300,
            cache_write=4000,
            searches=1,
        )
    )
    profile = enrich_company(session, company, deps_for(client))
    session.commit()

    assert profile.hq.value == "London, UK"
    session.refresh(company)
    assert company.category == "AI models and applications"
    stored = facts_of(session, company)
    assert stored["hq"].value_json == '"London, UK"' and stored["hq"].source_url == SEEN
    assert stored["founded"].value_json is None  # researched, nothing found
    (run,) = runs(session)
    assert run.status == "ok"
    stats = run.stats
    assert stats["stop"] == "submitted" and stats["company_number"] == "12345678"
    assert (stats["input_tokens"], stats["output_tokens"]) == (1200, 300)
    assert (stats["cache_write_tokens"], stats["web_searches"]) == (4000, 1)
    assert stats["tool_calls"] == {"submit_profile": 1}
    assert stats["cost_usd"] > 0
    assert stats["budget"] == {"max_iterations": 12, "max_usd": 0.13}
    first = client.requests[0]
    assert [t.get("name") for t in first["tools"]] == [
        "web_search",
        "fetch_page",
        "submit_profile",
    ]  # no CH client given, so no lookup tool
    assert "12345678" in first["messages"][0]["content"]


def test_companies_house_tool_only_offered_when_company_has_a_number(session, deps_for):
    nameless = Company(name="Numberless Ltd")
    session.add(nameless)
    session.commit()
    client = ScriptedClient(tool_use_turn(("submit_profile", empty_submission())))
    enrich_company(session, nameless, deps_for(client, with_ch=True))
    names = [t["name"] for t in client.requests[0]["tools"]]
    assert "lookup_companies_house" not in names


def test_repair_path_accepts_the_second_submission(session, company, deps_for):
    bad = submission(hq=fact("London", url="https://invented.example/x"))
    client = ScriptedClient(
        server_search_turn([SEEN], ("submit_profile", bad)),
        tool_use_turn(("submit_profile", submission())),
    )
    enrich_company(session, company, deps_for(client))
    (run,) = runs(session)
    assert run.stats["repairs"] == 1 and run.stats["iterations"] == 2
    feedback = client.requests[1]["messages"][-1]["content"][0]
    assert feedback["is_error"] and "invented.example" in feedback["content"]


def test_invalid_after_repair_writes_nothing_but_records_the_cost(session, company, deps_for):
    bad = submission(hq=fact("London", url="https://invented.example/x"))
    client = ScriptedClient(
        server_search_turn([SEEN], ("submit_profile", bad), input=1000, output=100),
        tool_use_turn(("submit_profile", bad), input=1000, output=100),
    )
    with pytest.raises(EnrichmentFailed) as err:
        enrich_company(session, company, deps_for(client))
    assert err.value.stop == "invalid" and err.value.cost_usd > 0
    session.refresh(company)
    assert company.category is None
    assert facts_of(session, company) == {}
    (run,) = runs(session)
    assert run.status == "error" and "invalid" in run.error
    assert run.stats["stop"] == "invalid"
    assert run.stats["cost_usd"] > 0
    assert run.stats["input_tokens"] == 2000


def test_funding_without_note_goes_through_repair(session, company, deps_for):
    unnoted = submission(total_raised_million_usd=fact(6, FUNDING_URL, "medium"))
    noted = submission(total_raised_million_usd=fact(6, FUNDING_URL, "medium", "Reported as £5m"))
    client = ScriptedClient(
        server_search_turn([SEEN, FUNDING_URL], ("submit_profile", unnoted)),
        tool_use_turn(("submit_profile", noted)),
    )
    enrich_company(session, company, deps_for(client))
    stored = facts_of(session, company)
    assert stored["total_raised_million_usd"].note == "Reported as £5m"


def test_tiny_usd_cap_stops_the_loop_and_logs_the_spend(session, company, deps_for):
    client = ScriptedClient(
        *[tool_use_turn(("fetch_page", {"url": "https://x.example"}), input=5000) for _ in range(5)]
    )
    budget = Budget(max_iterations=12, max_usd=Decimal("0.000001"))
    with pytest.raises(EnrichmentFailed) as err:
        enrich_company(session, company, deps_for(client), budget)
    assert err.value.stop == "budget" and client.calls == 1
    (run,) = runs(session)
    assert run.stats["stop"] == "budget" and run.stats["iterations"] == 1
    assert run.stats["input_tokens"] == 5000 and run.stats["cost_usd"] > 0


def test_tiny_iteration_cap(session, company, deps_for):
    client = ScriptedClient(
        *[tool_use_turn(("fetch_page", {"url": "https://x.example"})) for _ in range(5)]
    )
    with pytest.raises(EnrichmentFailed) as err:
        enrich_company(session, company, deps_for(client), Budget(2, Decimal("1")))
    assert err.value.stop == "max_iter" and client.calls == 2
    assert runs(session)[0].stats["tool_calls"] == {"fetch_page": 2}


def test_end_turn_without_submission_fails_cleanly(session, company, deps_for):
    client = ScriptedClient(text_turn("all done"), text_turn("really done"))
    with pytest.raises(EnrichmentFailed) as err:
        enrich_company(session, company, deps_for(client))
    assert err.value.stop == "no_submit"
    assert facts_of(session, company) == {}


def test_rerun_overwrites_same_source_facts(session, company, deps_for):
    for hq in ("London, UK", "Leeds, UK"):
        client = ScriptedClient(
            server_search_turn([SEEN], ("submit_profile", submission(hq=fact(hq))))
        )
        enrich_company(session, company, deps_for(client))
    rows = session.exec(select(Fact).where(Fact.field == "hq")).all()
    assert [r.value_json for r in rows] == ['"Leeds, UK"']


# ---- observability: steps and failure category ---------------------------------------------


def steps_of(session, run):
    return session.exec(
        select(RunLogStep).where(RunLogStep.run_log_id == run.id).order_by(RunLogStep.iteration)
    ).all()


def test_success_persists_one_step_per_response_and_no_failure_category(session, company, deps_for):
    client = ScriptedClient(
        server_search_turn(
            [SEEN],
            ("fetch_page", {"url": SEEN}),
            input=1000,
            output=100,
            cache_write=3000,
            searches=1,
        ),  # fmt: skip
        tool_use_turn(("submit_profile", submission()), input=200, output=400, cache_read=3000),
    )
    enrich_company(session, company, deps_for(client))
    (run,) = runs(session)
    assert run.status == "ok" and run.failure_category is None
    assert run.stats["error_type"] is None
    first, second = steps_of(session, run)
    assert (first.iteration, first.tool_name, first.stop_reason) == (
        1, "web_search,fetch_page", "tool_use",
    )  # fmt: skip
    assert (first.input_tokens, first.output_tokens) == (1000, 100)
    assert (first.cache_write_tokens, first.web_searches, first.tool_errors) == (3000, 1, 1)
    assert (second.iteration, second.tool_name) == (2, "submit_profile")
    assert (second.cache_read_tokens, second.web_searches, second.tool_errors) == (3000, 0, 0)
    assert first.created_at <= second.created_at
    assert sum(s.cost_usd for s in (first, second)) == pytest.approx(
        run.stats["cost_usd"], abs=1e-5
    )


def test_a_step_without_tools_has_no_tool_name_and_keeps_its_text(session, company, deps_for):
    client = ScriptedClient(text_turn("thinking aloud"), text_turn("still nothing"))
    with pytest.raises(EnrichmentFailed):
        enrich_company(session, company, deps_for(client))
    (run,) = runs(session)
    steps = steps_of(session, run)
    assert [s.tool_name for s in steps] == [None, None]
    assert [s.response_text for s in steps] == ["thinking aloud", "still nothing"]
    assert [s.stop_reason for s in steps] == ["end_turn", "end_turn"]


def bad_submission():
    return submission(hq=fact("London", url="https://invented.example/x"))


def fetch_turns(count):
    return [tool_use_turn(("fetch_page", {"url": "https://x.example"})) for _ in range(count)]


API_ERROR = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x.example"))


@pytest.mark.parametrize(
    ("script", "budget", "category", "iterations"),
    [
        (
            [server_search_turn([SEEN], ("submit_profile", bad_submission())),
             tool_use_turn(("submit_profile", bad_submission()))],
            Budget(12, Decimal("1")), "invalid_output", 2,
        ),
        (fetch_turns(3), Budget(2, Decimal("1")), "iteration_cap", 2),
        (
            [tool_use_turn(("fetch_page", {"url": "https://x.example"}), input=5000)],
            Budget(12, Decimal("0.000001")), "budget_cap", 1,
        ),
        ([text_turn(), text_turn()], Budget(12, Decimal("1")), "no_submission", 2),
        ([response([], "max_tokens", input=10)], Budget(12, Decimal("1")), "output_truncated", 1),
        ([response([], "refusal", input=10)], Budget(12, Decimal("1")), "refusal", 1),
        ([tool_use_turn(("fetch_page", {"url": "https://x.example"}), input=100), API_ERROR],
         Budget(12, Decimal("1")), "api_error", 1),
    ],
    ids=["invalid", "max_iter", "budget", "no_submit", "truncated", "refused", "api_error"],
)  # fmt: skip
def test_every_non_success_stop_commits_its_category_and_steps(
    session, company, deps_for, script, budget, category, iterations
):
    with pytest.raises(EnrichmentFailed):
        enrich_company(session, company, deps_for(ScriptedClient(*script)), budget)
    session.expire_all()  # read what was committed, not what the session holds in memory
    (run,) = runs(session)
    assert run.status == "error" and run.failure_category == category
    steps = steps_of(session, run)
    assert len(steps) == run.stats["iterations"] == iterations
    assert [s.iteration for s in steps] == list(range(1, iterations + 1))


def test_api_error_records_the_exception_class_in_stats(session, company, deps_for):
    client = ScriptedClient(API_ERROR)
    with pytest.raises(EnrichmentFailed):
        enrich_company(session, company, deps_for(client))
    (run,) = runs(session)
    assert run.failure_category == "api_error"
    assert run.stats["error_type"] == "APIConnectionError"
    assert steps_of(session, run) == []  # a failed call produced no response, so no step


def test_save_profile_failure_after_submission_is_internal_error_with_steps_kept(
    session, company, deps_for, monkeypatch
):
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(enrich_mod, "save_profile", boom)
    client = ScriptedClient(server_search_turn([SEEN], ("submit_profile", submission())))
    with pytest.raises(RuntimeError, match="disk full"):
        enrich_company(session, company, deps_for(client))
    session.expire_all()
    (run,) = runs(session)
    assert run.status == "error" and run.failure_category == "internal_error"
    assert "RuntimeError: disk full" in run.error
    assert run.stats["stop"] == "submitted"
    (step,) = steps_of(session, run)
    assert step.tool_name == "web_search,submit_profile"


# ---- resolving targets and the CLI -----------------------------------------------------------


def ch_site(known: dict[str, dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/company/"):
            data = known.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=data) if data else httpx.Response(404)
        if path == "/search/companies":
            return httpx.Response(
                200, json={"items": [{"title": "ZETA AI LTD", "company_number": "99999999"}]}
            )
        return httpx.Response(404)

    return handler


def test_resolve_by_number_name_and_ambiguity(session, company):
    assert resolve_company(session, "12345678", None) is company
    assert resolve_company(session, "acme ai", None) is company
    session.add(Company(company_number="87654321", name="ACME AI HOLDINGS LTD"))
    session.commit()
    with pytest.raises(CompanyNotFound, match="ambiguous.*12345678.*87654321"):
        resolve_company(session, "acme", None)
    assert resolve_company(session, "Acme AI Limited", None) is company  # exact beats partial


def test_resolve_unknown_number_adds_company_from_companies_house(session, make_http):
    data = {
        "company_number": "10924127",
        "company_name": "WAYVE TECHNOLOGIES LTD",
        "company_status": "active",
        "date_of_creation": "2017-08-21",
        "sic_codes": ["62012"],
        "registered_office_address": {"locality": "London"},
    }
    ch = CompaniesHouseClient(make_http(ch_site({"10924127": data})))
    company = resolve_company(session, "10924127", ch)
    assert company.id is not None and company.name == "WAYVE TECHNOLOGIES LTD"
    assert company.incorporated == date(2017, 8, 21)
    with pytest.raises(CompanyNotFound, match="no company 11111111"):
        resolve_company(session, "11111111", ch)
    with pytest.raises(CompanyNotFound, match="no Companies House key"):
        resolve_company(session, "22222222", None)


def test_resolve_unknown_name_suggests_companies_house_matches(session, make_http):
    ch = CompaniesHouseClient(make_http(ch_site({})))
    with pytest.raises(
        CompanyNotFound, match=r"Companies House suggests: ZETA AI LTD \(99999999\)"
    ):
        resolve_company(session, "zeta", ch)


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    settings = Settings(
        anthropic_api_key="test-key",
        companies_house_api_key=None,
        bulk_model=MODEL,
        strong_model="claude-sonnet-5-5",
        db_path=tmp_path / "cli.db",
        cache_dir=tmp_path / "cache",
    )
    monkeypatch.setattr(enrich_mod, "get_settings", lambda: settings)
    engine = get_engine(settings.db_path)
    init_db(engine)
    with Session(engine) as s:
        s.add(Company(company_number="12345678", name="ACME AI LTD"))
        s.commit()
    return settings


def test_cli_enriches_and_prints_a_summary(cli_env, monkeypatch, capsys):
    client = ScriptedClient(server_search_turn([SEEN], ("submit_profile", submission())))
    monkeypatch.setattr(enrich_mod, "make_client", lambda key: client)
    assert enrich_cli.main(["12345678", "--max-usd", "0.05", "--max-iterations", "4"]) == 0
    out = capsys.readouterr().out
    assert "ok   ACME AI LTD" in out and "category='AI models and applications'" in out
    assert "sourced_facts=3" in out and "cost=USD" in out
    assert "total cost USD" in out


def test_cli_uses_the_strong_model_and_reports_failures(cli_env, monkeypatch, capsys):
    client = ScriptedClient(text_turn(), text_turn())
    monkeypatch.setattr(enrich_mod, "make_client", lambda key: client)
    assert enrich_cli.main(["acme", "nobody-here", "--strong"]) == 1
    captured = capsys.readouterr()
    assert client.requests[0]["model"] == "claude-sonnet-5-5"
    assert "FAIL ACME AI LTD" in captured.err and "no_submit" in captured.err
    assert "cost=USD" in captured.err
    assert "skip nobody-here" in captured.err


def test_cli_reset_deletes_existing_facts_before_enriching(cli_env, monkeypatch, capsys):
    engine = get_engine(cli_env.db_path)
    with Session(engine) as s:
        company = s.exec(select(Company).where(Company.company_number == "12345678")).one()
        enrich_mod.save_profile(
            s, company, enrich_mod.CompanyProfile(name=company.name, category=Category.OTHER)
        )
        s.commit()

    client = ScriptedClient(server_search_turn([SEEN], ("submit_profile", submission())))
    monkeypatch.setattr(enrich_mod, "make_client", lambda key: client)
    assert enrich_cli.main(["12345678", "--reset"]) == 0
    assert "reset ACME AI LTD: deleted" in capsys.readouterr().err


def test_cli_without_an_anthropic_key_exits_2(cli_env, monkeypatch, capsys):
    settings = Settings(None, None, MODEL, "claude-sonnet-5-5", cli_env.db_path, cli_env.cache_dir)
    monkeypatch.setattr(enrich_mod, "get_settings", lambda: settings)
    assert enrich_cli.main(["12345678"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
