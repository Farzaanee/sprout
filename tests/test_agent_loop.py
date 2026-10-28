from datetime import UTC
from decimal import Decimal

import anthropic
import httpx2
import pytest

from fakes import (
    ScriptedClient,
    pause_turn,
    response,
    server_search_turn,
    text_block,
    text_turn,
    tool_use_turn,
)
from sprout.agents.loop import (
    LOGGED_TEXT_MAX_CHARS,
    Budget,
    Tool,
    ToolOutcome,
    run_agent,
)
from sprout.costs import UnknownModelError, Usage, cost_usd

MODEL = "claude-haiku-5-5"
BUDGET = Budget(max_iterations=6, max_usd=Decimal("1"))


def echo_tool(calls: list | None = None) -> Tool:
    def handler(args):
        if calls is not None:
            calls.append(args)
        return ToolOutcome(f"echo {args.get('q')}")

    return Tool(spec={"name": "echo", "input_schema": {"type": "object"}}, handler=handler)


def submit_tool(accept_on: int = 1) -> Tool:
    """A terminal tool that rejects until its `accept_on`-th call."""
    seen = []

    def handler(args):
        seen.append(args)
        if len(seen) < accept_on:
            return ToolOutcome("bad profile", is_error=True)
        return ToolOutcome("ok", payload={"got": args})

    return Tool(
        spec={"name": "submit", "input_schema": {"type": "object"}}, handler=handler, terminal=True
    )


def search_tool() -> Tool:
    return Tool(spec={"type": "web_search_20260209", "name": "web_search"})


def run(client, tools, budget=BUDGET, **kw):
    return run_agent(
        client,
        model=kw.pop("model", MODEL),
        system="SYS",
        messages=[{"role": "user", "content": "go"}],
        tools=tools,
        budget=budget,
        max_tokens=1000,
        effort="low",
        **kw,
    )


def test_happy_path_sends_cache_markers_and_returns_tool_results():
    client = ScriptedClient(
        tool_use_turn(("echo", {"q": "a"}), input=100, output=50, cache_write=2000),
        tool_use_turn(("submit", {"x": 1}), input=10, output=20, cache_read=2000),
    )
    result = run(client, [echo_tool(), submit_tool()])
    assert result.stop == "submitted" and result.payload == {"got": {"x": 1}}
    assert result.iterations == 2 and result.tool_calls == {"echo": 1, "submit": 1}
    assert result.usage.cache_write_tokens == 2000 and result.usage.cache_read_tokens == 2000
    assert result.cost_usd == cost_usd(MODEL, result.usage) > 0

    first, second = client.requests
    assert first["tool_choice"] == {"type": "auto"}
    assert first["output_config"] == {"effort": "low"}
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [t["name"] for t in first["tools"]] == ["echo", "submit"]
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result == {"type": "tool_result", "tool_use_id": "tu_1", "content": "echo a"}
    assert second["messages"][-2]["role"] == "assistant"


def test_repair_path_feeds_the_error_back_once():
    client = ScriptedClient(
        tool_use_turn(("submit", {"v": 1})), tool_use_turn(("submit", {"v": 2}))
    )
    result = run(client, [submit_tool(accept_on=2)])
    assert result.stop == "submitted" and result.repairs == 1 and result.iterations == 2
    sent = client.requests[1]["messages"][-1]["content"][0]
    assert sent["is_error"] is True and sent["content"] == "bad profile"


def test_invalid_after_repair_stops_without_a_third_call():
    client = ScriptedClient(
        tool_use_turn(("submit", {})), tool_use_turn(("submit", {})), text_turn()
    )
    result = run(client, [submit_tool(accept_on=99)])
    assert result.stop == "invalid" and result.payload is None
    assert result.repairs == 1 and client.calls == 2 and result.error == "bad profile"


def test_iteration_cap_is_exact_and_last_turn_gets_a_wrap_up_notice():
    client = ScriptedClient(*[tool_use_turn(("echo", {"q": i})) for i in range(10)])
    result = run(client, [echo_tool()], Budget(max_iterations=3, max_usd=Decimal("1")))
    assert result.stop == "max_iter" and result.iterations == 3 and client.calls == 3
    notices = [
        [b for b in r["messages"][-1]["content"] if b.get("type") == "text"]
        for r in client.requests[1:]
    ]
    assert notices[0] == []  # second call is not the last one
    assert "submit_profile" in notices[1][0]["text"]


