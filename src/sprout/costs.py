"""Token and search cost accounting. Pure: no I/O and no imports from the rest of `sprout`.

Prices are code (reviewed, tested, reproducible from a commit), not env. An unpriced model fails
closed so a typo in `SPROUT_BULK_MODEL` cannot silently bypass the spend caps.
"""

from dataclasses import dataclass
from decimal import Decimal

# USD to GBP: ASSUMPTION, dated 2026-10-19. Taken from approximate web readings of GBP/USD at
# about 1.34 to 1.35 in September 2026 (no authoritative October source was reachable), then
# rounded up to 0.76 so caps are not under-counted. A stale rate only skews the GBP figure by a
# few percent; there is deliberately no live FX lookup in the cost path.
USD_TO_GBP = Decimal("0.76")

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


def cost_gbp(model: str, usage: Usage) -> Decimal:
    return cost_usd(model, usage) * USD_TO_GBP
