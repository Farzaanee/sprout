from datetime import date

import httpx
import pytest

from sprout.sources.http import FetchError
from sprout.sources.rss import Feed, fetch_feed, normalise_url, parse_feed

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
<item>
  <title>  Acme raises   £5m  </title>
  <link>https://news.example/acme?utm_source=x&amp;id=7#top</link>
  <pubDate>Wed, 14 Oct 2026 09:35:18 +0000</pubDate>
  <description>&lt;p&gt;Acme builds &lt;b&gt;agents&lt;/b&gt;.&lt;/p&gt;</description>
</item>
<item><title>No link here</title></item>
<item><title>Bad link</title><link>ftp://news.example/x</link></item>
<item><title>Undated</title><link>https://news.example/undated</link></item>
</channel></rss>"""


def test_normalise_url_strips_tracking_and_fragment():
    assert (
        normalise_url("https://news.example/a?utm_source=x&id=7&fbclid=1#top")
        == "https://news.example/a?id=7"
    )


def test_parse_feed_normalises_items_and_skips_invalid_ones():
    items = parse_feed(RSS, "Example")
    assert [i.title for i in items] == ["Acme raises £5m", "Undated"]
    first = items[0]
    assert str(first.url) == "https://news.example/acme?id=7"
    assert first.published == date(2026, 10, 14)
    assert first.summary == "Acme builds agents."
    assert first.source == "Example"
    assert items[1].published is None


def test_long_summaries_are_truncated():
    xml = RSS.replace("&lt;p&gt;Acme builds &lt;b&gt;agents&lt;/b&gt;.&lt;/p&gt;", "word " * 400)
    assert len(parse_feed(xml, "Example")[0].summary) <= 500


def test_garbage_is_rejected():
    with pytest.raises(FetchError):
        parse_feed("<html><body>not a feed</body></html>", "Example")


def test_fetch_feed_uses_the_cache(make_http):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, text=RSS, headers={"content-type": "application/rss+xml"})

    http = make_http(handler)
    feed = Feed("Example", "https://news.example/feed")
    assert len(fetch_feed(http, feed)) == 2
    assert len(fetch_feed(http, feed)) == 2
    assert len(calls) == 1


def test_missing_feed_raises(make_http):
    http = make_http(lambda r: httpx.Response(404))
    with pytest.raises(FetchError, match="not found"):
        fetch_feed(http, Feed("Gone", "https://news.example/feed"))
