from datetime import date

import pytest

from sprout.candidates import DISCOVERY_TERMS, assess


@pytest.mark.parametrize(
    "name",
    [
        "Acme Machine Learning Ltd",
        "NEURAL WORKS LIMITED",
        "Foo Robotics Ltd",
        "Generative Labs Ltd",
    ],
)
def test_strong_keyword_alone_is_enough(name):
    assert assess(name, ["47910"]).is_candidate


def test_weak_keyword_needs_a_tech_sic_code():
    assert not assess("Faculty Ai Ltd", ["56101"]).is_candidate
    assert assess("Faculty Ai Ltd", ["62012"]).is_candidate


def test_dotted_abbreviation_is_recognised():
    assert assess("A.I. Foundry Ltd", ["62020"]).is_candidate


@pytest.mark.parametrize("name", ["Airways Holdings Ltd", "Maintain Ltd", "Plain Software Ltd"])
def test_substrings_do_not_match(name):
    assert not assess(name, ["62012"]).is_candidate


def test_inactive_or_old_companies_are_rejected():
    assert not assess("Machine Learning Ltd", status="dissolved").is_candidate
    assert not assess("Machine Learning Ltd", incorporated=date(2005, 1, 1)).is_candidate
    assert assess("Machine Learning Ltd", incorporated=date(2020, 1, 1)).is_candidate


def test_score_rewards_more_evidence():
    weak = assess("Acme Intelligence Ltd", ["62012"])
    strong = assess("Acme Artificial Intelligence Ltd", ["62012"])
    assert strong.score > weak.score > 0
    assert "artificial intelligence" in strong.matched_keywords


def test_discovery_terms_are_lowercase_and_unique():
    assert len(set(DISCOVERY_TERMS)) == len(DISCOVERY_TERMS)
    assert all(t == t.lower() for t in DISCOVERY_TERMS)
