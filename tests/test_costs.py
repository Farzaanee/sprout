from decimal import Decimal

import pytest

from sprout import costs
from sprout.costs import UnknownModelError, Usage, cost_gbp, cost_usd


def test_haiku_cost_is_exact_decimal_arithmetic():
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    # 0.10 + 0.50 + 0.125 + 0.01 USD per million of each
    assert cost_usd("claude-haiku-5-5", usage) == Decimal("0.735")
    assert cost_gbp("claude-haiku-5-5", usage) == Decimal("0.735") * costs.USD_TO_GBP


def test_sonnet_prices_and_web_searches():
    usage = Usage(input_tokens=500_000, output_tokens=100_000, web_searches=3)
    # 1.00 + 1.00 for tokens, 3 searches at 0.01
    assert cost_usd("claude-sonnet-5-5", usage) == Decimal("2.03")


def test_unknown_model_fails_closed():
    with pytest.raises(UnknownModelError, match="claude-nope"):
        cost_gbp("claude-nope", Usage())


def test_usage_adds_every_field():
    total = Usage(1, 2, 3, 4, 5) + Usage(10, 20, 30, 40, 50)
    assert total == Usage(11, 22, 33, 44, 55)


def test_zero_usage_costs_nothing():
    assert cost_gbp("claude-haiku-5-5", Usage()) == 0
