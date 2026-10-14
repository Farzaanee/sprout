"""One-command ingestion: `uv run python -m sprout.ingest`.

Discovers candidate companies from Companies House and pulls news from RSS feeds into SQLite.
Re-running is safe: companies dedupe on company number, news on URL, and HTTP responses are
served from the on-disk cache while fresh.
"""

import argparse
import sys
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

from sqlmodel import Session, func, select

from sprout.candidates import DISCOVERY_TERMS, MIN_INCORPORATION_DATE, assess
from sprout.config import MissingConfigError, get_settings
from sprout.db import (
    Company,
    NewsItemRow,
    add_news_items,
    get_engine,
    init_db,
    logged_run,
    save_fact,
    upsert_company,
)
from sprout.models import Confidence, Sourced
from sprout.sources import companies_house
from sprout.sources.companies_house import CompaniesHouseClient, CompanyHit
from sprout.sources.http import DiskCache, FetchError, HttpClient, PerHostRateLimiter
from sprout.sources.rss import FEEDS, Feed, fetch_feed

DEFAULT_MAX_PER_TERM = 200


def _save_registry_facts(session: Session, company_id: int, hit: CompanyHit) -> None:
    """Record what Companies House itself states, each with the public register page as source."""
    if hit.locality:
        save_fact(
            session,
            company_id,
            "hq",
            Sourced[str](
                value=hit.locality,
                source_url=hit.url,
                confidence=Confidence.MEDIUM,
                note="registered office locality",
            ),
        )
    if hit.incorporated:
        save_fact(
            session,
            company_id,
            "founded",
            Sourced[int](
                value=hit.incorporated.year,
                source_url=hit.url,
                confidence=Confidence.MEDIUM,
                note="incorporation year, which can differ from the founding year",
            ),
        )


def ingest_companies(
    session: Session,
    client: CompaniesHouseClient,
    *,
    terms: Iterable[str] = DISCOVERY_TERMS,
    max_per_term: int = DEFAULT_MAX_PER_TERM,
    incorporated_from: date = MIN_INCORPORATION_DATE,
) -> dict[str, int]:
    stats = {"searched": 0, "new": 0, "updated": 0, "rejected": 0}
    seen: set[str] = set()
    for term in terms:
        hits = client.iter_advanced_search(
            term, incorporated_from=incorporated_from, limit=max_per_term
        )
        for hit in hits:
            stats["searched"] += 1
            if hit.number in seen:
                continue
            seen.add(hit.number)
            assessment = assess(
                hit.name, hit.sic_codes, status=hit.status, incorporated=hit.incorporated
            )
            if not assessment.is_candidate:
                stats["rejected"] += 1
                continue
            company, created = upsert_company(session, hit, assessment)
            assert company.id is not None
            _save_registry_facts(session, company.id, hit)
            stats["new" if created else "updated"] += 1
        session.commit()
    return stats


def ingest_news(
    session: Session, http: HttpClient, feeds: Sequence[Feed] = FEEDS
) -> dict[str, Any]:
    """Fetch every feed; one broken feed is reported but does not stop the others."""
    stats: dict[str, Any] = {"fetched": 0, "new": 0}
    failed: list[str] = []
    for feed in feeds:
        try:
            items = fetch_feed(http, feed)
        except FetchError as exc:
            failed.append(f"{feed.name}: {exc}")
            continue
        stats["fetched"] += len(items)
        stats["new"] += add_news_items(session, items)
        session.commit()
    stats["failed_feeds"] = failed
    return stats


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sprout.ingest", description=__doc__)
    parser.add_argument("--skip-companies", action="store_true")
    parser.add_argument("--skip-news", action="store_true")
    parser.add_argument(
        "--max-per-term",
        type=int,
        default=DEFAULT_MAX_PER_TERM,
        help="Companies House results to read per search term (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    engine = get_engine(settings.db_path)
    init_db(engine)
    exit_code = 0

    with Session(engine) as session:
        if not args.skip_news:
            try:
                feed_http = HttpClient(
                    cache=DiskCache(settings.cache_dir), limiter=PerHostRateLimiter(1, 1.0)
                )
                with feed_http as http:
                    with logged_run(session, "ingest_news") as run:
                        run.stats = ingest_news(session, http)
                        run.stats["http_requests"] = http.requests_made
                        run.stats["cache_hits"] = http.cache_hits
                    print(f"news: {run.stats}")
                    if run.stats["failed_feeds"]:
                        exit_code = 1
            except FetchError as exc:
                print(f"news ingestion failed: {exc}", file=sys.stderr)
                exit_code = 1

        if not args.skip_companies:
            try:
                key = settings.require_companies_house_key()
                with companies_house.make_http_client(key, settings.cache_dir) as http:
                    with logged_run(session, "ingest_companies") as run:
                        run.stats = ingest_companies(
                            session, CompaniesHouseClient(http), max_per_term=args.max_per_term
                        )
                        run.stats["http_requests"] = http.requests_made
                        run.stats["cache_hits"] = http.cache_hits
                    print(f"companies: {run.stats}")
            except (MissingConfigError, FetchError) as exc:
                print(f"company ingestion failed: {exc}", file=sys.stderr)
                exit_code = 1

        companies = session.exec(select(func.count()).select_from(Company)).one()
        news = session.exec(select(func.count()).select_from(NewsItemRow)).one()
        print(f"database {settings.db_path}: {companies} candidate companies, {news} news items")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
