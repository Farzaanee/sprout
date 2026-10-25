from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sprout.models import (
    Category,
    CompanyProfile,
    Confidence,
    FundingRound,
    NewsItem,
    Sourced,
    Stage,
)

SRC = "https://example.com/funding-announcement"


def test_taxonomy_matches_fixed_categories():
    assert len(Category) == 16
    assert [category.value for category in Category] == [
        "AI models and applications",
        "Fintech and payments",
        "Enterprise software and cloud",
        "Cybersecurity",
        "Biotech and pharma",
        "Healthtech and digital health",
        "Electric vehicles and batteries",
        "Climate, energy and cleantech",
        "Semiconductors and AI hardware",
        "Robotics and automation",
        "Defense, aerospace and space",
        "E-commerce and retail tech",
        "Consumer social, media and gaming",
        "Logistics and supply chain",
        "Deep tech and quantum",
        "Other",
    ]
    assert Category.OTHER in Category


def test_category_and_stage_reject_unknown_labels():
    assert Category("Healthtech and digital health") is Category.HEALTHTECH_AND_DIGITAL_HEALTH
    assert Stage("Series D") is Stage.SERIES_D
    with pytest.raises(ValueError):
        Category("Healthcare")
    with pytest.raises(ValueError):
        Stage("Series Z")


def test_sourced_not_disclosed_is_valid_without_source():
    s = Sourced[int]()
    assert s.value is None and s.source_url is None


def test_sourced_value_with_source_is_valid():
    s = Sourced[int](value=5_000_000, source_url=SRC, confidence=Confidence.HIGH)
    assert s.value == 5_000_000
    assert s.retrieved_at.tzinfo is not None


def test_sourced_value_without_source_is_rejected():
    with pytest.raises(ValidationError, match="source_url"):
        Sourced[int](value=5_000_000)


@pytest.mark.parametrize("url", ["not a url", "file:///etc/passwd", "ftp://example.com/x"])
def test_sourced_rejects_non_http_urls(url):
    with pytest.raises(ValidationError):
        Sourced[int](value=1, source_url=url)


def test_sourced_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        Sourced[int](value=1, source_url=SRC, retrieved_at=datetime(2026, 1, 1))


def test_sourced_rejects_unknown_confidence_and_fields():
    with pytest.raises(ValidationError):
        Sourced[int](value=1, source_url=SRC, confidence="certain")
    with pytest.raises(ValidationError):
        Sourced[int](value=1, source_url=SRC, guess=True)


def test_empty_profile_defaults_to_not_disclosed():
    p = CompanyProfile(name="Acme AI")
    assert p.category is Category.OTHER
    assert p.stage.value is None
    assert p.total_raised_million_usd.value is None
    assert p.valuation_million_usd.value is None
    assert p.news == []


def test_default_sourced_fields_are_not_shared_between_profiles():
    a, b = CompanyProfile(name="A"), CompanyProfile(name="B")
    assert a.total_raised_million_usd is not b.total_raised_million_usd


def test_full_profile_round_trips_through_json():
    now = datetime.now(UTC)
    p = CompanyProfile(
        name="Acme AI",
        category=Category.HEALTHTECH_AND_DIGITAL_HEALTH,
        stage=Sourced[Stage](value=Stage.SEED, source_url=SRC, confidence=Confidence.MEDIUM),
        total_raised_million_usd=Sourced[int](
            value=4, source_url=SRC, retrieved_at=now, note="GBP 3.0m converted"
        ),
        valuation_million_usd=Sourced[int](
            value=20, source_url=SRC, retrieved_at=now, note="Valued at US$20m"
        ),
        funding_rounds=[
            Sourced[FundingRound](
                value=FundingRound(stage=Stage.SEED, amount_million_usd=4, investors=["Seedcamp"]),
                source_url=SRC,
            )
        ],
        news=[NewsItem(title="Acme raises seed", url=SRC)],
    )
    assert CompanyProfile.model_validate_json(p.model_dump_json()) == p


@pytest.mark.parametrize(
    "bad",
    [
        {"name": ""},
        {"name": "X", "category": "Fintech"},
        {"name": "X", "unexpected": 1},
        {"name": "X", "total_raised_million_usd": {"value": -5, "source_url": SRC}},
        {"name": "X", "founded": {"value": 1066, "source_url": SRC}},
        {"name": "X", "stage": {"value": "Series Z", "source_url": SRC}},
        {"name": "X", "total_raised_million_usd": {"value": 1}},  # fabricated: no source
        {"name": "X", "funding_rounds": [{"value": {"stage": "Seed"}}]},  # round with no source
    ],
)
def test_invalid_profiles_are_rejected(bad):
    with pytest.raises(ValidationError):
        CompanyProfile.model_validate(bad)


def test_news_item_requires_title_and_http_url():
    with pytest.raises(ValidationError):
        NewsItem(title="", url=SRC)
    with pytest.raises(ValidationError):
        NewsItem(title="x", url="javascript:alert(1)")
