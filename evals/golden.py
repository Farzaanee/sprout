"""Hand-made truth for the evals: the golden set and the judge calibration pairs."""

from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sprout.models import BusinessStatus, Category, Stage

EVALS_DIR = Path(__file__).parent
GOLDEN_PATH = EVALS_DIR / "golden.yaml"
CALIBRATION_PATH = EVALS_DIR / "judge_calibration.yaml"

# Strict so a quoted or fractional amount in the labels fails instead of being coerced.
Millions = Annotated[int, Field(ge=0, strict=True)]
Sources = list[str]


class GoldenError(ValueError):
    """The label file is malformed; the message names the offending entry."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LastRound(_Strict):
    stage: Stage
    amount_million_usd: Millions | None
    announced: date | None = None
    note: str = ""
    sources: Sources


class MoneyFact(_Strict):
    """Reference only (total_raised, valuation); never scored, so sources may be empty."""

    amount_million_usd: Millions | None
    note: str = ""
    sources: Sources = []


class GoldenLabel(_Strict):
    date: date
    verified: bool


class GoldenEntry(_Strict):
    company_number: str
    name: str
    category: Category | list[Category]
    one_liner: str
    one_liner_sources: Sources = []
    status: BusinessStatus
    status_sources: Sources = []
    stage: Stage
    stage_sources: Sources
    last_round: LastRound | None = None
    total_raised: MoneyFact | None = None
    valuation: MoneyFact | None = None
    business_model: str
    business_model_sources: Sources = []
    target_user: str
    target_user_sources: Sources = []
    split: Literal["dev", "holdout"]
    label: GoldenLabel

    @property
    def categories(self) -> tuple[Category, ...]:
        """Every acceptable category; a single label becomes a one-item tuple."""
        if isinstance(self.category, list):
            return tuple(self.category)
        return (self.category,)

    @model_validator(mode="after")
    def _check_consistency(self) -> "GoldenEntry":
        if isinstance(self.category, list) and not self.category:
            raise ValueError("category list is empty")
        if not self.stage_sources:
            raise ValueError("stage_sources must not be empty")
        if self.last_round is None:
            if self.stage != Stage.UNKNOWN:
                raise ValueError(f"stage is {self.stage.value} but last_round is null")
        else:
            if not self.last_round.sources:
                raise ValueError("last_round.sources must not be empty")
            if self.stage != self.last_round.stage:
                raise ValueError(
                    f"stage {self.stage.value} differs from last_round.stage "
                    f"{self.last_round.stage.value}"
                )
        return self


class CalibrationPair(_Strict):
    company: str
    field: Literal["business_model", "target_user"]
    reference: str
    candidate: str
    verdict: Literal["correct", "partial", "incorrect"]


def _read_yaml_list(path: Path, key: str) -> list[dict]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GoldenError(f"{path}: cannot read: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise GoldenError(f"{path}: expected a mapping with a '{key}' list")
    return data[key]


def _label_of(raw: object, index: int) -> str:
    if isinstance(raw, dict):
        return str(raw.get("name") or raw.get("company_number") or f"#{index}")
    return f"#{index}"


def _validate_all[M: BaseModel](
    model: type[M], rows: list[dict], path: Path, label_of=_label_of
) -> list[M]:
    parsed: list[M] = []
    for index, raw in enumerate(rows):
        try:
            parsed.append(model.model_validate(raw))
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'entry'}: {err['msg']}"
                for err in exc.errors()
            )
            raise GoldenError(f"{path}: {label_of(raw, index)}: {problems}") from exc
    return parsed


def load_golden(path: Path = GOLDEN_PATH, *, verified_only: bool = True) -> list[GoldenEntry]:
    """Load and validate the golden set; every entry is validated, then unverified ones dropped."""
    entries = _validate_all(GoldenEntry, _read_yaml_list(path, "entries"), path)
    seen: set[str] = set()
    for entry in entries:
        if entry.company_number in seen:
            raise GoldenError(
                f"{path}: {entry.name}: duplicate company_number {entry.company_number}"
            )
        seen.add(entry.company_number)
    return [e for e in entries if e.label.verified or not verified_only]


def load_calibration(path: Path = CALIBRATION_PATH) -> list[CalibrationPair]:
    """Load the hand-graded (reference, candidate, verdict) pairs for judge calibration."""
    return _validate_all(
        CalibrationPair,
        _read_yaml_list(path, "pairs"),
        path,
        lambda raw, i: f"{raw.get('company', '?')} pair #{i}" if isinstance(raw, dict) else f"#{i}",
    )
