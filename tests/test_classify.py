from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlmodel import Session, select

from fakes import ScriptedClient, text_turn, tool_use_turn
from sprout.agents import classify as classify_mod
from sprout.agents import enrich as enrich_mod
from sprout.agents.classify import (
    CLASSIFY_BUDGET,
    SYSTEM_PROMPT,
    Classification,
    classify_company,
    classify_with_result,
)
from sprout.agents.common import AgentFailed, Deps
from sprout.agents.loop import Budget
from sprout.config import Settings
from sprout.db import Company, Fact, RunKind, RunLog, RunLogStep, get_engine, init_db
from sprout.models import Category
from sprout.sources.web import WebFetcher

MODEL = "claude-haiku-5-5"
SITE = "https://acme.example/about"
ENRICHED = Category.ENTERPRISE_SOFTWARE_AND_CLOUD.value
FINTECH = Category.FINTECH_AND_PAYMENTS.value


def submit(category=FINTECH, rationale="Sells fraud detection to banks.", **usage):
    args = {"category": category, "rationale": rationale}
    return tool_use_turn(("submit_classification", args), **usage)


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
        sic_codes=["62012"],
        locality="London",
        category=ENRICHED,
    )
    session.add(row)
    session.commit()
    assert row.id is not None
    for name, value in {
        "one_liner": "Detects payment fraud for banks.",
        "target_user": "Banks.",
        "business_model": None,  # researched, nothing found: must not reach the prompt
        "stage": "Seed",  # not a classifier input
    }.items():
        session.add(
            Fact(
                company_id=row.id,
                field=name,
                value_json=None if value is None else f'"{value}"',
                source_url=SITE,
                retrieved_at=datetime.now(UTC),
                confidence="high",
            )
        )
    session.add(
        RunLog(
            kind=RunKind.COMPANY_ENRICH.value, company_id=row.id, status="ok", stats={"stop": "x"}
        )
    )
    session.commit()
    return row


@pytest.fixture
def deps_for(make_http):
    def build(client):
        fetcher = WebFetcher(
            make_http(lambda r: httpx.Response(404)), resolver=lambda h: ["1.1.1.1"]
        )
        return Deps(client, fetcher, None, Settings(None, None, MODEL, "claude-sonnet-5-5"), MODEL)

    return build


def run_of(session, kind=RunKind.COMPANY_CLASSIFICATION):
    return session.exec(select(RunLog).where(RunLog.kind == kind.value)).all()


def test_success_overwrites_category_and_records_the_run(session, company, deps_for):
    client = ScriptedClient(submit(input=1_000, output=100))
    result = classify_company(session, company, deps_for(client))
    assert result == Classification(
        category=Category.FINTECH_AND_PAYMENTS, rationale=result.rationale
    )
    session.refresh(company)
    assert company.category == FINTECH
    (run,) = run_of(session)
    assert run.status == "ok" and run.company_id == company.id
    assert run.model == MODEL and run.effort == "low"
    assert run.max_iterations == CLASSIFY_BUDGET.max_iterations
    assert run.stats["category"] == FINTECH
    assert run.stats["previous_category"] == ENRICHED
    assert run.stats["rationale"] == "Sells fraud detection to banks."
    assert run.stats["stop"] == "submitted"
    assert len(session.exec(select(RunLogStep)).all()) == 1


def test_request_has_cached_taxonomy_prompt_and_only_the_allowed_tools(session, company, deps_for):
    client = ScriptedClient(submit())
    classify_company(session, company, deps_for(client))
    request = client.requests[0]
    (system,) = request["system"]
    assert system["text"] == SYSTEM_PROMPT and system["cache_control"] == {"type": "ephemeral"}
    assert all(c.value in SYSTEM_PROMPT for c in Category)
    assert [t["name"] for t in request["tools"]] == ["fetch_page", "submit_classification"]
    assert request["model"] == MODEL and request["output_config"] == {"effort": "low"}


def test_prompt_reuses_the_enrichment_taxonomy_text():
    assert enrich_mod.CATEGORY_GUIDANCE in SYSTEM_PROMPT
    assert enrich_mod.CATEGORY_GUIDANCE in enrich_mod.SYSTEM_PROMPT
    assert enrich_mod.UNTRUSTED_CONTENT_RULE in SYSTEM_PROMPT


