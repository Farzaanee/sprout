"""Scoring of enrichment runs against the golden set.

`read_observed` is the only function that touches the database; everything else is a pure
function over `Observed` and `GoldenEntry`. Page text for the number-match check is injected as a
callable so the module needs no network.
"""

import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlmodel import Session, col, select

from evals.golden import GoldenEntry
from sprout.agents.tools import SourceLedger, fetch_page_view
from sprout.db import Company, Fact, FundingRoundRow, RunKind, RunLog, winning_facts
from sprout.models import Stage
from sprout.sources.http import FetchError
from sprout.sources.web import WebFetcher

PageText = Callable[[str], str | None]  # url -> the text the model could see, None if unavailable

# Fact field names as persisted by `save_profile`.
TOTAL_RAISED = "total_raised_million_usd"
VALUATION = "valuation_million_usd"
FUNDING_FACTS = (TOTAL_RAISED, VALUATION)

WITHIN_FRACTION = Decimal("0.10")  # "within 10 percent" funding match
NUMBER_MATCH_TOLERANCE = Decimal("0.01")  # D21: stored amount vs page amount
THIN_FACT_LIMIT = 3  # an `Other` profile with this many sourced facts or fewer is "thin"


@dataclass(frozen=True)
class Ratio:
    """A count beside its denominator; `percent` is None when nothing was measurable."""

    num: int
    den: int

    @property
    def percent(self) -> float | None:
        return None if self.den == 0 else 100 * self.num / self.den

    def __str__(self) -> str:
        pct = self.percent
        return f"{self.num}/{self.den} ({'n/a' if pct is None else f'{pct:.1f}%'})"


# ---- number parsing (D21) -------------------------------------------------------------------

# Optional currency marker, a number with optional commas and decimals, an optional unit. A match
# needs a unit or a marker ("5m", "$5m" and "$5,000,000" count; a bare "5" or "5 months" do not).
_AMOUNT = re.compile(
    r"(?P<marker>US\s?\$|USD|\$|GBP|£|EUR|€)?\s?"
    r"(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<unit>billion|bn|b|million|mn|m)?(?![A-Za-z])",
    re.IGNORECASE,
)
_UNIT_IN_MILLIONS = {"m": 1, "mn": 1, "million": 1, "b": 1000, "bn": 1000, "billion": 1000}


def parse_amounts_million(text: str) -> list[Decimal]:
    """Money amounts found in `text`, normalised to millions (currency is not converted)."""
    amounts: list[Decimal] = []
    for match in _AMOUNT.finditer(text):
        unit, marker = match["unit"], match["marker"]
        if unit is None and marker is None:
            continue
        value = Decimal(match["number"].replace(",", ""))
        if unit is None:
            amounts.append(value / 1_000_000)  # a full figure such as $12,500,000
        else:
            amounts.append(value * _UNIT_IN_MILLIONS[unit.lower()])
    return amounts


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= NUMBER_MATCH_TOLERANCE * max(abs(a), abs(b))


def amount_supported(stored_million: int, note: str | None, page_text: str) -> bool:
    """D21: the stored amount, or an amount quoted in its note, appears on the page (within 1%)."""
    claimed = [Decimal(stored_million), *parse_amounts_million(note or "")]
    on_page = parse_amounts_million(page_text)
    return any(_close(c, p) for c in claimed for p in on_page)


# ---- observed state -------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservedFact:
    field: str
    value: Any  # JSON-decoded; None means researched but not disclosed
    source_url: str | None
    note: str | None


@dataclass(frozen=True)
class ObservedRound:
    stage: str
    amount_million_usd: int | None
    announced: date | None
    source_url: str | None
    note: str | None


@dataclass(frozen=True)
class Observed:
    """What one enrichment run left in the database. All defaults mean "nothing persisted"."""

    has_run: bool = False
    stop: str | None = None
    failure_category: str | None = None
    iterations: int | None = None
    repairs: int | None = None
    cost_usd: Decimal | None = None
    # None: the run predates `stats["sources"]`, so ledger and fabrication checks cannot run.
    sources: tuple[tuple[str, bool], ...] | None = None
    category: str | None = None
    facts: dict[str, ObservedFact] = field(default_factory=dict)  # winning fact per field
    all_facts: tuple[ObservedFact, ...] = ()
    rounds: tuple[ObservedRound, ...] = ()

    def value(self, name: str) -> Any:
        fact = self.facts.get(name)
        return None if fact is None else fact.value


