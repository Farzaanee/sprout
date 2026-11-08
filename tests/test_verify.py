import json
from datetime import UTC, date, datetime

import httpx
import pytest
from sqlmodel import Session, select

from fakes import ScriptedClient, server_search_turn, text_turn, tool_use_turn
from sprout.agents import verify as verify_mod
from sprout.agents.common import AgentFailed, Deps
from sprout.agents.verify import (
    SYSTEM_PROMPT,
    VERIFY_BUDGET,
    build_claims,
    verify_funding,
)
from sprout.config import Settings
from sprout.db import (
    Company,
    Fact,
    FundingRoundRow,
    RunKind,
    RunLog,
    apply_verification,
    get_engine,
    init_db,
    winning_facts,
)
from sprout.sources.web import WebFetcher

STRONG = "claude-sonnet-5-5"
OWN = "https://acme.example/about"  # on the enrichment ledger, total raised comes from here
PRESS = "https://press.example/acme-series-a"  # stage and latest round come from here
OTHER = "https://other.example/acme-funding"  # a genuinely independent page
NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture
def session(tmp_path):
    engine = get_engine(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def add_fact(session, company, field, value, url, confidence, note=None):
    session.add(
        Fact(
            company_id=company.id,
            field=field,
            value_json=None if value is None else json.dumps(value),
            source_url=url,
            retrieved_at=NOW,
            confidence=confidence,
            note=note,
        )
    )


@pytest.fixture
def company(session):
    row = Company(company_number="12345678", name="ACME AI LTD", locality="London")
    session.add(row)
    session.commit()
    add_fact(session, row, "stage", "Series A", PRESS, "high")
    add_fact(session, row, "total_raised_million_usd", 20, OWN, "medium", "Reported as US$20m")
    add_fact(session, row, "valuation_million_usd", 100, OWN, "medium")
    for stage, amount, announced in (
        ("Seed", 5, date(2023, 3, 1)),
        ("Series A", 15, date(2024, 6, 1)),
    ):
        session.add(
            FundingRoundRow(
                company_id=row.id,
                stage=stage,
                amount_million_usd=amount,
                announced=announced,
                source_url=PRESS,
                retrieved_at=NOW,
                confidence="high",
            )
        )
    session.add(
        RunLog(
            kind=RunKind.COMPANY_ENRICH.value,
            company_id=row.id,
            status="ok",
            stats={
                "sources": [
                    {"url": OWN, "fetched": True},
                    {"url": PRESS, "fetched": True},
                    {"url": "https://seen.example/snippet", "fetched": False},
                ]
            },
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
                200, text="Acme raised US$15m in a Series A.", headers={"content-type": "text/html"}
            )

        fetcher = WebFetcher(make_http(handler), resolver=lambda h: ["1.1.1.1"])
        return Deps(client, fetcher, None, Settings(None, None, "bulk", STRONG), STRONG)

    return build


def verdict(claim, kind="confirmed", url=OTHER, observed="Series A, US$15m", quote="raised US$15m"):
    return {
        "claim": claim,
        "verdict": kind,
        "second_source_url": url,
        "observed": observed,
        "quote": quote,
    }


def unverified(claim):
    return verdict(claim, "unverified", url=None, observed="nothing found", quote=None)


def submit(*verdicts, **usage):
    return tool_use_turn(("submit_verification", {"verdicts": list(verdicts)}), **usage)


def fetch(url=OTHER):
    return tool_use_turn(("fetch_page", {"url": url}))


def run_verify(session, company, client, deps_for, **kwargs):
    claims = build_claims(session, company)
    return verify_funding(session, company, deps_for(client), claims, **kwargs)


def row(session, model, **where):
    return session.exec(select(model).filter_by(company_id=1, **where)).all()


def stage_fact(session):
    return winning_facts(row(session, Fact))["stage"]


def latest(session):
    return max(row(session, FundingRoundRow), key=lambda r: r.announced)


# ---- build_claims ---------------------------------------------------------------------------


def test_build_claims_uses_winning_facts_and_the_latest_round(session, company):
    claims = build_claims(session, company)
    assert [(c.name, c.text, c.source_url, c.confidence) for c in claims] == [
        ("stage", "Stage: Series A", PRESS, "high"),
        (
            "latest_round",
            "Latest funding round: Series A, US$15m, announced 2024-06-01",
            PRESS,
            "high",
        ),
        ("total_raised", "Total raised: US$20m", OWN, "medium"),
    ]  # valuation is not a claim


def test_build_claims_is_empty_without_stage_or_funding(session, company):
    for r in session.exec(select(Fact).where(Fact.field != "valuation_million_usd")).all():
        session.delete(r)
    for r in session.exec(select(FundingRoundRow)).all():
        session.delete(r)
    session.commit()
    assert build_claims(session, company) == []


def test_unknown_stage_and_null_values_are_not_claims(session, company):
    for r in session.exec(select(Fact).where(Fact.field == "stage")).all():
        r.value_json = json.dumps("Unknown")
    for r in session.exec(select(Fact).where(Fact.field == "total_raised_million_usd")).all():
        r.value_json = None
    session.commit()
    assert [c.name for c in build_claims(session, company)] == ["latest_round"]


# ---- the run --------------------------------------------------------------------------------


def test_verdicts_are_applied_without_changing_any_value(session, company, deps_for):
    client = ScriptedClient(
        server_search_turn([OTHER], ("fetch_page", {"url": OTHER})),
        submit(
            verdict("stage"),
            verdict("latest_round", "disagrees", observed="Series A, US$9m", quote="US$9m"),
            unverified("total_raised"),
            input=3_000,
            output=300,
        ),
    )
    verdicts = run_verify(session, company, client, deps_for)
    assert [v.verdict for v in verdicts] == ["confirmed", "disagrees", "unverified"]

    stage = stage_fact(session)
    assert (stage.value_json, stage.confidence) == ('"Series A"', "high")
    newest = latest(session)
    assert (newest.amount_million_usd, newest.stage) == (15, "Series A")  # the value is kept
    assert newest.confidence == "medium"
    assert newest.note == f"Disputed by {OTHER}: Series A, US$9m"
    total = winning_facts(row(session, Fact))["total_raised_million_usd"]
    assert (total.confidence, total.note) == ("medium", "Reported as US$20m")
    assert [r.confidence for r in row(session, FundingRoundRow) if r.stage == "Seed"] == ["high"]

    facts = winning_facts(row(session, Fact))
    confirmed = facts["verification:stage"]
    assert json.loads(confirmed.value_json) == {
        "verdict": "confirmed",
        "observed": "Series A, US$15m",
    }
    assert confirmed.source_url == OTHER and confirmed.note == "raised US$15m"
    assert json.loads(facts["verification:latest_round"].value_json)["verdict"] == "disagrees"
    assert facts["verification:total_raised"].value_json is None
    assert facts["verification:total_raised"].source_url is None

    (run,) = row(session, RunLog, kind=RunKind.COMPANY_VERIFICATION.value)
    assert run.status == "ok" and run.model == STRONG
    assert run.max_iterations == VERIFY_BUDGET.max_iterations and run.max_usd == 0.10
    assert [v["verdict"] for v in run.stats["verdicts"]] == ["confirmed", "disagrees", "unverified"]
    assert run.stats["cost_usd"] > 0


def test_request_offers_two_searches_and_the_three_allowed_tools(session, company, deps_for):
    client = ScriptedClient(
        submit(*(unverified(c) for c in ("stage", "latest_round", "total_raised")))
    )
    run_verify(session, company, client, deps_for)
    request = client.requests[0]
    (system,) = request["system"]
    assert system["text"] == SYSTEM_PROMPT
    names = [t["name"] for t in request["tools"]]
    assert names == ["web_search", "fetch_page", "submit_verification"]
    assert request["tools"][0]["max_uses"] == 2
    assert request["model"] == STRONG


def test_prompt_and_message_treat_second_sources_and_claims_as_untrusted(
    session, company, deps_for
):
    assert "untrusted data" in SYSTEM_PROMPT
    assert "never correct, replace or add a value" in SYSTEM_PROMPT
    assert "unverified" in SYSTEM_PROMPT and "10 percent" in SYSTEM_PROMPT
    client = ScriptedClient(
        submit(*(unverified(c) for c in ("stage", "latest_round", "total_raised")))
    )
    run_verify(session, company, client, deps_for)
    text = client.requests[0]["messages"][0]["content"]
    assert f'<untrusted_content source="{PRESS}">\nStage: Series A' in text
    assert "Already used for the profile" in text and OWN in text  # told what not to cite
    assert "Valued" not in text and "valuation" not in text.lower()


def all_unverified_with(first):
    return [first, unverified("latest_round"), unverified("total_raised")]


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (verdict("stage", url="https://never.example/x"), "not returned by any tool"),
        (verdict("stage", url="https://seen.example/snippet"), "only seen in a search result"),
        (verdict("stage", url="https://www.press.example/other-page"), "same website"),
        (verdict("stage", url=OWN), "a page the profile was built from"),
        (verdict("stage", quote="  "), "quote"),
        (verdict("stage", observed=""), "observed"),
        (verdict("stage", url=None), "second_source_url is required"),
    ],
)
def test_a_non_independent_verdict_is_repaired_then_accepted(
    session, company, deps_for, bad, reason
):
    client = ScriptedClient(
        server_search_turn([OTHER, "https://seen.example/snippet"], ("fetch_page", {"url": OTHER})),
        tool_use_turn(("fetch_page", {"url": OWN})),  # fetched, but the enrichment already used it
        tool_use_turn(("fetch_page", {"url": "https://www.press.example/other-page"})),
        submit(*all_unverified_with(bad)),
        submit(*all_unverified_with(verdict("stage"))),
    )
    verdicts = run_verify(session, company, client, deps_for)
    rejection = str(client.requests[-1]["messages"][-1])
    assert "Verdicts rejected" in rejection and reason in rejection
    assert verdicts[0].verdict == "confirmed" and verdicts[0].second_source_url is not None
    (run,) = row(session, RunLog, kind=RunKind.COMPANY_VERIFICATION.value)
    assert run.status == "ok" and run.repairs == 1