def test_classifier_input_is_independent_of_enrichments_category(session, company, deps_for):
    client = ScriptedClient(submit())
    classify_company(session, company, deps_for(client))
    (message,) = client.requests[0]["messages"]
    text = message["content"]
    assert ENRICHED not in text  # the prior category never reaches the model
    assert "Company: ACME AI LTD" in text and "SIC codes: 62012" in text
    assert "Registered office locality: London" in text
    assert f'<untrusted_content source="{SITE}">\none_liner: Detects payment fraud' in text
    assert "target_user: Banks." in text
    assert "business_model" not in text and "stage" not in text


def test_profile_with_no_facts_still_classifies(session, company, deps_for):
    for fact in session.exec(select(Fact)).all():
        session.delete(fact)
    session.commit()
    client = ScriptedClient(submit())
    classify_company(session, company, deps_for(client))
    assert "(none)" in client.requests[0]["messages"][0]["content"]


def test_out_of_taxonomy_label_gets_one_repair_then_is_accepted(session, company, deps_for):
    client = ScriptedClient(submit(category="Generative AI video"), submit())
    classify_company(session, company, deps_for(client))
    assert client.calls == 2
    assert "not in the taxonomy" in str(client.requests[1]["messages"][-1])
    assert company.category == FINTECH
    assert run_of(session)[0].repairs == 1


def test_still_out_of_taxonomy_after_the_repair_is_stored_as_other(session, company, deps_for):
    client = ScriptedClient(submit(category="Video AI"), submit(category="Still wrong"))
    classification = classify_company(session, company, deps_for(client))
    assert classification.category is Category.OTHER
    assert company.category == Category.OTHER.value
    (run,) = run_of(session)
    assert run.status == "ok" and run.stats["category"] == "Other"


def test_overlong_rationale_is_trimmed_not_rejected(session, company, deps_for):
    client = ScriptedClient(submit(rationale="x" * 500))
    assert len(classify_company(session, company, deps_for(client)).rationale) == 300


def test_missing_rationale_is_repaired(session, company, deps_for):
    client = ScriptedClient(submit(rationale=""), submit())
    classify_company(session, company, deps_for(client))
    assert client.calls == 2


def test_failure_raises_agent_failed_keeps_the_category_and_records_the_cost(
    session, company, deps_for
):
    client = ScriptedClient(text_turn(input=2_000), text_turn(input=2_000))
    with pytest.raises(AgentFailed) as caught:
        classify_company(session, company, deps_for(client))
    assert caught.value.stop == "no_submit" and caught.value.cost_usd > 0
    session.refresh(company)
    assert company.category == ENRICHED
    (run,) = run_of(session)
    assert run.status == "error" and run.failure_category is not None
    assert "category" not in run.stats and run.stats["cost_usd"] > 0


def test_budget_is_honoured(session, company, deps_for):
    client = ScriptedClient(text_turn(input=500_000))
    budget = Budget(max_iterations=4, max_usd=Decimal("0.01"), max_repairs=1)
    with pytest.raises(AgentFailed) as caught:
        classify_with_result(session, company, deps_for(client), budget)
    assert caught.value.stop == "budget" and client.calls == 1


def test_offering_a_tool_outside_the_allow_list_fails_before_any_run(
    session, company, deps_for, monkeypatch
):
    monkeypatch.setattr(
        classify_mod, "ALLOWED_CLASSIFY_TOOLS", frozenset({"submit_classification"})
    )
    client = ScriptedClient()
    with pytest.raises(RuntimeError, match="ALLOWED_CLASSIFY_TOOLS"):
        classify_company(session, company, deps_for(client))
    assert client.calls == 0 and run_of(session) == []


def test_reclassifying_keeps_enrichments_category_as_previous(session, company, deps_for):
    classify_company(session, company, deps_for(ScriptedClient(submit())))
    classify_company(
        session, company, deps_for(ScriptedClient(submit(category=Category.OTHER.value)))
    )
    first, second = run_of(session)
    assert first.stats["previous_category"] == second.stats["previous_category"] == ENRICHED
    assert company.category == Category.OTHER.value


def test_a_new_enrichment_resets_previous_category(session, company, deps_for):
    classify_company(session, company, deps_for(ScriptedClient(submit())))
    session.add(RunLog(kind=RunKind.COMPANY_ENRICH.value, company_id=company.id, status="ok"))
    company.category = Category.CYBERSECURITY.value  # what the new enrichment stored
    session.commit()
    classify_company(session, company, deps_for(ScriptedClient(submit())))
    assert run_of(session)[-1].stats["previous_category"] == Category.CYBERSECURITY.value
