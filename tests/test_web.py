import httpx
import pytest

from sprout.sources.http import FetchError
from sprout.sources.web import FetchBlockedError, WebFetcher, html_to_text

HTML = """<html><head><title> Acme  Ltd </title><style>p{color:red}</style></head>
<body><script>var secret = 1;</script>
<h1>Acme</h1><p>We build   <b>AI</b> agents.</p><ul><li>One</li><li>Two</li></ul>
<noscript>enable js</noscript></body></html>"""

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def site(pages: dict[str, httpx.Response], log: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(str(request.url))
        return pages.get(request.url.path, httpx.Response(404))

    return handler


def html_response(body: str = HTML, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})


def test_html_to_text_drops_scripts_and_keeps_structure():
    title, text = html_to_text(HTML)
    assert title == "Acme Ltd"
    assert text == "Acme\nWe build AI agents.\nOne\nTwo"


def test_fetch_returns_text_and_caches(make_http):
    log: list[str] = []
    pages = {"/robots.txt": httpx.Response(404), "/": html_response()}
    fetcher = WebFetcher(make_http(site(pages, log)), resolver=PUBLIC)
    page = fetcher.fetch("https://acme.example/")
    assert page.title == "Acme Ltd" and "AI agents" in page.text and not page.from_cache
    assert fetcher.fetch("https://acme.example/").from_cache
    assert log == ["https://acme.example/robots.txt", "https://acme.example/"]


def test_robots_txt_disallow_is_respected(make_http):
    pages = {
        "/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /private"),
        "/private": html_response(),
        "/public": html_response(),
    }
    fetcher = WebFetcher(make_http(site(pages)), resolver=PUBLIC)
    assert fetcher.fetch("https://acme.example/public").text
    with pytest.raises(FetchBlockedError, match="robots.txt"):
        fetcher.fetch("https://acme.example/private")


def test_unreadable_robots_txt_blocks_but_forbidden_robots_allows(make_http):
    server_error = WebFetcher(
        make_http(site({"/robots.txt": httpx.Response(500)}), max_retries=0), resolver=PUBLIC
    )
    with pytest.raises(FetchBlockedError, match="robots.txt"):
        server_error.fetch("https://acme.example/")

    pages = {"/robots.txt": httpx.Response(403), "/": html_response()}
    assert WebFetcher(make_http(site(pages)), resolver=PUBLIC).fetch("https://acme.example/").text


@pytest.mark.parametrize("url", ["ftp://acme.example/", "file:///etc/passwd", "https:///x"])
def test_only_http_urls_are_fetched(make_http, url):
    fetcher = WebFetcher(make_http(site({})), resolver=PUBLIC)
    with pytest.raises(FetchBlockedError):
        fetcher.fetch(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"])
def test_non_public_hosts_are_blocked_without_any_request(make_http, address):
    fetcher = WebFetcher(
        make_http(lambda r: pytest.fail("no request expected")), resolver=lambda host: [address]
    )
    with pytest.raises(FetchBlockedError, match="public"):
        fetcher.fetch("https://internal.example/")


def test_redirects_are_followed_and_rechecked(make_http):
    resolved: list[str] = []

    def resolver(host: str) -> list[str]:
        resolved.append(host)
        return ["10.0.0.1"] if host == "internal.example" else ["93.184.216.34"]

    pages = {
        "/robots.txt": httpx.Response(404),
        "/go": httpx.Response(302, headers={"location": "/landing"}),
        "/landing": html_response(),
        "/evil": httpx.Response(302, headers={"location": "https://internal.example/x"}),
    }
    http = make_http(site(pages), follow_redirects=False)
    fetcher = WebFetcher(http, resolver=resolver)
    assert fetcher.fetch("https://acme.example/go").url == "https://acme.example/landing"
    with pytest.raises(FetchBlockedError):
        fetcher.fetch("https://acme.example/evil")
    assert "internal.example" in resolved


def test_redirect_loops_stop(make_http):
    pages = {
        "/robots.txt": httpx.Response(404),
        "/a": httpx.Response(302, headers={"location": "/a"}),
    }
    fetcher = WebFetcher(make_http(site(pages), follow_redirects=False), resolver=PUBLIC)
    with pytest.raises(FetchError, match="redirects"):
        fetcher.fetch("https://acme.example/a")


def test_non_text_content_and_404_are_errors(make_http):
    pages = {
        "/robots.txt": httpx.Response(404),
        "/file.pdf": httpx.Response(
            200, content=b"%PDF", headers={"content-type": "application/pdf"}
        ),
    }
    fetcher = WebFetcher(make_http(site(pages)), resolver=PUBLIC)
    with pytest.raises(FetchError, match="content type"):
        fetcher.fetch("https://acme.example/file.pdf")
    with pytest.raises(FetchError) as err:
        fetcher.fetch("https://acme.example/nope")
    assert err.value.status_code == 404


def test_text_is_truncated(make_http):
    pages = {"/robots.txt": httpx.Response(404), "/": html_response("<p>" + "a" * 500 + "</p>")}
    fetcher = WebFetcher(make_http(site(pages)), resolver=PUBLIC, max_chars=100)
    assert len(fetcher.fetch("https://acme.example/").text) == 100
