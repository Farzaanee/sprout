"""Hand-written tool-use loop on the Anthropic Messages API.

This is the only pipeline module that imports `anthropic`, so SDK drift, error types and the
test seam live in one file. The loop is generic: tools are plain specs plus handlers, a terminal
tool ends the run, and the caller owns what a valid submission means.

Guards are checked before every API call, so the iteration cap is exact and the GBP cap is soft
by at most one response (cost is only known after the call; `max_tokens` bounds the overshoot).
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol

import anthropic

from sprout.costs import Usage, cost_gbp

Stop = Literal[
    "submitted", "invalid", "max_iter", "budget", "no_submit", "truncated", "refused", "error"
]

CACHE_MARKER = {"type": "ephemeral"}
WRAP_UP_FRACTION = Decimal("0.8")
REQUEST_TIMEOUT_SECONDS = 120.0

NO_SUBMIT_REMINDER = (
    "You have not submitted a profile. Call the submit_profile tool now with what you have "
    "found. Use null values for anything you could not source."
)
WRAP_UP_NOTICE = (
    "Budget nearly used. Call submit_profile now with what you have; null is fine for the rest."
)


class MessagesClient(Protocol):
    """What the loop needs from a client: `client.messages.create(**kwargs)`. The test seam."""

    messages: Any


def make_client(api_key: str) -> MessagesClient:
    """Real client. The SDK retries 408/409/429/5xx and connection errors twice by default."""
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)


@dataclass(frozen=True)
class Budget:
    max_iterations: int
    max_gbp: Decimal
    max_repairs: int = 1

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if self.max_gbp <= 0:
            raise ValueError("max_gbp must be positive")


@dataclass(frozen=True)
class ToolOutcome:
    text: str
    is_error: bool = False
    payload: Any = None  # on a terminal tool: set means "submission accepted"


@dataclass(frozen=True)
class Tool:
    spec: dict[str, Any]  # sent to the API (custom tool or server-side tool type)
    handler: Callable[[dict[str, Any]], ToolOutcome] | None = None  # None for server-side tools
    terminal: bool = False


@dataclass
class LoopResult:
    payload: Any | None = None
    usage: Usage = field(default_factory=Usage)
    cost_gbp: Decimal = Decimal(0)
    iterations: int = 0
    repairs: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)
    stop: Stop = "error"
    error: str | None = None


def _usage_of(response: Any) -> Usage:
    raw = response.usage
    server = getattr(raw, "server_tool_use", None)
    return Usage(
        input_tokens=getattr(raw, "input_tokens", 0) or 0,
        output_tokens=getattr(raw, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
        web_searches=getattr(server, "web_search_requests", 0) or 0,
    )


def _search_urls(content: list[Any]) -> list[str]:
    """URLs in server-side search results (an error result carries no list)."""
    urls: list[str] = []
    for block in content:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        results = block.content
        if isinstance(results, list):
            urls.extend(r.url for r in results if getattr(r, "url", None))
    return urls


def _run_tool(tool: Tool | None, name: str, tool_input: dict[str, Any]) -> ToolOutcome:
    if tool is None or tool.handler is None:
        return ToolOutcome(f"Unknown tool {name!r}.", is_error=True)
    try:
        return tool.handler(tool_input)
    except Exception as exc:  # a tool bug must reach the model as an error, never end the run
        return ToolOutcome(f"{name} failed: {type(exc).__name__}: {exc}", is_error=True)


def _tool_result(block_id: str, outcome: ToolOutcome) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": block_id,
        "content": outcome.text,
    }
    if outcome.is_error:
        result["is_error"] = True
    return result


def run_agent(
    client: MessagesClient,
    *,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[Tool],
    budget: Budget,
    max_tokens: int,
    effort: str,
    on_search_urls: Callable[[list[str]], None] | None = None,
) -> LoopResult:
    cost_gbp(model, Usage())  # fail closed on an unpriced model before spending anything
    by_name = {t.spec["name"]: t for t in tools}
    specs = [t.spec for t in tools]
    history = list(messages)
    pending: list[dict[str, Any]] = []  # blocks for the next user message
    calls: Counter[str] = Counter()
    result = LoopResult()
    reminded = False

    def finish(stop: Stop, error: str | None = None) -> LoopResult:
        result.stop, result.error = stop, error
        result.tool_calls = dict(calls)
        return result

    while True:
        if result.iterations >= budget.max_iterations:
            return finish("max_iter", f"iteration cap of {budget.max_iterations} reached")
        if result.cost_gbp >= budget.max_gbp:
            return finish("budget", f"GBP cap of {budget.max_gbp} reached")
        if pending:
            last_iteration = result.iterations + 1 >= budget.max_iterations
            if last_iteration or result.cost_gbp >= budget.max_gbp * WRAP_UP_FRACTION:
                pending.append({"type": "text", "text": WRAP_UP_NOTICE})
            history.append({"role": "user", "content": pending})
            pending = []

        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                # Static prefix (tools, then system) is cached; the top-level marker also caches
                # the growing conversation so each turn re-reads earlier turns at the read price.
                system=[{"type": "text", "text": system, "cache_control": CACHE_MARKER}],
                tools=specs,
                tool_choice={"type": "auto"},
                output_config={"effort": effort},
                cache_control=CACHE_MARKER,
                messages=history,
            )
        except anthropic.APIError as exc:
            return finish("error", f"{type(exc).__name__}: {exc}"[:500])

        result.iterations += 1
        result.usage = result.usage + _usage_of(response)
        result.cost_gbp = cost_gbp(model, result.usage)
        content = list(response.content)
        if content:
            history.append({"role": "assistant", "content": content})
        if on_search_urls is not None:
            urls = _search_urls(content)
            if urls:
                on_search_urls(urls)

        stop_reason = response.stop_reason
        if stop_reason == "pause_turn":
            continue
        if stop_reason == "max_tokens":
            return finish("truncated", "response hit max_tokens")
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            return finish("refused", f"the model declined the request (category: {category})")

        uses = [b for b in content if getattr(b, "type", None) == "tool_use"]
        if stop_reason == "tool_use" and uses:
            for block in uses:
                tool = by_name.get(block.name)
                outcome = _run_tool(tool, block.name, dict(block.input))
                calls[block.name] += 1
                if tool is not None and tool.terminal:
                    if outcome.payload is not None:
                        result.payload = outcome.payload
                        return finish("submitted")
                    if outcome.is_error:
                        if result.repairs >= budget.max_repairs:
                            return finish("invalid", outcome.text)
                        result.repairs += 1  # one repair attempt: the error goes back to the model
                pending.append(_tool_result(block.id, outcome))
            continue

        if stop_reason == "end_turn" or (stop_reason == "tool_use" and not uses):
            if reminded:
                return finish("no_submit", "the model stopped without calling submit_profile")
            reminded = True
            pending = [{"type": "text", "text": NO_SUBMIT_REMINDER}]
            continue

        return finish("error", f"unexpected stop_reason {stop_reason!r}")
