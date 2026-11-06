import httpx
import pytest

from sprout.sources.http import FetchError
from sprout.sources.web import (
    FetchBlockedError,
    UrlPolicy,
    WebFetcher,
    _is_public_address,
    html_to_text,
)

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


def no_request(request: httpx.Request) -> httpx.Response:
    pytest.fail(f"no request expected: {request.url}")


def echo_host(host: str) -> list[str]:
    return [host]  # what getaddrinfo returns for an IP literal


@pytest.mark.parametrize(
    "url",
    [
        "ftp://acme.example/",
        "file:///etc/passwd",
        "gopher://acme.example/",
        "javascript:alert(1)",
        "data:text/html,<p>x</p>",
        "https:///x",
        "http://user:pw@acme.example/",
        "http://user@acme.example/",
        "http://localhost/",
        "http://LOCALHOST./",
        "http://app.localhost/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://db.internal/",
        "http://printer.local/",
        "http://router.home.arpa/",
        "http://acme.example:22/",
        "http://acme.example:8080/",
        "http://acme.example:notaport/",
    ],
)
def test_policy_rejects_urls_before_any_lookup_or_request(make_http, url):
    def resolver(host: str) -> list[str]:
        pytest.fail(f"no lookup expected: {host}")

    fetcher = WebFetcher(make_http(no_request), resolver=resolver)
    with pytest.raises(FetchBlockedError):
        fetcher.fetch(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:7f00:1]/",
        "http://[64:ff9b::7f00:1]/",
        "http://[64:ff9b::a9fe:a9fe]/",
        "http://[fe80::1]/",
    ],
)
def test_private_ip_literals_are_blocked(make_http, url):
    fetcher = WebFetcher(make_http(no_request), resolver=echo_host)
    with pytest.raises(FetchBlockedError, match="public"):
        fetcher.fetch(url)


@pytest.mark.parametrize(
    "url", ["http://2130706433/", "http://0177.0.0.1/", "http://0x7f.0.0.1/", "http://0x7f000001/"]
)
def test_alternative_ipv4_notations_are_blocked_via_the_resolver(make_http, url):
    # getaddrinfo normalises these to 127.0.0.1; the check runs on the resolved address.
    fetcher = WebFetcher(make_http(no_request), resolver=lambda host: ["127.0.0.1"])
    with pytest.raises(FetchBlockedError, match="public"):
        fetcher.fetch(url)


def test_host_with_any_private_record_is_blocked(make_http):
    fetcher = WebFetcher(make_http(no_request), resolver=lambda host: ["93.184.216.34", "10.0.0.5"])
    with pytest.raises(FetchBlockedError, match="public"):
        fetcher.fetch("https://acme.example/")


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:2800:220:1:248:1893:25c8:1946", True),
        ("::ffff:93.184.216.34", True),
        ("64:ff9b::5db8:d822", True),
        ("::ffff:127.0.0.1", False),
        ("64:ff9b::7f00:1", False),
        ("100.64.0.1", False),
        ("169.254.169.254", False),
        ("0.0.0.0", False),
        ("::1", False),
    ],
)
def test_is_public_address_looks_through_embedded_ipv4(address, public):
    assert _is_public_address(address) is public


def test_allow_hosts_blocks_everything_else(make_http):
    pages = {"/robots.txt": httpx.Response(404), "/": html_response()}
    policy = UrlPolicy(allow_hosts=frozenset({"acme.example"}))
    fetcher = WebFetcher(make_http(site(pages)), resolver=PUBLIC, policy=policy)
    assert fetcher.fetch("https://acme.example/").text
    with pytest.raises(FetchBlockedError, match="allowed"):
        fetcher.fetch("https://other.example/")


def test_default_policy_allows_explicit_standard_ports(make_http):
    pages = {"/robots.txt": httpx.Response(404), "/": html_response()}
    fetcher = WebFetcher(make_http(site(pages)), resolver=PUBLIC)
    assert fetcher.fetch("https://acme.example:443/").text


@pytest.mark.parametrize(
    "location", ["http://localhost/x", "http://169.254.169.254/", "http://acme.example:22/"]
)
def test_redirect_to_a_policy_violation_is_blocked(make_http, location):
    pages = {
        "/robots.txt": httpx.Response(404),
        "/evil": httpx.Response(302, headers={"location": location}),
    }
    fetcher = WebFetcher(make_http(site(pages), follow_redirects=False), resolver=echo_public)
    with pytest.raises(FetchBlockedError):
        fetcher.fetch("https://acme.example/evil")


def echo_public(host: str) -> list[str]:
    return ["169.254.169.254"] if host == "169.254.169.254" else ["93.184.216.34"]


def test_robots_txt_redirect_to_a_private_host_is_blocked(make_http):
    pages = {
        "/robots.txt": httpx.Response(302, headers={"location": "http://localhost/robots.txt"})
    }
    fetcher = WebFetcher(make_http(site(pages), follow_redirects=False), resolver=PUBLIC)
    with pytest.raises(FetchBlockedError):
        fetcher.fetch("https://acme.example/")


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"])
def test_non_public_hosts_are_blocked_without_any_request(make_http, address):
    fetcher = WebFetcher(make_http(no_request), resolver=lambda host: [address])
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


def test_text_cap_defaults_to_20000_characters(make_http):
    body = "<p>" + "a" * 30_000 + "</p>"
    pages = {"/robots.txt": httpx.Response(404), "/": html_response(body)}
    fetcher = WebFetcher(make_http(site(pages)), resolver=PUBLIC)
    assert len(fetcher.fetch("https://acme.example/").text) == 20_000


def test_page_over_the_byte_cap_fails(make_http):
    pages = {"/robots.txt": httpx.Response(404), "/": html_response("a" * 5_000_001)}
    fetcher = WebFetcher(make_http(site(pages)), resolver=PUBLIC)
    with pytest.raises(FetchError, match="larger than 5000000"):
        fetcher.fetch("https://acme.example/")
