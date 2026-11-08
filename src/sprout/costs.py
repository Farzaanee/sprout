"""Token and search cost accounting. Pure: no I/O and no imports from the rest of `sprout`.

Prices are code (reviewed, tested, reproducible from a commit), not env. An unpriced model fails
closed so a typo in `SPROUT_BULK_MODEL` cannot silently bypass the spend caps.
"""

import threading
from dataclasses import dataclass, field
from decimal import Decimal

_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: Decimal
    output: Decimal
    cache_write: Decimal  # 5-minute cache writes, 1.25x input
    cache_read: Decimal  # 0.1x input


# Verified against the `claude-api` skill model table (cached 2026-10-06) and its Haiku 5.5
# migration notes (cache reads 0.1x, 5-minute writes 1.25x). Haiku 5.5 doubles to $0.50/$2.50 for
# prompts over 100K tokens; our per-call prompts stay far below that (tool results are capped), so
# the lower rate card is used.
PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(
        input=Decimal("0.10"),
        output=Decimal("0.50"),
        cache_write=Decimal("0.125"),
        cache_read=Decimal("0.01"),
    ),
    "claude-sonnet-5-5": Price(
        input=Decimal("2.00"),
        output=Decimal("10.00"),
        cache_write=Decimal("2.50"),
        cache_read=Decimal("0.20"),
    ),
}

# USD 10 per 1,000 searches. Source: the `claude-api` skill (managed-agents list-cost note says
# "web searches at $10 per 1,000"). Not independently confirmed against an invoice for the plain
# Messages API; check the first console invoice.
WEB_SEARCH_USD_EACH = Decimal("0.01")

# D34: observed cost of enriching one company (see docs/architecture/2. data-model.md), and the
# smoke-test-scale budget for a single enrichment run until a real weekly batch figure is set.
COST_PER_COMPANY_USD = Decimal("0.015")
BUDGET_PER_RUN_USD = Decimal("0.75")


class UnknownModelError(KeyError):
    """No price is configured for this model ID."""


@dataclass
class Usage:
    """Token and search counts, summed over the calls of one run."""

    input_tokens: int = 0  # fresh (uncached) input only
    output_tokens: int = 0  # includes thinking tokens
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_searches: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.web_searches + other.web_searches,
        )


def cost_usd(model: str, usage: Usage) -> Decimal:
    try:
        price = PRICES[model]
    except KeyError:
        raise UnknownModelError(
            f"no price configured for model {model!r}; add it to costs.PRICES"
        ) from None
    tokens = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_write_tokens * price.cache_write
        + usage.cache_read_tokens * price.cache_read
    ) / _MILLION
    return tokens + usage.web_searches * WEB_SEARCH_USD_EACH


@dataclass
class BatchBudget:
    """Shared USD cap over many runs. Soft by at most one response per run, like the per-run cap.

    Thread-safe: the Phase 6 workers charge it concurrently.
    """

    max_usd: Decimal
    spent: Decimal = Decimal(0)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.max_usd <= 0:
            raise ValueError("max_usd must be positive")

    def charge(self, cost: Decimal) -> None:
        with self._lock:
            self.spent += cost

    @property
    def remaining(self) -> Decimal:
        return max(self.max_usd - self.spent, Decimal(0))

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.max_usd

    def clamp(self, per_item_usd: Decimal) -> Decimal:
        with self._lock:
            return min(per_item_usd, self.remaining)

    def try_slice(self, cap: Decimal) -> Decimal | None:
        """Atomically take `min(cap, remaining)` for one call; None when nothing remains."""
        with self._lock:
            remaining = max(self.max_usd - self.spent, Decimal(0))
            return min(cap, remaining) if remaining > 0 else None


def enrichment_batch_size(
    survivors_after_prescreen: int,
    *,
    budget_usd: Decimal = BUDGET_PER_RUN_USD,
    cost_per_company_usd: Decimal = COST_PER_COMPANY_USD,
) -> int:
    """N (D34): how many pre-screened candidates one run can afford to enrich."""
    return min(survivors_after_prescreen, int(budget_usd / cost_per_company_usd))
