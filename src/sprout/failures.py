"""Fixed failure taxonomy for runs. Pure: no sprout imports, so loop, enrich and db can share it."""

from enum import StrEnum


class FailureCategory(StrEnum):
    """One value per non-success outcome; success is stored as NULL, not as a category."""

    INVALID_OUTPUT = "invalid_output"
    ITERATION_CAP = "iteration_cap"
    BUDGET_CAP = "budget_cap"
    NO_SUBMISSION = "no_submission"
    OUTPUT_TRUNCATED = "output_truncated"
    REFUSAL = "refusal"
    API_ERROR = "api_error"
    INTERNAL_ERROR = "internal_error"


_BY_STOP: dict[str, FailureCategory] = {
    "invalid": FailureCategory.INVALID_OUTPUT,
    "max_iter": FailureCategory.ITERATION_CAP,
    "budget": FailureCategory.BUDGET_CAP,
    "no_submit": FailureCategory.NO_SUBMISSION,
    "truncated": FailureCategory.OUTPUT_TRUNCATED,
    "refused": FailureCategory.REFUSAL,
    "error": FailureCategory.API_ERROR,
}


def failure_category_for(stop: str) -> FailureCategory | None:
    """Map a `LoopResult.stop` to its category: None for success, INTERNAL_ERROR if unrecognised."""
    if stop == "submitted":
        return None
    return _BY_STOP.get(stop, FailureCategory.INTERNAL_ERROR)