def _observed_fact(row: Fact) -> ObservedFact:
    value = None if row.value_json is None else json.loads(row.value_json)
    return ObservedFact(row.field, value, row.source_url, row.note)


def _sources_of(stats: dict[str, Any]) -> tuple[tuple[str, bool], ...] | None:
    raw = stats.get("sources")
    if not isinstance(raw, list):
        return None
    return tuple((str(item["url"]), bool(item.get("fetched"))) for item in raw)


def read_observed(session: Session, entry: GoldenEntry) -> Observed:
    """Persisted state for `entry`'s company: category, facts, rounds and its latest enrich run."""
    company = session.exec(
        select(Company).where(Company.company_number == entry.company_number)
    ).first()
    if company is None or company.id is None:
        return Observed()
    rows = session.exec(select(Fact).where(Fact.company_id == company.id)).all()
    winners = winning_facts(rows)
    rounds = session.exec(
        select(FundingRoundRow).where(FundingRoundRow.company_id == company.id)
    ).all()
    common: dict[str, Any] = {
        "category": company.category,
        "facts": {name: _observed_fact(row) for name, row in winners.items()},
        "all_facts": tuple(_observed_fact(row) for row in rows),
        "rounds": tuple(
            ObservedRound(r.stage, r.amount_million_usd, r.announced, r.source_url, r.note)
            for r in rounds
        ),
    }
    run = session.exec(
        select(RunLog)
        .where(RunLog.kind == RunKind.COMPANY_ENRICH.value, RunLog.company_id == company.id)
        .order_by(col(RunLog.id).desc())
    ).first()
    if run is None:
        return Observed(**common)
    stats = run.stats or {}
    cost = stats.get("cost_usd")
    return Observed(
        has_run=True,
        stop=stats.get("stop"),
        failure_category=run.failure_category,
        iterations=run.iterations,
        repairs=run.repairs,
        cost_usd=None if cost is None else Decimal(str(cost)),
        sources=_sources_of(stats),
        **common,
    )


# ---- last round (D22) -----------------------------------------------------------------------

_STAGE_RANK = {stage.value: rank for rank, stage in enumerate(Stage)}
_STAGE_RANK[Stage.UNKNOWN.value] = -1


def last_round(rounds: Iterable[ObservedRound]) -> ObservedRound | None:
    """The latest announced round; undated or tied rounds fall back to the highest stage.

    A round with a null amount still wins: the agent said the latest amount is unknown, which is
    a miss, not a reason to look at an older round.
    """
    return max(
        rounds,
        key=lambda r: (r.announced or date.min, _STAGE_RANK.get(r.stage, -1)),
        default=None,
    )


# ---- per-entry scoring ----------------------------------------------------------------------


def category_correct(observed: Observed, entry: GoldenEntry) -> bool:
    return observed.category in entry.categories


def business_status_correct(observed: Observed, entry: GoldenEntry) -> bool:
    return observed.value("business_status") == entry.status.value


def stage_correct(observed: Observed, entry: GoldenEntry) -> bool:
    got = observed.value("stage")
    if entry.stage == Stage.UNKNOWN:
        return got in (None, Stage.UNKNOWN.value)
    return got == entry.stage.value


def funding_match(observed: Observed, entry: GoldenEntry) -> tuple[bool, bool] | None:
    """(exact, within 10 percent) of the observed last round against the golden one.

    None when the golden entry has no round. A null observed amount is a miss, unless the golden
    amount is null too ("not public"), which then matches exactly.
    """
    if entry.last_round is None:
        return None
    gold = entry.last_round.amount_million_usd
    latest = last_round(observed.rounds)
    got = None if latest is None else latest.amount_million_usd
    if latest is None:
        return (False, False)
    exact = got == gold
    if got is None or gold is None:
        return (exact, exact)
    return (exact, abs(got - gold) <= WITHIN_FRACTION * gold)


