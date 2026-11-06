"""A `WebFetcher` that serves local pages and never touches the network (injection fixtures)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from sprout.sources.http import HttpClient
from sprout.sources.web import UrlPolicy, WebFetcher

PUBLIC_ADDRESS = "93.184.216.34"


@dataclass(frozen=True)
class _RecordingPolicy(UrlPolicy):
    """Logs every URL the fetcher tries before applying the rules, so a test can show that a
    blocked attacker URL was attempted (the policy rejects it before any request is made)."""

    attempted: list[str] = field(default_factory=list, compare=False)

    def check(self, url: str) -> str:
        self.attempted.append(url)
        return super().check(url)


def fixture_fetcher(pages: Mapping[str, str]) -> tuple[WebFetcher, list[str]]:
    """Serve `pages` (absolute URL to HTML) and return the fetcher plus its attempt log.

    Only the hosts in `pages` may be fetched. Anything else on those hosts, including
    `/robots.txt`, answers 404.
    """
    policy = _RecordingPolicy(allow_hosts=frozenset(urlsplit(url).hostname or "" for url in pages))

    def handler(request: httpx.Request) -> httpx.Response:
        html = pages.get(str(request.url))
        if html is None:
            return httpx.Response(404)
        return httpx.Response(200, text=html, headers={"Content-Type": "text/html"})

    http = HttpClient(
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
        sleep=lambda _: None,
        max_retries=0,
    )
    fetcher = WebFetcher(http, resolver=lambda _host: [PUBLIC_ADDRESS], policy=policy)
    return fetcher, policy.attempted
