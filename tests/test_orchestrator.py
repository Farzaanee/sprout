import asyncio
import re
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import anthropic
import httpx
import pytest
from sqlmodel import Session, col, select

from fakes import response, server_search_turn, tool_use_turn
from sprout.agents import orchestrator as orch
from sprout.agents.orchestrator import (
    RateLimitGate,
    RunContext,
    Worker,
    order_for_work,
    process_company,
    select_working_set,
)
from sprout.config import Settings
from sprout.costs import BatchBudget
from sprout.db import Company, RunKind, RunLog, get_engine, init_db
from sprout.models import Category
from sprout.status import STEPS, CompanyStatus, StatusPolicy, StepStatus

BULK, STRONG = "claude-haiku-5-5", "claude-sonnet-5-5"
NOT_DISCLOSED = {"value": None, "source_url": None, "confidence": "low", "note": None}
ALL_KINDS = [
    RunKind.COMPANY_ENRICH,
    RunKind.COMPANY_CLASSIFICATION,
    RunKind.COMPANY_NEWS_MATCH,
    RunKind.COMPANY_VERIFICATION,
]
SUBMIT_TOOLS = {
    "enrich": "submit_profile",
    "classify": "submit_classification",
    "news": "submit_news",
    "verify": "submit_verification",
}
NO_CLAIMS = "NOCLAIMS AI LTD"


def rate_limit_error():
    return anthropic.RateLimitError(
        "slow down",
        response=httpx.Response(429, request=httpx.Request("POST", "https://x.example")),
        body=None,
    )