def test_a_verdict_still_not_independent_after_the_repair_is_recorded_as_unverified(
    session, company, deps_for
):
    bad = verdict("stage", url="https://never.example/x")
    client = ScriptedClient(submit(*all_unverified_with(bad)), submit(*all_unverified_with(bad)))
    verdicts = run_verify(session, company, client, deps_for)
    assert client.calls == 2
    assert verdicts[0].verdict == "unverified" and verdicts[0].second_source_url is None
    assert verdicts[0].observed.startswith("rejected: https://never.example/x was not returned")
    stage = stage_fact(session)
    assert stage.confidence == "high"
    assert winning_facts(row(session, Fact))["verification:stage"].value_json is None


def test_missing_duplicate_and_unknown_claims_are_repaired(session, company, deps_for):
    partial = submit(unverified("stage"), unverified("stage"))
    client = ScriptedClient(partial, partial)
    verdicts = run_verify(session, company, client, deps_for)
    rejection = str(client.requests[1]["messages"][-1])
    assert "given twice" in rejection and "latest_round: no verdict given" in rejection
    assert [v.claim for v in verdicts] == ["stage", "latest_round", "total_raised"]
    assert verdicts[1].observed == "rejected: no verdict given"


def test_unverified_keeps_no_source_even_if_the_model_gave_one(session, company, deps_for):
    odd = verdict("stage", "unverified", url="https://never.example/x", quote="x")
    client = ScriptedClient(submit(odd, unverified("latest_round"), unverified("total_raised")))
    verdicts = run_verify(session, company, client, deps_for)
    assert client.calls == 1
    assert verdicts[0].second_source_url is None and verdicts[0].quote is None