def unexpected_disclosure(observed: Observed, entry: GoldenEntry) -> bool | None:
    """None unless the golden entry has no round; True if the agent reported any round amount."""
    if entry.last_round is not None:
        return None
    return any(r.amount_million_usd is not None for r in observed.rounds)


def is_thin_other(observed: Observed) -> bool:
    sourced = sum(1 for f in observed.facts.values() if f.value is not None and f.source_url)
    return observed.category == "Other" and sourced <= THIN_FACT_LIMIT


@dataclass(frozen=True)
class FundingClaim:
    label: str  # "total_raised_million_usd", "valuation_million_usd" or "round:<stage>"
    amount_million_usd: int
    source_url: str | None
    note: str | None


def funding_claims(observed: Observed) -> list[FundingClaim]:
    """Every non-null funding value the run stored: totals, valuation and round amounts."""
    claims = [
        FundingClaim(f.field, f.value, f.source_url, f.note)
        for f in observed.all_facts
        if f.field in FUNDING_FACTS and f.value is not None
    ]
    claims += [
        FundingClaim(f"round:{r.stage}", r.amount_million_usd, r.source_url, r.note)
        for r in observed.rounds
        if r.amount_million_usd is not None
    ]
    return claims


def rebuild_ledger(sources: Iterable[tuple[str, bool]]) -> SourceLedger:
    """A `SourceLedger` from `stats["sources"]`, so URL normalisation is the production rule."""
    ledger = SourceLedger()
    for url, fetched in sources:
        ledger.add(url, fetched=fetched)
    return ledger


def check_claim(claim: FundingClaim, ledger: SourceLedger | None, page_text: PageText) -> str:
    """ "supported", "fabricated" or "unverifiable" (the page was not read, or its text is gone)."""
    if ledger is None or not claim.source_url:
        return "unverifiable"
    seen = ledger.get(claim.source_url)
    if seen is None or not seen.fetched:
        return "unverifiable"
    text = page_text(claim.source_url)
    if text is None:
        return "unverifiable"
    return (
        "supported"
        if amount_supported(claim.amount_million_usd, claim.note, text)
        else "fabricated"
    )


def cited_urls(observed: Observed) -> list[str]:
    return [f.source_url for f in observed.all_facts if f.source_url] + [
        r.source_url for r in observed.rounds if r.source_url
    ]


@dataclass(frozen=True)
class EntryScore:
    """Every per-entry outcome; None means the metric does not apply to this entry."""

    company_number: str
    name: str
    has_run: bool
    submitted: bool
    api_error: bool
    repaired: bool
    category_ok: bool | None = None
    business_status_ok: bool | None = None
    stage_ok: bool | None = None
    funding_exact: bool | None = None
    funding_within: bool | None = None
    unexpected_disclosure: bool | None = None
    thin_other: bool | None = None
    claims: tuple[tuple[FundingClaim, str], ...] = ()  # claim and its check result
    ledger_hits: int = 0
    ledger_cited: int = 0
    ledger_unverifiable: int = 0  # cited URLs of a run with no recorded sources
    cost_usd: Decimal | None = None


def score_entry(observed: Observed, entry: GoldenEntry, page_text: PageText) -> EntryScore:
    submitted = observed.stop == "submitted"
    ledger = None if observed.sources is None else rebuild_ledger(observed.sources)
    urls = cited_urls(observed)
    funding = funding_match(observed, entry) if submitted else None
    cache: dict[str, str | None] = {}

    def cached_text(url: str) -> str | None:
        if url not in cache:
            cache[url] = page_text(url)
        return cache[url]

    return EntryScore(
        company_number=entry.company_number,
        name=entry.name,
        has_run=observed.has_run,
        submitted=submitted,
        api_error=observed.failure_category == "api_error",
        repaired=(observed.repairs or 0) > 0,
        category_ok=category_correct(observed, entry) if submitted else None,
        business_status_ok=business_status_correct(observed, entry) if submitted else None,
        stage_ok=stage_correct(observed, entry) if submitted else None,
        funding_exact=None if funding is None else funding[0],
        funding_within=None if funding is None else funding[1],
        unexpected_disclosure=unexpected_disclosure(observed, entry) if submitted else None,
        thin_other=is_thin_other(observed) if submitted else None,
        claims=tuple((c, check_claim(c, ledger, cached_text)) for c in funding_claims(observed)),
        ledger_hits=0 if ledger is None else sum(ledger.get(u) is not None for u in urls),
        ledger_cited=0 if ledger is None else len(urls),
        ledger_unverifiable=len(urls) if ledger is None else 0,
        cost_usd=observed.cost_usd,
    )