def test_tiny_usd_cap_stops_after_the_first_response():
    client = ScriptedClient(
        *[tool_use_turn(("echo", {}), input=5000, output=500) for _ in range(5)]
    )
    result = run(client, [echo_tool()], Budget(max_iterations=10, max_usd=Decimal("0.000001")))
    assert result.stop == "budget" and client.calls == 1
    assert result.cost_usd > Decimal("0.000001")  # soft cap: overshoot by at most one response


def test_usd_cap_adds_wrap_up_notice_at_80_percent():
    one_call = cost_usd(MODEL, Usage(input_tokens=1000))
    client = ScriptedClient(
        tool_use_turn(("echo", {}), input=1000), tool_use_turn(("echo", {}), input=1000)
    )
    budget = Budget(max_iterations=10, max_usd=one_call * Decimal("1.1"))
    result = run(client, [echo_tool()], budget)
    assert result.stop == "budget" and client.calls == 2
    last = client.requests[1]["messages"][-1]["content"]
    assert any(b.get("type") == "text" for b in last)


def test_pause_turn_resends_history_without_a_new_user_message():
    seen = []
    client = ScriptedClient(
        pause_turn(["https://a.example/x"], searches=1),
        server_search_turn(["https://b.example/y"], ("submit", {}), searches=1),
    )
    result = run(client, [search_tool(), submit_tool()], on_search_urls=seen.extend)
    assert result.stop == "submitted" and result.iterations == 2
    assert result.usage.web_searches == 2
    second = client.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant"]
    assert second[-1]["content"][1].type == "web_search_tool_result"  # blocks kept unchanged
    assert seen == ["https://a.example/x", "https://b.example/y"]


def test_end_turn_without_submission_gets_one_reminder_then_stops():
    client = ScriptedClient(text_turn("I am done"), text_turn("still done"), text_turn())
    result = run(client, [submit_tool()])
    assert result.stop == "no_submit" and client.calls == 2
    assert "submit_profile" in client.requests[1]["messages"][-1]["content"][0]["text"]


def test_reminder_can_rescue_the_run():
    client = ScriptedClient(text_turn("done"), tool_use_turn(("submit", {})))
    assert run(client, [submit_tool()]).stop == "submitted"


@pytest.mark.parametrize(
    ("stop_reason", "expected"), [("max_tokens", "truncated"), ("refusal", "refused")]
)
def test_terminal_stop_reasons(stop_reason, expected):
    client = ScriptedClient(response([], stop_reason, input=10, output=5))
    result = run(client, [submit_tool()])
    assert result.stop == expected and client.calls == 1 and result.usage.output_tokens == 5


def test_truncated_tool_use_is_not_executed():
    calls: list = []
    client = ScriptedClient(tool_use_turn(("echo", {"q": "cut"})))
    client.queue[0].stop_reason = "max_tokens"
    result = run(client, [echo_tool(calls)])
    assert result.stop == "truncated" and calls == []


def test_tool_errors_do_not_raise_and_unknown_tools_are_reported():
    def boom(args):
        raise RuntimeError("kaput")

    tools = [Tool(spec={"name": "boom", "input_schema": {}}, handler=boom), submit_tool()]
    client = ScriptedClient(
        tool_use_turn(("boom", {}), ("nope", {})), tool_use_turn(("submit", {}))
    )
    result = run(client, tools)
    assert result.stop == "submitted"
    results = client.requests[1]["messages"][-1]["content"]
    assert results[0]["is_error"] and "RuntimeError: kaput" in results[0]["content"]
    assert results[1]["is_error"] and "Unknown tool" in results[1]["content"]
    assert len(results) == 2  # one user message carries every result


def test_api_error_keeps_usage_and_does_not_raise():
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x.example"))
    client = ScriptedClient(tool_use_turn(("echo", {}), input=100), error)
    result = run(client, [echo_tool()])
    assert result.stop == "error" and "APIConnectionError" in result.error
    assert result.usage.input_tokens == 100 and result.iterations == 1


def test_unpriced_model_fails_before_any_call():
    client = ScriptedClient()
    with pytest.raises(UnknownModelError):
        run(client, [], model="claude-unpriced")
    assert client.calls == 0


def test_budget_validation():
    with pytest.raises(ValueError):
        Budget(max_iterations=0, max_usd=Decimal("1"))
    with pytest.raises(ValueError):
        Budget(max_iterations=1, max_usd=Decimal("0"))


