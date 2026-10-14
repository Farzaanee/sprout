"""Companies House REST client (search, company profile, filing history).

Auth is HTTP basic with the API key as username and an empty password. The API allows about
600 requests per 5 minutes; the default limiter stays safely under that.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from sprout.sources.http import USER_AGENT, DiskCache, FetchError, HttpClient, RateLimiter

BASE_URL = "https://api.company-information.service.gov.uk"
PUBLIC_COMPANY_URL = "https://find-and-update.company-information.service.gov.uk/company/{number}"
RATE_LIMIT_CALLS = 550
RATE_LIMIT_PERIOD_SECONDS = 300.0
DEFAULT_CACHE_TTL = timedelta(days=7)
_COMPANY_NUMBER = re.compile(r"^[A-Z0-9]{8}$")


@dataclass(frozen=True)
class CompanyHit:
    """The few fields we keep from a search result."""

    number: str
    name: str
    status: str | None
    incorporated: date | None
    sic_codes: tuple[str, ...]
    locality: str | None

    @property
    def url(self) -> str:
        return PUBLIC_COMPANY_URL.format(number=self.number)


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def parse_hit(item: dict[str, Any]) -> CompanyHit | None:
    """Normalise an item from `/advanced-search` or `/search`; None if it lacks a number/name."""
    number = item.get("company_number")
    name = item.get("company_name") or item.get("title")
    if not number or not name:
        return None
    address = item.get("registered_office_address") or item.get("address") or {}
    return CompanyHit(
        number=str(number).upper(),
        name=str(name),
        status=item.get("company_status"),
        incorporated=_parse_date(item.get("date_of_creation")),
        sic_codes=tuple(str(c) for c in item.get("sic_codes") or ()),
        locality=address.get("locality"),
    )


def make_http_client(api_key: str, cache_dir: Path | None = None) -> HttpClient:
    client = httpx.Client(
        auth=httpx.BasicAuth(api_key, ""),
        headers={"User-Agent": USER_AGENT},
        timeout=20.0,
        follow_redirects=True,
    )
    return HttpClient(
        client=client,
        cache=DiskCache(cache_dir) if cache_dir else None,
        limiter=RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD_SECONDS),
    )


class CompaniesHouseClient:
    def __init__(
        self,
        http: HttpClient,
        *,
        base_url: str = BASE_URL,
        cache_ttl: timedelta | None = DEFAULT_CACHE_TTL,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._cache_ttl = cache_ttl

    def search(
        self, query: str, *, items_per_page: int = 100, start_index: int = 0
    ) -> list[CompanyHit]:
        """Free-text company search (names only; results carry no SIC codes)."""
        data = self._get_json(
            "/search/companies",
            {"q": query, "items_per_page": items_per_page, "start_index": start_index},
        )
        return self._hits(data)

    def advanced_search(
        self,
        name_includes: str,
        *,
        incorporated_from: date,
        status: str = "active",
        size: int = 100,
        start_index: int = 0,
    ) -> list[CompanyHit]:
        data = self._get_json(
            "/advanced-search/companies",
            {
                "company_name_includes": name_includes,
                "company_status": status,
                "incorporated_from": incorporated_from.isoformat(),
                "size": size,
                "start_index": start_index,
            },
        )
        return self._hits(data)

    def iter_advanced_search(
        self, name_includes: str, *, incorporated_from: date, limit: int, page_size: int = 100
    ) -> Iterator[CompanyHit]:
        """Page through advanced-search results, yielding at most `limit` hits."""
        fetched = 0
        while fetched < limit:
            size = min(page_size, limit - fetched)
            page = self.advanced_search(
                name_includes,
                incorporated_from=incorporated_from,
                size=size,
                start_index=fetched,
            )
            yield from page
            fetched += size
            if len(page) < size:
                return

    def get_company(self, number: str) -> dict[str, Any] | None:
        return self._get_json(f"/company/{self._valid_number(number)}")

    def filing_history(self, number: str, *, items_per_page: int = 25) -> dict[str, Any] | None:
        return self._get_json(
            f"/company/{self._valid_number(number)}/filing-history",
            {"items_per_page": items_per_page},
        )

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Parsed JSON body, or None when the API answers 404."""
        response = self._http.get(self._base_url + path, params=params, max_age=self._cache_ttl)
        if response.status_code == 404:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(f"Companies House returned invalid JSON for {path}") from exc

    @staticmethod
    def _hits(data: dict[str, Any] | None) -> list[CompanyHit]:
        hits = (parse_hit(item) for item in (data or {}).get("items") or ())
        return [hit for hit in hits if hit is not None]

    @staticmethod
    def _valid_number(number: str) -> str:
        number = number.strip().upper()
        if not _COMPANY_NUMBER.match(number):
            raise ValueError(f"not a valid Companies House company number: {number!r}")
        return number
