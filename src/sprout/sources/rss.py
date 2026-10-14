"""RSS ingestion: UK/European tech and startup feeds normalised to `NewsItem`.

Only titles, links and short summaries are kept, not article bodies.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
from pydantic import ValidationError

from sprout.models import NewsItem
from sprout.sources.http import FetchError, HttpClient
from sprout.sources.web import html_to_text

FEED_CACHE_TTL = timedelta(hours=6)
SUMMARY_MAX_CHARS = 500
_TRACKING_PARAMS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "cmp"})


@dataclass(frozen=True)
class Feed:
    name: str
    url: str


FEEDS = (
    Feed("Sifted", "https://sifted.eu/feed"),
    Feed("UKTN", "https://www.uktech.news/feed"),
    Feed("Tech.eu", "https://tech.eu/feed"),
    Feed("The Guardian: AI", "https://www.theguardian.com/technology/artificialintelligenceai/rss"),
    Feed("TechCrunch: Startups", "https://techcrunch.com/category/startups/feed/"),
    Feed("BBC: Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
)


def normalise_url(url: str) -> str:
    """Drop the fragment and tracking parameters so the same article dedupes by URL."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _summary(raw: str) -> str:
    text = " ".join(html_to_text(raw)[1].split())
    return text if len(text) <= SUMMARY_MAX_CHARS else text[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"


def parse_feed(xml: str, source: str) -> list[NewsItem]:
    """Parse feed XML; entries without a title or a valid http(s) link are skipped."""
    parsed = feedparser.parse(xml)
    if not parsed.version:  # feedparser could not recognise RSS/Atom (e.g. an HTML error page)
        raise FetchError(f"{source}: response is not an RSS/Atom feed")
    items: list[NewsItem] = []
    for entry in parsed.entries:
        timestamp = entry.get("published_parsed") or entry.get("updated_parsed")
        try:
            items.append(
                NewsItem(
                    title=" ".join(str(entry.get("title", "")).split()),
                    url=normalise_url(str(entry.get("link", ""))),
                    published=date(*timestamp[:3]) if timestamp else None,
                    summary=_summary(str(entry.get("summary", ""))),
                    source=source,
                )
            )
        except ValidationError:
            continue
    return items


def fetch_feed(http: HttpClient, feed: Feed) -> list[NewsItem]:
    response = http.get(feed.url, max_age=FEED_CACHE_TTL)
    if response.status_code == 404:
        raise FetchError(f"{feed.name}: feed not found at {feed.url}", status_code=404)
    return parse_feed(response.text, feed.name)
