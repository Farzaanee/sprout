"""What every agent shares: dependencies, the failure type and the recorded run.

`run_recorded` is the logged_run + run_agent + stats + run_log_step block that used to live in
`enrich.py`; the enrichment, classification, news and verification agents all call it.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlmodel import Session

from sprout.agents.loop import (
    Budget,
    LoopResult,
    MessagesClient,
    StepRecord,
    Tool,
    run_agent,
)
from sprout.agents.tools import SourceLedger
from sprout.config import Settings
from sprout.db import Company, RunKind, RunLogStep, logged_run
from sprout.failures import failure_category_for
from sprout.sources.companies_house import CompaniesHouseClient
from sprout.sources.web import WebFetcher

DEFAULT_EFFORT = "low"


@dataclass(frozen=True)
class Deps:
    client: MessagesClient
    fetcher: WebFetcher
    ch: CompaniesHouseClient | None
    settings: Settings
    model: str
    effort: str = DEFAULT_EFFORT


class AgentFailed(RuntimeError):
    """An agent run ended without an accepted submission. The run is already recorded."""

    label = "agent"

    def __init__(
        self,
        stop: str,
        cost_usd: Decimal,
        detail: str | None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(
            f"{self.label} ended with stop={stop!r}" + (f": {detail}" if detail else "")
        )
        self.stop = stop
        self.cost_usd = cost_usd
        self.error_type = error_type  # LoopResult.error_type, e.g. "RateLimitError"


def _stats(result: LoopResult, ledger: SourceLedger) -> dict[str, Any]:
    """The thin, free-form remainder of a run's record; everything common to every run has its own
    `run_log` column instead (company, model, budget, tool_calls, error_message, ...)."""
    usage = result.usage
    return {
        "stop": result.stop,
        "error_type": result.error_type,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "web_searches": usage.web_searches,
        "cost_usd": round(float(result.cost_usd), 6),
        "sources": ledger.snapshot(),
    }


def _step_row(run_id: int, step: StepRecord) -> RunLogStep:
    usage = step.usage
    return RunLogStep(
        run_log_id=run_id,
        iteration=step.iteration,
        created_at=step.at,
        stop_reason=step.stop_reason,
        tool_name=",".join(step.tool_names) or None,
        tool_errors=step.tool_errors,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        web_searches=usage.web_searches,
        cost_usd=round(float(step.cost_usd), 6),
        response_text=step.response_text,
        tool_error_text=step.tool_error_text or None,
    )


def run_recorded(
    session: Session,
    company: Company,
    kind: RunKind,
    deps: Deps,
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[Tool],
    budget: Budget,
    ledger: SourceLedger,
    max_tokens: int,
    persist: Callable[[Any], Mapping[str, Any] | None],
    failed: type[AgentFailed] = AgentFailed,
) -> LoopResult:
    """Run one agent over `company` and record it in `run_log` and `run_log_step`.

    On a submitted payload, calls `persist(payload)` inside the run block (so a persist error
    marks the run failed); a mapping it returns is merged into the run's `stats`. Any other
    outcome raises `failed` (default `AgentFailed`) after the run row is committed: a failed run
    still cost money and that must stay on record.
    """
    with logged_run(session, kind.value) as run:
        result = run_agent(
            deps.client,
            model=deps.model,
            system=system,
            messages=messages,
            tools=tools,
            budget=budget,
            max_tokens=max_tokens,
            effort=deps.effort,
            on_search_urls=lambda urls: [ledger.add(u, fetched=False) for u in urls],
        )
        run.stats = _stats(result, ledger)
        run.company_id = company.id
        run.model = deps.model
        run.effort = deps.effort
        run.iterations = result.iterations
        run.repairs = result.repairs
        run.max_iterations = budget.max_iterations
        run.max_usd = float(budget.max_usd)
        run.tool_calls = result.tool_calls
        run.error_message = result.error
        failure = failure_category_for(result.stop)
        run.failure_category = failure.value if failure else None
        assert run.id is not None  # logged_run commits the row before yielding
        session.add_all(_step_row(run.id, step) for step in result.steps)
        # logged_run rolls back on exceptions, so commit stats, category and steps before
        # raising: a failed run still cost money and that must stay on record.
        session.commit()
        if result.stop != "submitted" or result.payload is None:
            raise failed(result.stop, result.cost_usd, result.error, result.error_type)
        extra_stats = persist(result.payload)
        if extra_stats:
            run.stats = {**run.stats, **extra_stats}
    return result
