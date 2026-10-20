"""A scripted stand-in for the Anthropic client: queued responses, recorded requests."""

from types import SimpleNamespace
from typing import Any


def usage(
    input: int = 0, output: int = 0, cache_read: int = 0, cache_write: int = 0, searches: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input,
        output_tokens=output,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        server_tool_use=SimpleNamespace(web_search_requests=searches),
    )


def response(content: list[Any], stop_reason: str, **usage_kwargs: int) -> SimpleNamespace:
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage(**usage_kwargs))


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def tool_use_block(name: str, tool_input: dict[str, Any], id: str = "tu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=tool_input)


def search_blocks(urls: list[str]) -> list[SimpleNamespace]:
    """The server-side search pair: a `server_tool_use` call and its result list."""
    results = [SimpleNamespace(type="web_search_result", url=u, title="t") for u in urls]
    return [
        SimpleNamespace(
            type="server_tool_use", id="srv_1", name="web_search", input={"query": "q"}
        ),
        SimpleNamespace(type="web_search_tool_result", tool_use_id="srv_1", content=results),
    ]


def text_turn(text: str = "done", stop_reason: str = "end_turn", **u: int) -> SimpleNamespace:
    return response([text_block(text)], stop_reason, **u)


def tool_use_turn(*calls: tuple[str, dict[str, Any]], **u: int) -> SimpleNamespace:
    blocks = [tool_use_block(n, i, id=f"tu_{k}") for k, (n, i) in enumerate(calls, 1)]
    return response(blocks, "tool_use", **u)


def server_search_turn(urls: list[str], *then: tuple[str, dict[str, Any]], **u: int):
    """A turn that ran a server-side search, then (optionally) calls our tools."""
    blocks = search_blocks(urls)
    blocks += [tool_use_block(n, i, id=f"tu_{k}") for k, (n, i) in enumerate(then, 1)]
    return response(blocks, "tool_use" if then else "end_turn", **u)


def pause_turn(urls: list[str] | None = None, **u: int) -> SimpleNamespace:
    return response(search_blocks(urls or []), "pause_turn", **u)


class _Messages:
    def __init__(self, owner: "ScriptedClient") -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        owner = self._owner
        # Snapshot the history list: the loop keeps appending to the same list object.
        owner.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        if not owner.queue:
            raise AssertionError("ScriptedClient: more calls than scripted responses")
        item = owner.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ScriptedClient:
    def __init__(self, *responses: Any) -> None:
        self.queue = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.messages = _Messages(self)

    @property
    def calls(self) -> int:
        return len(self.requests)
