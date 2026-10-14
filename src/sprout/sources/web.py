"""Polite web fetcher: robots.txt, per-domain rate limit, disk cache, HTML to text.

URLs may come from scraped content (untrusted), so only public http(s) hosts are fetched and
every redirect hop is re-checked against the same rules.
"""

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from sprout.sources.http import (
    USER_AGENT,
    DiskCache,
    FetchError,
    HttpClient,
    PerHostRateLimiter,
    Response,
)

DEFAULT_MIN_INTERVAL_SECONDS = 2.0
DEFAULT_CACHE_TTL = timedelta(days=7)
ROBOTS_TTL = timedelta(days=1)
DEFAULT_MAX_CHARS = 20_000
MAX_REDIRECTS = 5
TEXT_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")

_SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "footer",
        "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol",
        "p", "pre", "section", "table", "td", "th", "tr", "ul",
    }
)  # fmt: skip


class FetchBlockedError(FetchError):
    """We chose not to fetch: robots.txt, a non-public host, or an unsupported URL."""


@dataclass(frozen=True)
class Page:
    url: str
    title: str | None
    text: str
    retrieved_at: datetime
    from_cache: bool


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._parts).splitlines())
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> tuple[str | None, str]:
    """Return (title, visible text) with scripts, styles and markup removed."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    title = " ".join("".join(parser.title_parts).split()) or None
    return title, parser.text()


def resolve_host(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def make_http_client(
    cache_dir: Path | None = None, min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS
) -> HttpClient:
    # Redirects are followed by WebFetcher so each hop can be checked.
    client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=False)
    return HttpClient(
        client=client,
        cache=DiskCache(cache_dir) if cache_dir else None,
        limiter=PerHostRateLimiter(1, min_interval),
    )


class WebFetcher:
    def __init__(
        self,
        http: HttpClient,
        *,
        user_agent: str = USER_AGENT,
        resolver: Callable[[str], list[str]] = resolve_host,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self._http = http
        self._user_agent = user_agent
        self._resolver = resolver
        self._cache_ttl = cache_ttl
        self._max_chars = max_chars
        self._robots: dict[str, RobotFileParser] = {}

    def fetch(self, url: str) -> Page:
        response, current = self._get_following_redirects(
            url, max_age=self._cache_ttl, check=self._check_allowed
        )
        if response.status_code == 404:
            raise FetchError(f"GET {current} returned 404", status_code=404)
        content_type = (response.content_type or "text/html").split(";")[0].strip().lower()
        if content_type not in TEXT_CONTENT_TYPES:
            raise FetchError(f"GET {current}: unsupported content type {content_type!r}")

        if content_type == "text/plain":
            title, text = None, response.text
        else:
            title, text = html_to_text(response.text)
        return Page(
            url=current,
            title=title,
            text=text[: self._max_chars],
            retrieved_at=datetime.now(UTC),
            from_cache=response.from_cache,
        )

    def _get_following_redirects(
        self, url: str, *, max_age: timedelta, check: Callable[[str], None]
    ) -> tuple[Response, str]:
        """GET `url`, running `check` on it and on every redirect target."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            check(current)
            response = self._http.get(current, max_age=max_age)
            if not 300 <= response.status_code < 400:
                return response, current
            if not response.location:
                raise FetchError(f"GET {current}: redirect without a Location header")
            current = urljoin(current, response.location)
        raise FetchError(f"GET {url}: more than {MAX_REDIRECTS} redirects")

    def _check_public_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchBlockedError(f"only http(s) URLs can be fetched: {url}")
        self._check_public_host(parts.hostname)

    def _check_allowed(self, url: str) -> None:
        self._check_public_url(url)
        parts = urlsplit(url)
        if not self._robots_for(parts.scheme, parts.netloc).can_fetch(self._user_agent, url):
            raise FetchBlockedError(f"robots.txt disallows fetching {url}")

    def _check_public_host(self, host: str) -> None:
        try:
            addresses = self._resolver(host)
        except OSError as exc:
            raise FetchError(f"could not resolve {host}: {exc}") from exc
        if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):
            raise FetchBlockedError(f"{host} does not resolve to a public address")

    def _robots_for(self, scheme: str, netloc: str) -> RobotFileParser:
        origin = f"{scheme}://{netloc}"
        if origin in self._robots:
            return self._robots[origin]
        parser = RobotFileParser()
        try:
            response, _ = self._get_following_redirects(
                f"{origin}/robots.txt", max_age=ROBOTS_TTL, check=self._check_public_url
            )
        except FetchBlockedError:
            raise
        except FetchError as exc:
            status = exc.status_code
            if status is not None and 400 <= status < 500 and status != 429:
                parser.parse([])  # robots.txt is unavailable: everything is allowed (RFC 9309)
            else:
                raise FetchBlockedError(f"cannot read robots.txt for {origin}: {exc}") from exc
        else:
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
        self._robots[origin] = parser
        return parser
