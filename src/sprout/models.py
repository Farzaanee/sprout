"""The vocabulary every later phase depends on: taxonomy, stage enum and profile schemas.

Changing `Category` after classifications or the golden set exist invalidates them,
so keep it small and stable.
"""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AnyHttpUrl, AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Category(StrEnum):
    AI_MODELS_AND_APPLICATIONS = "AI models and applications"
    FINTECH_AND_PAYMENTS = "Fintech and payments"
    ENTERPRISE_SOFTWARE_AND_CLOUD = "Enterprise software and cloud"
    CYBERSECURITY = "Cybersecurity"
    BIOTECH_AND_PHARMA = "Biotech and pharma"
    HEALTHTECH_AND_DIGITAL_HEALTH = "Healthtech and digital health"
    ELECTRIC_VEHICLES_AND_BATTERIES = "Electric vehicles and batteries"
    CLIMATE_ENERGY_AND_CLEANTECH = "Climate, energy and cleantech"
    SEMICONDUCTORS_AND_AI_HARDWARE = "Semiconductors and AI hardware"
    ROBOTICS_AND_AUTOMATION = "Robotics and automation"
    DEFENSE_AEROSPACE_AND_SPACE = "Defense, aerospace and space"
    ECOMMERCE_AND_RETAIL_TECH = "E-commerce and retail tech"
    CONSUMER_SOCIAL_MEDIA_AND_GAMING = "Consumer social, media and gaming"
    LOGISTICS_AND_SUPPLY_CHAIN = "Logistics and supply chain"
    DEEP_TECH_AND_QUANTUM = "Deep tech and quantum"
    OTHER = "Other"


class Stage(StrEnum):
    PRE_SEED = "Pre-seed"
    SEED = "Seed"
    SERIES_A = "Series A"
    SERIES_B = "Series B"
    SERIES_C_PLUS = "Series C+"
    GROWTH = "Growth"
    UNKNOWN = "Unknown"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class _Strict(BaseModel):
    """Reject unknown fields so malformed model output fails validation."""

    model_config = ConfigDict(extra="forbid")


class Sourced[T](_Strict):
    """A fact with provenance. `value=None` means "not disclosed" and is always valid.

    A non-null value must carry a source URL: this is enforced here, not just in prompts.
    """

    value: T | None = None
    source_url: AnyHttpUrl | None = None
    retrieved_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    confidence: Confidence = Confidence.LOW
    note: str | None = None  # e.g. original currency: "US$12m converted at 0.79"

    @model_validator(mode="after")
    def _value_needs_source(self) -> Self:
        if self.value is not None and self.source_url is None:
            raise ValueError("a non-null value requires a source_url")
        return self


class NewsItem(_Strict):
    title: str = Field(min_length=1)
    url: AnyHttpUrl
    published: date | None = None
    summary: str = ""
    source: str | None = None


class FundingRound(_Strict):
    stage: Stage = Stage.UNKNOWN
    amount_gbp: Annotated[int, Field(ge=0)] | None = None
    announced: date | None = None
    investors: list[str] = Field(default_factory=list)


class CompanyProfile(_Strict):
    name: str = Field(min_length=1)
    website: Sourced[str] = Field(default_factory=Sourced[str])
    hq: Sourced[str] = Field(default_factory=Sourced[str])
    founded: Sourced[Annotated[int, Field(ge=1800, le=2100)]] = Field(
        default_factory=Sourced[Annotated[int, Field(ge=1800, le=2100)]]
    )
    # A classification (model judgement against the fixed taxonomy), not a sourced fact.
    category: Category = Category.OTHER
    stage: Sourced[Stage] = Field(default_factory=Sourced[Stage])
    total_raised_gbp: Sourced[Annotated[int, Field(ge=0)]] = Field(
        default_factory=Sourced[Annotated[int, Field(ge=0)]]
    )
    last_round: Sourced[FundingRound] = Field(default_factory=Sourced[FundingRound])
    business_model: Sourced[str] = Field(default_factory=Sourced[str])
    target_user: Sourced[str] = Field(default_factory=Sourced[str])
    one_liner: Sourced[str] = Field(default_factory=Sourced[str])
    news: list[NewsItem] = Field(default_factory=list)