# ---- aggregate ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scores:
    """Counts beside percentages. `ratios` is ordered for the report table; `counts()` is the
    machine-readable form the runner records and diffs against the next run."""

    scored: int
    ratios: dict[str, Ratio]
    ledger_unverifiable: int
    cost_mean: Decimal | None
    cost_max: Decimal | None
    entries: tuple[EntryScore, ...] = ()

    def counts(self) -> dict[str, Any]:
        out: dict[str, Any] = {"scored": self.scored}
        out.update({name: [r.num, r.den] for name, r in self.ratios.items()})
        out["ledger_unverifiable"] = self.ledger_unverifiable
        out["cost_mean_usd"] = None if self.cost_mean is None else str(self.cost_mean)
        out["cost_max_usd"] = None if self.cost_max is None else str(self.cost_max)
        return out


def _ratio(flags: Iterable[bool | None]) -> Ratio:
    """Counts True over every flag that is not None (the entries the metric applies to)."""
    applicable = [f for f in flags if f is not None]
    return Ratio(sum(applicable), len(applicable))


def aggregate(scores: Sequence[EntryScore]) -> Scores:
    total = len(scores)
    not_api = [s for s in scores if not s.api_error]
    submitted = [s for s in scores if s.submitted]
    claim_results = [result for s in scores for _, result in s.claims]
    costs = [s.cost_usd for s in scores if s.cost_usd is not None]
    ratios = {
        "profiles_submitted": Ratio(sum(s.submitted for s in not_api), len(not_api)),
        "infrastructure_errors": Ratio(sum(s.api_error for s in scores), total),
        "repair_rate": Ratio(sum(s.repaired for s in scores), total),
        "category_accuracy": _ratio(s.category_ok for s in scores),
        "business_status_accuracy": _ratio(s.business_status_ok for s in scores),
        "stage_accuracy": _ratio(s.stage_ok for s in scores),
        "funding_exact": _ratio(s.funding_exact for s in scores),
        "funding_within_10pct": _ratio(s.funding_within for s in scores),
        "unexpected_disclosure": _ratio(s.unexpected_disclosure for s in scores),
        "fabricated_funding": Ratio(claim_results.count("fabricated"), len(claim_results)),
        "unverifiable_funding": Ratio(claim_results.count("unverifiable"), len(claim_results)),
        "source_ledger": Ratio(
            sum(s.ledger_hits for s in scores), sum(s.ledger_cited for s in scores)
        ),
        "thin_other": Ratio(sum(bool(s.thin_other) for s in submitted), len(submitted)),
    }
    return Scores(
        scored=total,
        ratios=ratios,
        ledger_unverifiable=sum(s.ledger_unverifiable for s in scores),
        cost_mean=sum(costs, Decimal(0)) / len(costs) if costs else None,
        cost_max=max(costs, default=None),
        entries=tuple(scores),
    )


def score_all(pairs: Iterable[tuple[GoldenEntry, Observed]], page_text: PageText) -> Scores:
    return aggregate([score_entry(observed, entry, page_text) for entry, observed in pairs])


# ---- page text from the real fetcher --------------------------------------------------------


def fetched_page_text(fetcher: WebFetcher) -> PageText:
    """Page text as the model saw it (title line plus text, capped), read through the fetcher's
    disk cache. A fetch failure yields None, so the claim is reported unverifiable."""

    def page_text(url: str) -> str | None:
        try:
            return fetch_page_view(fetcher.fetch(url))
        except FetchError:
            return None

    return page_text