def test_steps_match_iterations_and_sum_to_the_run_totals():
    client = ScriptedClient(
        tool_use_turn(("echo", {"q": "a"}), input=100, output=50, cache_write=2000),
        pause_turn(["https://a.example/x"], input=7, output=3, searches=1),
        tool_use_turn(("submit", {"x": 1}), input=10, output=20, cache_read=2000),
    )
    result = run(client, [echo_tool(), search_tool(), submit_tool()])
    assert len(result.steps) == result.iterations == 3
    assert [s.iteration for s in result.steps] == [1, 2, 3]
    assert [s.stop_reason for s in result.steps] == ["tool_use", "pause_turn", "tool_use"]
    total = Usage()
    for step in result.steps:
        total = total + step.usage
        assert step.cost_usd == cost_usd(MODEL, step.usage)
        assert step.at.tzinfo is UTC
    assert total == result.usage
    assert sum(s.cost_usd for s in result.steps) == result.cost_usd
    assert result.steps[0].usage == Usage(
        input_tokens=100, output_tokens=50, cache_write_tokens=2000
    )


def test_step_tool_names_keep_order_duplicates_and_server_side_calls():
    client = ScriptedClient(
        server_search_turn(
            ["https://a.example/x"], ("echo", {"q": 1}), ("echo", {"q": 2}), searches=1
        ),
        pause_turn(["https://b.example/y"], searches=1),
        text_turn("I am done"),
        tool_use_turn(("submit", {})),
    )
    result = run(client, [search_tool(), echo_tool(), submit_tool()])
    names = [s.tool_names for s in result.steps]
    assert names == [("web_search", "echo", "echo"), ("web_search",), (), ("submit",)]
    assert result.steps[1].stop_reason == "pause_turn"
    assert (
        result.steps[2].stop_reason == "end_turn" and result.steps[2].response_text == "I am done"
    )


def test_tool_errors_are_counted_per_step_including_a_rejected_submission():
    def boom(args):
        raise RuntimeError("kaput")

    tools = [Tool(spec={"name": "boom", "input_schema": {}}, handler=boom), submit_tool(2)]
    client = ScriptedClient(
        tool_use_turn(("boom", {}), ("nope", {}), ("boom", {})),
        tool_use_turn(("submit", {})),
        tool_use_turn(("submit", {})),
    )
    result = run(client, tools)
    assert result.stop == "submitted"
    assert [s.tool_errors for s in result.steps] == [3, 1, 0]


def test_a_second_rejected_submission_is_counted_before_the_run_ends():
    client = ScriptedClient(tool_use_turn(("submit", {})), tool_use_turn(("submit", {})))
    result = run(client, [submit_tool(accept_on=99)])
    assert result.stop == "invalid"
    assert [s.tool_errors for s in result.steps] == [1, 1]


def test_response_text_joins_text_blocks_and_skips_thinking_and_tool_inputs():
    thinking = type("Thinking", (), {"type": "thinking", "thinking": "SECRET REASONING"})()
    turn = response(
        [
            thinking,
            text_block("first"),
            text_block("second"),
            *tool_use_turn(("echo", {"q": "https://secret.example/url"})).content,
        ],
        "tool_use",
    )
    result = run(ScriptedClient(turn, tool_use_turn(("submit", {}))), [echo_tool(), submit_tool()])
    step = result.steps[0]
    assert step.response_text == "first\nsecond"
    assert "SECRET" not in repr(step) and "secret.example" not in repr(step)


@pytest.mark.parametrize(
    ("length", "truncated"),
    [(LOGGED_TEXT_MAX_CHARS, False), (LOGGED_TEXT_MAX_CHARS + 1, True)],
)
def test_response_text_is_cut_at_the_cap_with_a_marker(length, truncated):
    result = run(ScriptedClient(text_turn("x" * length), text_turn("y")), [submit_tool()])
    text = result.steps[0].response_text
    if truncated:
        assert text == "x" * LOGGED_TEXT_MAX_CHARS + " [truncated]"
    else:
        assert text == "x" * length


def test_api_error_produces_no_step_and_sets_error_type():
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x.example"))
    result = run(ScriptedClient(tool_use_turn(("echo", {}), input=100), error), [echo_tool()])
    assert result.error_type == "APIConnectionError"
    assert len(result.steps) == result.iterations == 1


def test_unexpected_stop_reason_sets_error_type_and_still_logs_its_step():
    result = run(ScriptedClient(response([text_block("hm")], "weird_reason")), [submit_tool()])
    assert result.stop == "error" and result.error_type == "unexpected_stop_reason"
    assert [s.stop_reason for s in result.steps] == ["weird_reason"]


def test_error_type_is_none_when_the_run_does_not_error():
    result = run(ScriptedClient(tool_use_turn(("submit", {}))), [submit_tool()])
    assert result.error_type is None