class RoutingClient:
    """A fake Anthropic client for concurrent workers. Dispatches on the submit tool the request
    offers (which agent) and the company named in the user message, so thread interleaving cannot
    change what a company receives. `script[(agent, company)]` holds one-shot replacements."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls: list[tuple[str, str, str]] = []  # (agent, company, model)
        self.script: dict[tuple[str, str], list] = {}
        self.on_call = None
        self.messages = self

    def create(self, **kwargs):
        tools = [t["name"] for t in kwargs["tools"]]
        agent = next(a for a, tool in SUBMIT_TOOLS.items() if tool in tools)
        company = re.search(r"Company: (.+)", kwargs["messages"][0]["content"]).group(1)
        with self.lock:
            self.calls.append((agent, company, kwargs["model"]))
            number = len(self.calls)
            queued = self.script.get((agent, company))
            item = queued.pop(0) if queued else None
        if self.on_call:
            self.on_call(number)
        if isinstance(item, Exception):
            raise item
        return item or self.default(agent, company)

    def default(self, agent, company):
        slug = re.sub(r"\W+", "-", company.lower())
        url = f"https://news.example/{slug}"
        if agent == "enrich":
            stage = NOT_DISCLOSED
            if company != NO_CLAIMS:
                stage = {"value": "Seed", "source_url": url, "confidence": "medium", "note": None}
            profile = {
                "website": NOT_DISCLOSED,
                "hq": NOT_DISCLOSED,
                "founded": NOT_DISCLOSED,
                "category": Category.ENTERPRISE_SOFTWARE_AND_CLOUD.value,
                "stage": stage,
                "business_status": NOT_DISCLOSED,
                "total_raised_million_usd": NOT_DISCLOSED,
                "valuation_million_usd": NOT_DISCLOSED,
                "funding_rounds": [],
                "business_model": NOT_DISCLOSED,
                "target_user": NOT_DISCLOSED,
                "one_liner": NOT_DISCLOSED,
            }
            return server_search_turn([url], ("submit_profile", profile), input=1_000)
        if agent == "classify":
            args = {"category": Category.OTHER.value, "rationale": "Builds software."}
            return tool_use_turn(("submit_classification", args), input=1_000)
        if agent == "news":
            entry = {
                "url": url,
                "title": f"{company} news",
                "published": "2026-10-20",
                "big": False,
                "kind": None,
                "summary": "Something happened.",
            }
            return server_search_turn([url], ("submit_news", {"entries": [entry]}), input=1_000)
        verdict = {
            "claim": "stage",
            "verdict": "unverified",
            "second_source_url": None,
            "observed": "nothing found",
            "quote": None,
        }
        return tool_use_turn(("submit_verification", {"verdicts": [verdict]}), input=1_000)

    def agent_calls(self, agent, company=None):
        return [c for c in self.calls if c[0] == agent and company in (None, c[1])]


@pytest.fixture
def settings(tmp_path):
    return Settings(None, None, BULK, STRONG, tmp_path / "t.db", tmp_path / "cache")


@pytest.fixture
def engine(settings):
    engine = get_engine(settings.db_path)
    init_db(engine)
    return engine


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(orch, "RATE_LIMIT_PAUSE_SECONDS", 0.0)


def add_companies(engine, *names, officers=2):
    """Companies ranked in the order given (descending score). Returns their ids."""
    ids = []
    with Session(engine) as session:
        for rank, name in enumerate(names):
            row = Company(
                company_number=f"{rank + 1:08d}",
                name=name,
                candidate_score=100 - rank,
                active_officers=officers,
            )
            session.add(row)
            session.commit()
            ids.append(row.id)
    return ids


def invoke(settings, client, *argv, stop=None):
    args = orch._parse_args(list(argv))
    return asyncio.run(orch.run(settings, args, client=client, stop=stop))


def runs(engine, **where):
    with Session(engine) as session:
        query = select(RunLog)
        for column, value in where.items():
            query = query.where(getattr(RunLog, column) == value)
        return session.exec(query.order_by(col(RunLog.id))).all()


def ok_counts(engine):
    counts: dict[tuple[int, str], int] = {}
    for run in runs(engine, status="ok"):
        if run.company_id is not None:
            key = (run.company_id, run.kind)
            counts[key] = counts.get(key, 0) + 1
    return counts


# ---- the whole pipeline -----------------------------------------------------------------------


def test_every_step_runs_and_writes_its_rows_with_three_workers(settings, engine):
    ids = add_companies(engine, "ACME AI LTD", "BETA AI LTD", NO_CLAIMS)
    client = RoutingClient()
    summary = invoke(settings, client, "--limit", "3", "--concurrency", "3")

    assert summary.exit_code == 0 and summary.stats["results"] == {"ok": 3}
    expected = {(i, k.value): 1 for i in ids for k in ALL_KINDS}
    del expected[(ids[2], RunKind.COMPANY_VERIFICATION.value)]  # no claim, no run row, no cost
    assert ok_counts(engine) == expected
    models = {(agent, model) for agent, _, model in client.calls}
    assert models == {
        ("enrich", BULK), ("classify", BULK), ("news", BULK), ("verify", STRONG)
    }  # fmt: skip
    with Session(engine) as session:
        assert {c.category for c in session.exec(select(Company))} == {Category.OTHER.value}

    (batch,) = runs(engine, kind=RunKind.PIPELINE_RUN.value)
    stats = batch.stats
    assert batch.company_id is None and batch.status == "ok" and "cost_usd" not in stats
    assert stats["selected"] == 3 and stats["limit"] == 3 and stats["concurrency"] == 3
    assert stats["max_total_usd"] == 5.0 and stats["rate_limit_trips"] == 0
    per_run = sum(r.stats["cost_usd"] for r in runs(engine) if r.company_id is not None)
    assert stats["spent_usd"] == pytest.approx(per_run)
    assert set(stats["by_kind"]) == {k.value for k in ALL_KINDS}
    assert "funding" in stats["incomplete_profiles"][str(ids[0])]  # no funding round was found


def test_a_second_invocation_has_nothing_to_do(settings, engine):
    add_companies(engine, "ACME AI LTD", "BETA AI LTD")
    client = RoutingClient()
    invoke(settings, client, "--limit", "2")
    calls = len(client.calls)
    summary = invoke(settings, client, "--limit", "2")
    assert len(client.calls) == calls and summary.stats["results"] == {"ok": 2}


# ---- resume, retry, give up -------------------------------------------------------------------


def test_resume_after_an_interrupt_repeats_no_completed_work(settings, engine):
    ids = add_companies(engine, "ACME AI LTD", "BETA AI LTD", "GAMMA AI LTD")
    stop = threading.Event()
    first = RoutingClient()
    first.on_call = lambda n: stop.set() if n == 2 else None  # Ctrl-C during the second call
    summary = invoke(settings, first, "--limit", "3", "--concurrency", "1", stop=stop)

    assert summary.exit_code == 1 and summary.stats["stopped_early"] == "interrupted"
    assert summary.stats["results"] == {"partial": 1, "stopped": 2}
    assert [c[0] for c in first.calls] == ["enrich", "classify"]  # both finished in flight

    second = RoutingClient()
    resumed = invoke(settings, second, "--limit", "3", "--concurrency", "1")
    assert resumed.exit_code == 0 and resumed.stats["results"] == {"ok": 3}
    assert ok_counts(engine) == {(i, k.value): 1 for i in ids for k in ALL_KINDS}
    assert len(first.calls) + len(second.calls) == 12  # every step exactly once
    # the interrupted company's remaining steps ran second; its enrich and classify did not
    assert [c[0] for c in second.calls if c[1] == "ACME AI LTD"] == ["news", "verify"]


def test_a_failed_step_is_retried_by_the_next_invocation(settings, engine):
    (acme,) = add_companies(engine, "ACME AI LTD")
    first = RoutingClient()
    first.script[("classify", "ACME AI LTD")] = [response([], "refusal")]
    summary = invoke(settings, first, "--limit", "1")
    assert summary.stats["results"] == {"partial": 1} and summary.exit_code == 1
    classify = runs(engine, kind=RunKind.COMPANY_CLASSIFICATION.value)
    assert [r.status for r in classify] == ["error"]
    assert [c[0] for c in first.calls] == ["enrich", "classify", "news", "verify"]  # isolated

    second = RoutingClient()
    resumed = invoke(settings, second, "--limit", "1")
    assert resumed.exit_code == 0 and [c[0] for c in second.calls] == ["classify"]
    assert [r.status for r in runs(engine, kind=RunKind.COMPANY_CLASSIFICATION.value)] == [
        "error",
        "ok",
    ]
    assert ok_counts(engine)[(acme, RunKind.COMPANY_ENRICH.value)] == 1


def test_enrich_failure_stops_the_company(settings, engine):
    add_companies(engine, "ACME AI LTD")
    client = RoutingClient()
    client.script[("enrich", "ACME AI LTD")] = [response([], "refusal")]
    summary = invoke(settings, client, "--limit", "1")
    assert summary.stats["results"] == {"failed": 1}
    assert [c[0] for c in client.calls] == ["enrich"]


def test_three_failed_attempts_give_up_until_forced(settings, engine):
    add_companies(engine, "ACME AI LTD")
    client = RoutingClient()
    client.script[("classify", "ACME AI LTD")] = [response([], "refusal")] * 3
    for _ in range(3):
        assert invoke(settings, client, "--limit", "1").stats["results"] == {"partial": 1}
    assert len(client.agent_calls("classify")) == 3

    fourth = invoke(settings, client, "--limit", "1")
    assert fourth.stats["results"] == {"gave_up": 1} and fourth.exit_code == 1
    assert len(client.agent_calls("classify")) == 3  # no attempt was made

    forced = invoke(settings, client, "--limit", "1", "--force")
    assert len(client.agent_calls("classify")) == 4 and forced.stats["results"] == {"ok": 1}


def test_an_api_error_never_exhausts_a_company(settings, engine):
    add_companies(engine, "ACME AI LTD")
    client = RoutingClient()
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x.example"))
    client.script[("classify", "ACME AI LTD")] = [error] * 4
    for _ in range(4):
        assert invoke(settings, client, "--limit", "1").stats["results"] == {"partial": 1}
    assert len(client.agent_calls("classify")) == 4


# ---- budget, 429, interrupt -------------------------------------------------------------------


def test_an_exhausted_batch_budget_skips_the_rest(settings, engine):
    add_companies(engine, "ACME AI LTD", "BETA AI LTD")
    client = RoutingClient()
    # One enrichment response (USD 0.0001) uses the whole cap: soft by one response.
    summary = invoke(
        settings, client, "--limit", "2", "--concurrency", "1", "--max-total-usd", "0.0001"
    )
    assert [c[0] for c in client.calls] == ["enrich"]
    assert summary.stats["results"] == {"partial": 1, "skipped_budget": 1}
    assert summary.exit_code == 1 and summary.stats["stopped_early"] == "budget"
    assert summary.stats["spent_usd"] == pytest.approx(0.0001)


def test_a_429_trips_the_gate_and_the_step_is_rerun_once(settings, engine):
    add_companies(engine, "ACME AI LTD")
    client = RoutingClient()
    client.script[("news", "ACME AI LTD")] = [rate_limit_error()]
    summary = invoke(settings, client, "--limit", "1")
    assert summary.exit_code == 0 and summary.stats["rate_limit_trips"] == 1
    assert len(client.agent_calls("news")) == 2
    news = runs(engine, kind=RunKind.COMPANY_NEWS_MATCH.value)
    assert [(r.status, r.failure_category) for r in news] == [("error", "api_error"), ("ok", None)]


def test_five_rate_limit_trips_stop_launching_companies(settings, engine):
    add_companies(engine, "A1 AI LTD", "A2 AI LTD", "A3 AI LTD", "A4 AI LTD")
    client = RoutingClient()
    for name in ("A1 AI LTD", "A2 AI LTD", "A3 AI LTD", "A4 AI LTD"):
        client.script[("enrich", name)] = [rate_limit_error(), rate_limit_error()]
    summary = invoke(settings, client, "--limit", "4", "--concurrency", "1")
    assert summary.stats["rate_limit_trips"] == 6  # a failed re-run trips again
    assert summary.stats["results"] == {"failed": 3, "stopped": 1}
    assert summary.exit_code == 1 and summary.stats["stopped_early"] == "rate_limited"
    assert len(client.calls) == 6


def test_the_gate_wait_ends_when_stop_is_set():
    gate, stop = RateLimitGate(60.0), threading.Event()
    gate.trip()
    threading.Timer(0.05, stop.set).start()
    gate.wait(stop)  # returns promptly instead of sleeping 60 s
    assert gate.trips == 1


def worker_for(engine, settings):
    session = Session(engine)
    return Worker(session, fetcher=None, ch=None)  # type: ignore[arg-type]


def context(settings, client, *, stop=None, budget="5"):
    return RunContext(
        client,
        settings,
        BatchBudget(Decimal(budget)),
        RateLimitGate(0.0),
        stop or threading.Event(),
        StatusPolicy(),
        datetime.now(UTC),
    )


def test_process_company_never_raises_and_a_set_stop_event_starts_nothing(settings, engine):
    (acme,) = add_companies(engine, "ACME AI LTD")
    client = RoutingClient()
    stop = threading.Event()
    stop.set()
    outcome = process_company(
        worker_for(engine, settings), acme, STEPS, context(settings, client, stop=stop)
    )
    assert outcome.result == "stopped" and client.calls == []

    ghost = process_company(worker_for(engine, settings), 999, STEPS, context(settings, client))
    assert ghost.result == "failed"
    assert ghost.steps[0].failure_category == "internal_error"


def test_an_unexpected_exception_in_a_step_is_an_internal_error_outcome(
    settings, engine, monkeypatch
):
    (acme,) = add_companies(engine, "ACME AI LTD")

    def boom(*_args):
        raise RuntimeError("boom")

    budget, _ = orch.AGENTS["classify"]
    monkeypatch.setitem(orch.AGENTS, "classify", (budget, boom))
    outcome = process_company(
        worker_for(engine, settings), acme, STEPS, context(settings, RoutingClient())
    )
    assert outcome.result == "partial"
    by_step = {o.step: o for o in outcome.steps}
    assert by_step["classify"].state == "failed"
    assert by_step["classify"].failure_category == "internal_error"
    assert by_step["news"].state == "ok"  # the failure was isolated


def test_a_company_with_no_claims_skips_verify_without_a_run_row(settings, engine):
    (acme,) = add_companies(engine, NO_CLAIMS)
    outcome = process_company(
        worker_for(engine, settings), acme, STEPS, context(settings, RoutingClient())
    )
    states = {o.step: o.state for o in outcome.steps}
    assert outcome.result == "ok" and states["verify"] == "skipped_no_claims"
    assert not runs(engine, kind=RunKind.COMPANY_VERIFICATION.value)


def test_steps_restricts_what_may_run(settings, engine):
    add_companies(engine, "ACME AI LTD")
    client = RoutingClient()
    invoke(settings, client, "--limit", "1", "--steps", "enrich,classify")
    assert [c[0] for c in client.calls] == ["enrich", "classify"]


# ---- startup: reaper, lock, config ------------------------------------------------------------


def test_abandoned_running_rows_are_reaped_at_startup_and_count_as_a_failed_attempt(
    settings, engine
):
    (acme,) = add_companies(engine, "ACME AI LTD")
    old = datetime.now(UTC) - timedelta(hours=2)
    with Session(engine) as session:
        session.add(RunLog(kind=RunKind.COMPANY_ENRICH.value, company_id=acme, started_at=old))
        session.commit()
    summary = invoke(settings, RoutingClient(), "--limit", "1")
    first = runs(engine, kind=RunKind.COMPANY_ENRICH.value)[0]
    assert (first.status, first.failure_category) == ("error", "internal_error")
    assert first.error_message.startswith("abandoned")
    assert summary.stats["results"] == {"ok": 1}  # retried, not exhausted


def test_a_young_running_pipeline_row_refuses_a_second_run(settings, engine):
    add_companies(engine, "ACME AI LTD")
    with Session(engine) as session:
        session.add(RunLog(kind=RunKind.PIPELINE_RUN.value))
        session.commit()
    client = RoutingClient()
    refused = invoke(settings, client, "--limit", "1")
    assert refused.exit_code == 2 and "in progress" in refused.error and client.calls == []

    forced = invoke(settings, client, "--limit", "1", "--force")
    assert forced.exit_code == 0 and client.calls


def test_an_old_running_pipeline_row_does_not_block(settings, engine):
    add_companies(engine, "ACME AI LTD")
    old = datetime.now(UTC) - timedelta(hours=2)
    with Session(engine) as session:
        session.add(RunLog(kind=RunKind.PIPELINE_RUN.value, started_at=old))
        session.commit()
    assert invoke(settings, RoutingClient(), "--limit", "1").exit_code == 0


def test_a_missing_api_key_is_a_configuration_error(settings, engine):
    add_companies(engine, "ACME AI LTD")
    summary = asyncio.run(orch.run(settings, orch._parse_args(["--limit", "1"])))
    assert summary.exit_code == 2 and "ANTHROPIC_API_KEY" in summary.error


def test_an_unknown_only_target_is_a_configuration_error(settings, engine):
    summary = invoke(settings, RoutingClient(), "--only", "NO SUCH CO")
    assert summary.exit_code == 2 and "not in the database" in summary.error


def test_bad_arguments_are_rejected():
    for argv in (["--steps", "enrich,dance"], ["--limit", "0"], ["--max-total-usd", "0"]):
        with pytest.raises(SystemExit):
            orch._parse_args(argv)


def test_concurrency_is_clamped(settings, engine):
    add_companies(engine, "ACME AI LTD")
    summary = invoke(settings, RoutingClient(), "--limit", "1", "--concurrency", "99")
    assert summary.stats["concurrency"] == orch.MAX_CONCURRENCY


# ---- dry run ----------------------------------------------------------------------------------


def test_dry_run_prints_the_plan_and_spends_nothing(settings, engine):
    add_companies(engine, "ACME AI LTD", "BETA AI LTD")
    args = orch._parse_args(["--limit", "2", "--dry-run"])
    summary = asyncio.run(orch.run(settings, args))  # no client, no key: none is needed
    assert summary.exit_code == 0
    assert "ACME AI LTD" in summary.text and "needs: enrich,classify,news,verify" in summary.text
    assert "estimated USD 0.18" in summary.text and "nothing was spent" in summary.text
    assert runs(engine) == []


# ---- selection and order ----------------------------------------------------------------------


def test_working_set_is_the_ranked_prescreen_survivors_and_limit_is_a_stable_prefix(engine):
    with Session(engine) as session:
        rows = [
            ("LOW", 1, 3),
            ("TOP", 9, 2),
            ("TIE-B", 5, 4),
            ("TIE-A", 5, 2),
            ("SHELL", 20, 1),
            ("UNKNOWN", 30, None),
        ]
        for number, (name, score, officers) in enumerate(rows):
            session.add(
                Company(
                    company_number=f"{number:08d}",
                    name=name,
                    candidate_score=score,
                    active_officers=officers,
                )
            )
        session.commit()
        names = {c.id: c.name for c in session.exec(select(Company))}

        def pick(**kwargs):
            return [names[i] for i in select_working_set(session, **kwargs)]

        assert pick(limit=10) == ["TOP", "TIE-B", "TIE-A", "LOW"]  # ties by id; shells dropped
        assert pick(limit=2) == ["TOP", "TIE-B"]
        assert pick(limit=2, prescreen=False) == ["UNKNOWN", "SHELL"]
        assert pick(limit=1, only=["shell", "TOP"]) == ["SHELL", "TOP"]  # bypasses both


def status_of(company_id, **states):
    steps = {s: StepStatus(s, states.get(s, "never"), None, 0, None) for s in STEPS}  # type: ignore[arg-type]
    return CompanyStatus(company_id, steps, Decimal(0))


def test_order_of_work_is_failed_then_partial_then_never_then_stale(engine):
    statuses = {
        1: status_of(1, enrich="fresh", classify="fresh", news="fresh", verify="fresh"),
        2: status_of(2, enrich="stale", classify="stale"),
        3: status_of(3),
        4: status_of(4, enrich="fresh"),
        5: status_of(5, enrich="fresh", classify="failed", news="fresh", verify="fresh"),
        6: status_of(6, enrich="never"),
    }
    assert order_for_work([1, 2, 3, 4, 5, 6], statuses, STEPS) == [5, 4, 3, 6, 2, 1]
    # outside the allowed steps, a failed classify no longer jumps the queue
    assert order_for_work([4, 5], statuses, ("enrich", "news")) == [4, 5]
