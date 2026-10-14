from collections.abc import Callable

import httpx
import pytest

from sprout.sources.http import DiskCache, HttpClient


@pytest.fixture
def make_http(tmp_path) -> Callable[..., HttpClient]:
    """Build an `HttpClient` backed by a mock transport, an on-disk cache and no real sleeping."""

    def factory(handler, *, cache: bool = True, **kwargs) -> HttpClient:
        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=kwargs.pop("follow_redirects", True),
        )
        return HttpClient(
            client=client,
            cache=DiskCache(tmp_path / "cache") if cache else None,
            sleep=kwargs.pop("sleep", lambda _: None),
            **kwargs,
        )

    return factory
