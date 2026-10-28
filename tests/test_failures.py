import pytest

from sprout.failures import FailureCategory, failure_category_for


def test_submitted_is_success_and_has_no_category():
    assert failure_category_for("submitted") is None


@pytest.mark.parametrize(
    ("stop", "expected"),
    [
        ("invalid", "invalid_output"),
        ("max_iter", "iteration_cap"),
        ("budget", "budget_cap"),
        ("no_submit", "no_submission"),
        ("truncated", "output_truncated"),
        ("refused", "refusal"),
        ("error", "api_error"),
    ],
)
def test_each_failing_stop_maps_to_its_category(stop, expected):
    assert failure_category_for(stop) == FailureCategory(expected)


def test_unknown_stop_is_an_internal_error_not_silent():
    assert failure_category_for("something_new") is FailureCategory.INTERNAL_ERROR


def test_taxonomy_has_eight_values():
    assert len(FailureCategory) == 8
