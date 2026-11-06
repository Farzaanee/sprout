from decimal import Decimal

import pytest

from sprout.costs import BatchBudget, UnknownModelError, Usage, cost_usd, enrichment_batch_size


def test_haiku_cost_is_exact_decimal_arithmetic():
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    # 0.10 + 0.50 + 0.125 + 0.01 USD per million of each
    assert cost_usd("claude-haiku-5-5", usage) == Decimal("0.735")


def test_sonnet_prices_and_web_searches():
    usage = Usage(input_tokens=500_000, output_tokens=100_000, web_searches=3)
    # 1.00 + 1.00 for tokens, 3 searches at 0.01
    assert cost_usd("claude-sonnet-5-5", usage) == Decimal("2.03")


def test_unknown_model_fails_closed():
    with pytest.raises(UnknownModelError, match="claude-nope"):
        cost_usd("claude-nope", Usage())


def test_usage_adds_every_field():
    total = Usage(1, 2, 3, 4, 5) + Usage(10, 20, 30, 40, 50)
    assert total == Usage(11, 22, 33, 44, 55)


def test_zero_usage_costs_nothing():
    assert cost_usd("claude-haiku-5-5", Usage()) == 0


def test_enrichment_batch_size_is_capped_by_budget():
    n = enrichment_batch_size(
        101, budget_usd=Decimal("0.75"), cost_per_company_usd=Decimal("0.015")
    )
    assert n == 50


def test_enrichment_batch_size_is_capped_by_survivors():
    n = enrichment_batch_size(3, budget_usd=Decimal("0.75"), cost_per_company_usd=Decimal("0.015"))
    assert n == 3


@pytest.mark.parametrize("bad", [Decimal(0), Decimal("-1")])
def test_batch_budget_rejects_a_non_positive_cap(bad):
    with pytest.raises(ValueError, match="max_usd"):
        BatchBudget(bad)


def test_batch_budget_charges_and_reports_remaining():
    batch = BatchBudget(Decimal("1.00"))
    batch.charge(Decimal("0.30"))
    batch.charge(Decimal("0.20"))
    assert batch.spent == Decimal("0.50") and batch.remaining == Decimal("0.50")
    assert not batch.exhausted


def test_batch_budget_is_exhausted_at_the_cap_and_remaining_never_negative():
    batch = BatchBudget(Decimal("1.00"))
    batch.charge(Decimal("1.00"))
    assert batch.exhausted and batch.remaining == Decimal(0)
    batch.charge(Decimal("0.25"))  # soft cap: the last response may overshoot
    assert batch.exhausted and batch.remaining == Decimal(0)


def test_batch_budget_clamp_is_the_smaller_of_item_cap_and_remaining():
    batch = BatchBudget(Decimal("1.00"))
    assert batch.clamp(Decimal("0.13")) == Decimal("0.13")
    batch.charge(Decimal("0.95"))
    assert batch.clamp(Decimal("0.13")) == Decimal("0.05")
