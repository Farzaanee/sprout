from datetime import date
from decimal import Decimal

import anthropic
import httpx
import pytest
from sqlmodel import Session, select

from fakes import ScriptedClient, tool_use_turn
from sprout.agents.common import AgentFailed, Deps, run_recorded
from sprout.agents.enrich import EnrichmentFailed
from sprout.agents.loop import Budget, Tool, ToolOutcome
from sprout.agents.tools import ALLOWED_ENRICHMENT_TOOLS, SourceLedger, assert_tools_allowed
from sprout.config import Settings
from sprout.db import Company, RunKind, RunLog, RunLogStep, get_engine, init_db

MODEL = "claude-haiku-5-5"
BUDGET = Budget(max_iterations=3, max_usd=Decimal("0.05"), max_repairs=1)


@pytest.fixture
def session(tmp_path):
    engine = get_engine(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def company(session):
    row = Company(company_number="12345678", name="ACME AI LTD", incorporated=date(2019, 3, 4))
    session.add(row)
    session.commit()
    return row


def submit_tool(accept: bool = True) -> Tool:
    def handle(args):
        if not accept:
            return ToolOutcome("rejected", is_error=True)
        return ToolOutcome("ok", payload=args["answer"])

    return Tool(
        spec={"name": "submit_x", "input_schema": {"type": "object"}}, handler=handle, terminal=True
    )


def record(session, company, client, persist, accept=True, **kwargs):
    deps = Deps(client, None, None, Settings(None, None, MODEL, "claude-sonnet-5-5"), MODEL)  # type: ignore[arg-type]
    return run_recorded(
        session,
        company,
        RunKind.COMPANY_CLASSIFICATION,
        deps,
        system="sys",
        messages=[{"role": "user", "content": "go"}],
        tools=[submit_tool(accept)],
        budget=BUDGET,
        ledger=SourceLedger(),
        max_tokens=100,
        persist=persist,
        **kwargs,
    )


def test_success_records_run_steps_and_persists_inside_the_run(session, company):
    seen = []
    client = ScriptedClient(tool_use_turn(("submit_x", {"answer": "A"}), input=100, output=10))
    result = record(session, company, client, seen.append)
    assert seen == ["A"] and result.payload == "A"
    run = session.exec(select(RunLog)).one()
    assert run.kind == "company_classification" and run.status == "ok"
    assert run.company_id == company.id and run.model == MODEL and run.iterations == 1
    assert run.stats["stop"] == "submitted" and run.stats["error_type"] is None
    assert len(session.exec(select(RunLogStep)).all()) == 1


def test_failure_commits_the_run_before_raising_and_carries_error_type(session, company):
    error = anthropic.RateLimitError(
        "slow down",
        response=httpx.Response(429, request=httpx.Request("POST", "https://x.example")),
        body=None,
    )
    seen = []
    with pytest.raises(AgentFailed) as err:
        record(session, company, ScriptedClient(error), seen.append)
    assert seen == []
    assert err.value.stop == "error" and err.value.error_type == "RateLimitError"
    assert err.value.cost_usd == 0 and not isinstance(err.value, EnrichmentFailed)
    run = session.exec(select(RunLog)).one()
    assert run.status == "error" and run.failure_category is not None
    assert run.stats["error_type"] == "RateLimitError"


def test_unaccepted_submission_raises_the_requested_subclass(session, company):
    client = ScriptedClient(*[tool_use_turn(("submit_x", {"answer": "A"}), input=100)] * 2)
    with pytest.raises(EnrichmentFailed, match="enrichment ended with stop="):
        record(session, company, client, lambda _: None, accept=False, failed=EnrichmentFailed)
    assert session.exec(select(RunLog)).one().status == "error"


def test_persist_error_marks_the_run_failed(session, company):
    def boom(_):
        raise ValueError("db down")

    client = ScriptedClient(tool_use_turn(("submit_x", {"answer": "A"})))
    with pytest.raises(ValueError, match="db down"):
        record(session, company, client, boom)
    assert session.exec(select(RunLog)).one().status == "error"


def test_assert_tools_allowed_accepts_a_subset_and_names_the_offender():
    tools = [Tool(spec={"name": "web_search"}), Tool(spec={"name": "fetch_page"})]
    assert_tools_allowed(tools, frozenset({"web_search", "fetch_page", "x"}), "ALLOWED_X")
    with pytest.raises(RuntimeError, match=r"ALLOWED_X.*fetch_page"):
        assert_tools_allowed(tools, frozenset({"web_search"}), "ALLOWED_X")
    assert_tools_allowed([], ALLOWED_ENRICHMENT_TOOLS, "ALLOWED_ENRICHMENT_TOOLS")
