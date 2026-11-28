"""One-command ingestion: `uv run python -m sprout.ingest`.

Discovers candidate companies from Companies House into SQLite. Re-running is safe: companies
dedupe on company number and HTTP responses are served from the on-disk cache while fresh.
"""

import argparse
import sys
from collections.abc import Iterable, Sequence
from datetime import date

from sqlmodel import Session, col, func, select

from sprout.candidates import DISCOVERY_TERMS, MIN_INCORPORATION_DATE, assess
from sprout.config import MissingConfigError, get_settings
from sprout.db import (
    Company,
    RunKind,
    get_engine,
    init_db,
    logged_run,
    save_fact,
    save_officers,
    upsert_company,
)
from sprout.models import Confidence, Sourced
from sprout.sources import companies_house
from sprout.sources.companies_house import CompaniesHouseClient, CompanyHit
from sprout.sources.http import FetchError

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
    max_companies: int | None = None,
    incorporated_from: date = MIN_INCORPORATION_DATE,
) -> dict[str, int]:
    if max_companies is not None and max_companies < 1:
        raise ValueError("max_companies must be positive")

    stats = {"searched": 0, "new": 0, "updated": 0, "rejected": 0}
    seen: set[str] = set()
    candidates = 0
    for term in terms:
        hits = client.iter_advanced_search(
            term, incorporated_from=incorporated_from, limit=max_per_term
        )
        reached_limit = False
        for hit in hits:
            stats["searched"] += 1
            if hit.number in seen:
                continue
            seen.add(hit.number)
            assessment = assess(
                hit.name, hit.sic_codes, status=hit.status, incorporated=hit.incorporated
            )
            if assessment.is_candidate:
                candidates += 1
                officers_info = client.officers_info(hit.number)
                company, created = upsert_company(
                    session, hit, assessment, officers_info.active_count
                )
                assert company.id is not None
                _save_registry_facts(session, company.id, hit)
                active = [o for o in officers_info.officers if not o.resigned]
                save_officers(session, company.id, active, source_url=hit.url)
                stats["new" if created else "updated"] += 1
            else:
                stats["rejected"] += 1

            if max_companies is not None and candidates >= max_companies:
                reached_limit = True
                break
        session.commit()
        if reached_limit:
            break
    return stats


def backfill_active_officers(session: Session, client: CompaniesHouseClient) -> dict[str, int]:
    """Fetch `active_officer_count` for companies that don't have one yet (e.g. ingested before
    the count was stored)."""
    stats = {"checked": 0, "updated": 0}
    companies = session.exec(select(Company).where(col(Company.active_officers).is_(None))).all()
    for company in companies:
        stats["checked"] += 1
        company.active_officers = client.active_officer_count(company.company_number)
        stats["updated"] += 1
        session.commit()
    return stats


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sprout.ingest", description=__doc__)
    parser.add_argument(
        "--backfill-officers",
        action="store_true",
        help="only fetch active_officers for existing companies that don't have one yet",
    )
    parser.add_argument(
        "--max-per-term",
        type=int,
        default=DEFAULT_MAX_PER_TERM,
        help="Companies House results to read per search term (default: %(default)s)",
    )
    parser.add_argument(
        "--max-companies",
        type=int,
        help="maximum unique candidate companies to ingest across all terms",
    )
    args = parser.parse_args(argv)
    if args.max_companies is not None and args.max_companies < 1:
        parser.error("--max-companies must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    engine = get_engine(settings.db_path)
    init_db(engine)
    exit_code = 0

    with Session(engine) as session:
        if args.backfill_officers:
            try:
                key = settings.require_companies_house_key()
                with companies_house.make_http_client(key, settings.cache_dir) as http:
                    with logged_run(session, RunKind.COMPANIES_INGEST.value) as run:
                        run.stats = backfill_active_officers(session, CompaniesHouseClient(http))
                        run.stats["http_requests"] = http.requests_made
                        run.stats["cache_hits"] = http.cache_hits
                    print(f"officers backfill: {run.stats}")
            except (MissingConfigError, FetchError) as exc:
                print(f"officers backfill failed: {exc}", file=sys.stderr)
                exit_code = 1
            return exit_code

        try:
            key = settings.require_companies_house_key()
            with companies_house.make_http_client(key, settings.cache_dir) as http:
                with logged_run(session, RunKind.COMPANIES_INGEST.value) as run:
                    run.stats = ingest_companies(
                        session,
                        CompaniesHouseClient(http),
                        max_per_term=args.max_per_term,
                        max_companies=args.max_companies,
                    )
                    run.stats["http_requests"] = http.requests_made
                    run.stats["cache_hits"] = http.cache_hits
                print(f"companies: {run.stats}")
        except (MissingConfigError, FetchError) as exc:
            print(f"company ingestion failed: {exc}", file=sys.stderr)
            exit_code = 1

        companies = session.exec(select(func.count()).select_from(Company)).one()
        print(f"database {settings.db_path}: {companies} candidate companies")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
