import copy
from collections import Counter
from datetime import date

import pytest
import yaml

from evals.golden import GoldenError, load_calibration, load_golden
from sprout.models import Category, Stage

ENTRY = {
    "company_number": "00000001",
    "name": "Acme AI",
    "category": "Cybersecurity",
    "one_liner": "Acme.",
    "status": "Active",
    "stage": "Seed",
    "stage_sources": ["https://example.com/s"],
    "last_round": {
        "stage": "Seed",
        "amount_million_usd": 5,
        "announced": date(2025, 1, 1),
        "sources": ["https://example.com/r"],
    },
    "total_raised": {"amount_million_usd": 5, "sources": []},
    "business_model": "SaaS.",
    "target_user": "Banks.",
    "split": "dev",
    "label": {"date": date(2026, 11, 5), "verified": True},
}


def write_entries(tmp_path, *entries):
    path = tmp_path / "golden.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "entries": list(entries)}))
    return path


def entry(**overrides):
    merged = copy.deepcopy(ENTRY)
    merged.update(overrides)
    return merged


def test_real_golden_file_loads():
    entries = load_golden()
    assert len(entries) == 32
    assert Counter(e.split for e in entries) == {"dev": 22, "holdout": 10}
    assert sum(len(e.categories) > 1 for e in entries) == 8
    assert sum(e.last_round is None for e in entries) == 10


def test_real_calibration_file_loads():
    pairs = load_calibration()
    assert len(pairs) == 12
    assert Counter(p.verdict for p in pairs) == {"correct": 5, "partial": 3, "incorrect": 4}
    assert Counter(p.field for p in pairs) == {"business_model": 7, "target_user": 5}


def test_categories_is_a_tuple_for_string_and_list(tmp_path):
    path = write_entries(
        tmp_path,
        entry(),
        entry(company_number="2", category=["Cybersecurity", "Other"]),
    )
    single, several = load_golden(path)
    assert single.categories == (Category.CYBERSECURITY,)
    assert several.categories == (Category.CYBERSECURITY, Category.OTHER)


def test_money_fact_with_empty_sources_and_null_amount_loads(tmp_path):
    path = write_entries(
        tmp_path, entry(valuation={"amount_million_usd": None, "note": "not public"})
    )
    (loaded,) = load_golden(path)
    assert loaded.total_raised.sources == []
    assert loaded.valuation.amount_million_usd is None


def test_no_round_requires_unknown_stage(tmp_path):
    ok = entry(stage="Unknown", last_round=None, stage_sources=["https://example.com/s"])
    (loaded,) = load_golden(write_entries(tmp_path, ok))
    assert loaded.stage is Stage.UNKNOWN
    with pytest.raises(GoldenError, match="Acme AI.*last_round is null"):
        load_golden(write_entries(tmp_path, entry(last_round=None)))


def test_stage_must_equal_last_round_stage(tmp_path):
    with pytest.raises(GoldenError, match="Acme AI.*differs from last_round.stage"):
        load_golden(write_entries(tmp_path, entry(stage="Series A")))


def test_duplicate_company_number(tmp_path):
    path = write_entries(tmp_path, entry(), entry(name="Other"))
    with pytest.raises(GoldenError, match="Other.*duplicate company_number 00000001"):
        load_golden(path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": "Not a category"},
        {"category": ["Cybersecurity", "Nope"]},
        {"category": []},
        {"status": "Thriving"},
        {"split": "train"},
        {"stage": "Series Z"},
    ],
)
def test_bad_enum_value_names_the_entry(tmp_path, overrides):
    with pytest.raises(GoldenError, match="Acme AI"):
        load_golden(write_entries(tmp_path, entry(**overrides)))


def test_missing_required_sources(tmp_path):
    no_round_sources = entry(last_round={**ENTRY["last_round"], "sources": []})
    with pytest.raises(GoldenError, match="last_round.sources"):
        load_golden(write_entries(tmp_path, no_round_sources))
    with pytest.raises(GoldenError, match="stage_sources"):
        load_golden(write_entries(tmp_path, entry(stage_sources=[])))
    missing = copy.deepcopy(ENTRY)
    del missing["last_round"]["sources"]
    with pytest.raises(GoldenError, match="last_round.sources"):
        load_golden(write_entries(tmp_path, missing))


@pytest.mark.parametrize("amount", [-1, "5", 5.5, True])
def test_bad_amount(tmp_path, amount):
    bad = entry(last_round={**ENTRY["last_round"], "amount_million_usd": amount})
    with pytest.raises(GoldenError, match="amount_million_usd"):
        load_golden(write_entries(tmp_path, bad))


def test_unknown_field_rejected(tmp_path):
    with pytest.raises(GoldenError, match="Acme AI"):
        load_golden(write_entries(tmp_path, entry(surprise=1)))


def test_unverified_entries_filtered_out_unless_requested(tmp_path):
    unverified = entry(company_number="2", label={"date": date(2026, 11, 5), "verified": False})
    path = write_entries(tmp_path, entry(), unverified)
    assert [e.company_number for e in load_golden(path)] == ["00000001"]
    assert len(load_golden(path, verified_only=False)) == 2


def test_malformed_file(tmp_path):
    path = tmp_path / "golden.yaml"
    path.write_text("version: 1\n")
    with pytest.raises(GoldenError, match="'entries' list"):
        load_golden(path)
    with pytest.raises(GoldenError, match="cannot read"):
        load_golden(tmp_path / "missing.yaml")


def test_calibration_rejects_bad_verdict_and_field(tmp_path):
    pair = {"company": "Acme", "field": "business_model", "reference": "r", "candidate": "c"}
    for bad in ({**pair, "verdict": "great"}, {**pair, "verdict": "correct", "field": "name"}):
        path = tmp_path / "cal.yaml"
        path.write_text(yaml.safe_dump({"version": 1, "pairs": [bad]}))
        with pytest.raises(GoldenError, match="Acme"):
            load_calibration(path)