def test_failure_raises_agent_failed_changes_nothing_and_records_the_cost(
    session, company, deps_for
):
    client = ScriptedClient(text_turn(input=2_000), text_turn(input=2_000))
    with pytest.raises(AgentFailed) as caught:
        run_verify(session, company, client, deps_for)
    assert caught.value.stop == "no_submit" and caught.value.cost_usd > 0
    assert row(session, Fact, field="verification:stage") == []
    (run,) = row(session, RunLog, kind=RunKind.COMPANY_VERIFICATION.value)
    assert run.status == "error" and run.failure_category is not None


def test_no_claims_is_a_caller_error_before_any_run(session, company, deps_for):
    client = ScriptedClient()
    with pytest.raises(ValueError, match="at least one claim"):
        verify_funding(session, company, deps_for(client), [])
    assert client.calls == 0 and row(session, RunLog, kind=RunKind.COMPANY_VERIFICATION.value) == []


def test_offering_a_tool_outside_the_allow_list_fails_before_any_run(
    session, company, deps_for, monkeypatch
):
    monkeypatch.setattr(verify_mod, "ALLOWED_VERIFY_TOOLS", frozenset({"submit_verification"}))
    client = ScriptedClient()
    with pytest.raises(RuntimeError, match="ALLOWED_VERIFY_TOOLS"):
        run_verify(session, company, client, deps_for)
    assert client.calls == 0
    assert row(session, RunLog, kind=RunKind.COMPANY_VERIFICATION.value) == []


