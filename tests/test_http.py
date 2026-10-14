from datetime import timedelta

import httpx
import pytest

from sprout.sources.http import DiskCache, FetchError, PerHostRateLimiter, RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_second_identical_request_is_served_from_cache(make_http):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, json={"ok": True})

    http = make_http(handler)
    first = http.get("https://example.com/a", params={"b": 2, "a": 1})
    second = http.get("https://example.com/a", params={"a": 1, "b": 2})  # same request, new order
    assert first.json() == second.json() == {"ok": True}
    assert not first.from_cache and second.from_cache
    assert len(calls) == 1
    assert (http.requests_made, http.cache_hits) == (1, 1)


def test_expired_cache_entries_are_refetched(make_http):
    calls = []
    http = make_http(lambda r: calls.append(r) or httpx.Response(200, text="x"))
    http.get("https://example.com/")
    http.get("https://example.com/", max_age=timedelta(seconds=-1))
    assert len(calls) == 2


def test_404_is_returned_and_cached_but_other_4xx_raise(make_http):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404 if request.url.path == "/missing" else 403)

    http = make_http(handler)
    assert http.get("https://example.com/missing").status_code == 404
    assert http.get("https://example.com/missing").from_cache
    with pytest.raises(FetchError) as err:
        http.get("https://example.com/forbidden")
    assert err.value.status_code == 403
    assert calls == ["/missing", "/forbidden"]  # 403 was not retried


def test_retries_with_backoff_then_succeeds(make_http):
    responses = iter([httpx.Response(503), httpx.Response(429, headers={"Retry-After": "7"})])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses, httpx.Response(200, text="done"))

    http = make_http(handler, sleep=sleeps.append, backoff_base=1.0)
    assert http.get("https://example.com/").text == "done"
    assert sleeps == [1.0, 7.0]  # exponential backoff, raised to Retry-After
    assert http.requests_made == 3


def test_gives_up_after_max_retries(make_http):
    http = make_http(lambda r: httpx.Response(500), max_retries=2)
    with pytest.raises(FetchError, match="3 attempts"):
        http.get("https://example.com/")
    assert http.requests_made == 3


def test_transport_errors_are_retried(make_http):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, text="ok")

    assert make_http(handler).get("https://example.com/").text == "ok"


def test_oversized_response_is_rejected(make_http):
    http = make_http(lambda r: httpx.Response(200, content=b"x" * 100), max_bytes=10)
    with pytest.raises(FetchError, match="larger than"):
        http.get("https://example.com/")


def test_failed_responses_are_not_cached(make_http):
    responses = iter([httpx.Response(500), httpx.Response(200, text="ok")])
    http = make_http(lambda r: next(responses), max_retries=0)
    with pytest.raises(FetchError):
        http.get("https://example.com/")
    assert http.get("https://example.com/").text == "ok"


def test_corrupt_cache_entry_is_a_miss(tmp_path):
    cache = DiskCache(tmp_path)
    key = DiskCache.key("https://example.com/")
    (tmp_path / f"{key}.json").write_text("{not json")
    assert cache.get(key) is None


def test_rate_limiter_waits_for_the_window_to_clear():
    clock = FakeClock()
    limiter = RateLimiter(2, 10.0, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        limiter.acquire()
    assert clock.slept == [10.0]


def test_per_host_limiter_keeps_hosts_independent():
    clock = FakeClock()
    limiter = PerHostRateLimiter(1, 5.0, clock=clock, sleep=clock.sleep)
    limiter.acquire("https://a.example/x")
    limiter.acquire("https://b.example/x")
    assert clock.slept == []
    limiter.acquire("https://a.example/y")
    assert clock.slept == [5.0]
