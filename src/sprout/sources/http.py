"""Resilient HTTP GET: disk cache, rate limiting, retries with backoff.

Every network source (Companies House, RSS, the web fetcher) goes through `HttpClient`, so
caching and politeness are implemented once. Credentials are passed to the underlying
`httpx.Client` and are never part of a cache key or a cache file.
"""

import hashlib
import json
import os
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

import httpx

USER_AGENT = "sprout/0.1 (UK AI startup research project)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 60.0
DEFAULT_MAX_BYTES = 5_000_000


class FetchError(RuntimeError):
    """A request failed for good (non-retryable status, or retries exhausted)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Response:
    url: str
    status_code: int
    text: str
    content_type: str | None = None
    location: str | None = None
    from_cache: bool = False

    def json(self) -> Any:
        return json.loads(self.text)


class RateLimiter:
    """Sliding window: at most `max_calls` calls in any `period` seconds."""

    def __init__(
        self,
        max_calls: int,
        period: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_calls < 1 or period <= 0:
            raise ValueError("max_calls must be >= 1 and period > 0")
        self._max_calls = max_calls
        self._period = period
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()

    def acquire(self, url: str | None = None) -> None:
        """Block until a call is allowed. `url` is ignored (see `PerHostRateLimiter`)."""
        while True:
            now = self._clock()
            while self._calls and now - self._calls[0] >= self._period:
                self._calls.popleft()
            if len(self._calls) < self._max_calls:
                self._calls.append(now)
                return
            self._sleep(self._period - (now - self._calls[0]))


class PerHostRateLimiter:
    """An independent `RateLimiter` for each host, so one slow site never delays another."""

    def __init__(
        self,
        max_calls: int,
        period: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._factory = lambda: RateLimiter(max_calls, period, clock=clock, sleep=sleep)
        self._limiters: dict[str, RateLimiter] = {}

    def acquire(self, url: str | None = None) -> None:
        host = httpx.URL(url).host if url else ""
        self._limiters.setdefault(host, self._factory()).acquire()


class DiskCache:
    """One JSON file per request, named by a hash of the request."""

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)

    @staticmethod
    def key(url: str, params: Mapping[str, Any] | None = None) -> str:
        ordered = sorted((k, str(v)) for k, v in (params or {}).items())
        return hashlib.sha256(str(httpx.URL(url, params=ordered)).encode()).hexdigest()

    def get(self, key: str, max_age: timedelta | None = None) -> Response | None:
        try:
            entry = json.loads(self._path(key).read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(entry["fetched_at"])
            response = Response(
                url=entry["url"],
                status_code=entry["status_code"],
                text=entry["text"],
                content_type=entry.get("content_type"),
                from_cache=True,
            )
        except (OSError, ValueError, KeyError):
            return None  # missing or corrupt entries are simply cache misses
        if max_age is not None and datetime.now(UTC) - fetched_at > max_age:
            return None
        return response

    def put(self, key: str, response: Response) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "url": response.url,
            "status_code": response.status_code,
            "text": response.text,
            "content_type": response.content_type,
            "fetched_at": datetime.now(UTC).isoformat(),
        }
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry), encoding="utf-8")
        os.replace(tmp, path)

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"


class HttpClient:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        cache: DiskCache | None = None,
        limiter: RateLimiter | PerHostRateLimiter | None = None,
        max_retries: int = 4,
        backoff_base: float = 1.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=20.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        self._cache = cache
        self._limiter = limiter
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_bytes = max_bytes
        self._sleep = sleep
        self.requests_made = 0
        self.cache_hits = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        max_age: timedelta | None = None,
    ) -> Response:
        """GET `url`. 2xx and 404 are cached; 3xx and 404 are returned, other errors raise.

        `max_age` bounds how stale a cached entry may be (None = any age).
        """
        key = DiskCache.key(url, params)
        if self._cache is not None and (hit := self._cache.get(key, max_age)) is not None:
            self.cache_hits += 1
            return hit

        response = self._get_with_retries(url, params)
        cacheable = 200 <= response.status_code < 300 or response.status_code == 404
        if self._cache is not None and cacheable:
            self._cache.put(key, response)
        if response.status_code >= 400 and response.status_code != 404:
            raise FetchError(
                f"GET {url} returned {response.status_code}", status_code=response.status_code
            )
        return response

    def _get_with_retries(self, url: str, params: Mapping[str, Any] | None) -> Response:
        attempt = 0
        while True:
            retry_after: float | None = None
            try:
                response, retry_after = self._send(url, params)
            except httpx.TransportError as exc:
                failure, status = f"{type(exc).__name__}: {exc}", None
            else:
                if response.status_code not in RETRY_STATUSES:
                    return response
                failure, status = f"status {response.status_code}", response.status_code
            if attempt >= self._max_retries:
                raise FetchError(
                    f"GET {url} failed after {attempt + 1} attempts ({failure})",
                    status_code=status,
                )
            delay = self._backoff_base * 2**attempt
            if retry_after is not None:
                delay = max(delay, min(retry_after, MAX_RETRY_AFTER_SECONDS))
            self._sleep(delay)
            attempt += 1

    def _send(self, url: str, params: Mapping[str, Any] | None) -> tuple[Response, float | None]:
        if self._limiter is not None:
            self._limiter.acquire(url)
        self.requests_made += 1
        with self._client.stream("GET", url, params=params) as raw:
            body = bytearray()
            for chunk in raw.iter_bytes():
                body += chunk
                if len(body) > self._max_bytes:
                    raise FetchError(f"GET {url}: response larger than {self._max_bytes} bytes")
            header = raw.headers.get("retry-after", "")
            response = Response(
                url=str(raw.url),
                status_code=raw.status_code,
                text=bytes(body).decode(raw.encoding or "utf-8", errors="replace"),
                content_type=raw.headers.get("content-type"),
                location=raw.headers.get("location"),
            )
            return response, float(header) if header.isdigit() else None