def test_without_an_ok_enrichment_nothing_is_excluded_but_the_claim_host(
    session, company, deps_for
):
    for r in session.exec(select(RunLog)).all():
        session.delete(r)
    session.commit()
    client = ScriptedClient(
        tool_use_turn(("fetch_page", {"url": OWN})),
        submit(*all_unverified_with(verdict("stage", url=OWN))),
    )
    assert run_verify(session, company, client, deps_for)[0].verdict == "confirmed"


# ---- apply_verification ---------------------------------------------------------------------


def apply(session, company, claim, kind, url=OTHER, observed="US$9m", quote="q"):
    apply_verification(
        session, company.id, claim, verdict=kind, source_url=url, observed=observed, quote=quote
    )
    session.commit()


def test_disagrees_lowers_one_step_per_verdict_and_floors_at_low(session, company):
    apply(session, company, "stage", "disagrees")
    assert stage_fact(session).confidence == "medium"
    apply(session, company, "stage", "disagrees", url="https://third.example/x")
    assert stage_fact(session).confidence == "low"
    apply(session, company, "stage", "disagrees", url="https://fourth.example/x")
    assert stage_fact(session).confidence == "low"
    assert stage_fact(session).value_json == '"Series A"'


@pytest.mark.parametrize("kind", ["confirmed", "disagrees", "unverified"])
def test_reapplying_a_verdict_changes_nothing(session, company, kind):
    apply(session, company, "latest_round", kind)
    apply(session, company, "stage", kind)
    snapshot = (
        [(r.field, r.value_json, r.source_url, r.confidence, r.note) for r in row(session, Fact)],
        [(r.confidence, r.note) for r in row(session, FundingRoundRow)],
    )
    apply(session, company, "latest_round", kind)
    apply(session, company, "stage", kind)
    assert (
        [(r.field, r.value_json, r.source_url, r.confidence, r.note) for r in row(session, Fact)],
        [(r.confidence, r.note) for r in row(session, FundingRoundRow)],
    ) == snapshot
    assert len(row(session, Fact, field="verification:stage")) == 1


def test_confirmed_raises_a_low_confidence_claim_to_high(session, company):
    total = winning_facts(row(session, Fact))["total_raised_million_usd"]
    total.confidence = "low"
    session.commit()
    apply(session, company, "total_raised", "confirmed")
    assert winning_facts(row(session, Fact))["total_raised_million_usd"].confidence == "high"


def test_unverified_changes_nothing_on_the_claim_and_records_a_null_fact(session, company):
    apply(session, company, "stage", "unverified", url=None, observed="nothing found", quote=None)
    assert stage_fact(session).confidence == "high" and stage_fact(session).note is None
    fact = winning_facts(row(session, Fact))["verification:stage"]
    assert fact.value_json is None and fact.source_url is None and fact.note is None


def test_a_new_verdict_replaces_the_old_verification_fact(session, company):
    apply(session, company, "stage", "confirmed")
    apply(session, company, "stage", "unverified", url=None, observed=None, quote=None)
    (only,) = row(session, Fact, field="verification:stage")
    assert only.value_json is None


def test_verification_facts_do_not_disturb_the_profiles_winning_facts(session, company):
    before = {k: v.value_json for k, v in winning_facts(row(session, Fact)).items()}
    apply(session, company, "stage", "confirmed")
    after = {k: v.value_json for k, v in winning_facts(row(session, Fact)).items()}
    assert {k: v for k, v in after.items() if not k.startswith("verification:")} == before
    assert set(after) - set(before) == {"verification:stage"}
