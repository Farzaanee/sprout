"""Offline tests of the eval runner: scripted model, local pages, temp databases, no network."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from evals import run_evals
from evals.fixtures import fixture_fetcher
from evals.golden import load_calibration
from evals.judge import TERMINAL_TOOL
from evals.metrics import Observed, ObservedFact, Ratio
from evals.run_evals import (
    GATES,
    Extras,
    Metric,
    Services,
    _delta,
    append_record,
    check_liveness,
    main,
    previous_counts,
)
from fakes import ScriptedClient, text_turn, tool_use_turn
from sprout.config import Settings
from sprout.sources.http import FetchError

URL = "https://acme.example/news"
PAGE = "<title>Acme</title><p>Acme raised US$12 million in a Series A.</p>"
NOT_DISCLOSED = {"value": None, "source_url": None, "confidence": "low", "note": None}
NUMBERS = ("00000001", "00000002", "00000003")


def golden_entry(number, name, split="dev", category="Cybersecurity", funded=True):
    return {
        "company_number": number,
        "name": name,
        "category": category,
        "one_liner": "Reference.",
        "status": "Active",
        "stage": "Series A" if funded else "Unknown",
        "stage_sources": [URL],
        "last_round": (
            {"stage": "Series A", "amount_million_usd": 12, "sources": [URL]} if funded else None
        ),
        "business_model": "SaaS for banks.",
        "business_model_sources": [URL],
        "target_user": "Banks.",
        "target_user_sources": [URL],
        "split": split,
        "label": {"date": date(2026, 11, 5), "verified": True},
    }


GOLDEN = [
    golden_entry(NUMBERS[0], "Alpha AI"),
    golden_entry(NUMBERS[1], "Beta Holdings", split="holdout", category="Other", funded=False),
    golden_entry(NUMBERS[2], "Gamma AI"),
]


def fact(value, note=None):
    return {"value": value, "source_url": URL, "confidence": "high", "note": note}


def submission(category="Cybersecurity", amount=12, funded=True, free_text=True):
    round_ = {
        "value": {"stage": "Series A", "amount_million_usd": amount, "investors": []},
        "source_url": URL,
        "confidence": "high",
        "note": f"Reported as US${amount} million",
    }
    return {
        "website": NOT_DISCLOSED,
        "hq": NOT_DISCLOSED,
        "founded": NOT_DISCLOSED,
        "category": category,
        "stage": fact("Series A") if funded else NOT_DISCLOSED,
        "business_status": fact("Active"),
        "total_raised_million_usd": NOT_DISCLOSED,
        "valuation_million_usd": NOT_DISCLOSED,
        "funding_rounds": [round_] if funded else [],
        "business_model": fact("Software sold to banks.") if free_text else NOT_DISCLOSED,
        "target_user": fact("Banks.") if free_text else NOT_DISCLOSED,
        "one_liner": fact("Acme builds things."),
    }


def company_turns(usage=None, **kwargs):
    usage = usage or {}
    return [
        tool_use_turn(("fetch_page", {"url": URL}), **usage),
        tool_use_turn(("submit_profile", submission(**kwargs))),
    ]


def good_run():
    """Turns for the three companies: all correct."""
    return (
        company_turns()
        + company_turns(category="Other", funded=False, free_text=False)
        + company_turns()
    )


def verdict_turn(verdict):
    return tool_use_turn((TERMINAL_TOOL, {"verdict": verdict, "reason": "because"}))


class FakeCompaniesHouse:
    def get_company(self, number):
        return {
            "company_number": number,
            "company_name": f"REGISTERED {number} LTD",
            "company_status": "active",
            "date_of_creation": "2020-01-01",
            "sic_codes": ["62012"],
        }


@pytest.fixture
def env(tmp_path, monkeypatch):
    golden = tmp_path / "golden.yaml"
    golden.write_text(yaml.safe_dump({"version": 1, "entries": GOLDEN}), encoding="utf-8")
    monkeypatch.setattr(run_evals, "GOLDEN_PATH", golden)
    monkeypatch.setattr(run_evals, "RESULTS_PATH", tmp_path / "results.md")
    fetcher, _ = fixture_fetcher({URL: PAGE})
    settings = Settings(
        "key", None, "claude-haiku-5-5", "claude-sonnet-5-5", tmp_path / "live.db", tmp_path / "c"
    )

    def run(*argv, client=None, ch=None, settings=settings):
        services = Services(client, fetcher, ch)
        code = main(
            ["--db", str(tmp_path / "eval.db"), *argv],
            settings=settings,
            open_services_fn=lambda _s, _stack: services,
        )
        return code

    return SimpleNamespace(
        run=run,
        settings=settings,
        results=tmp_path / "results.md",
        tmp=tmp_path,
        ch=FakeCompaniesHouse(),
    )


def full_run(env, *turns, extra=()):
    client = ScriptedClient(*(turns or good_run()))
    return env.run("--skip-judge", *extra, client=client, ch=env.ch), client


# ---- exit codes and gates -------------------------------------------------------------------


def test_complete_full_run_passes_gates_with_exit_0(env, capsys):
    code, client = full_run(env)
    out = capsys.readouterr().out
    assert code == 0
    assert client.calls == 6
    assert "| Metric | Result | Delta | Gate |" in out
    assert "| Category accuracy | 3/3 (100.0%) | n/a | >= 80% pass |" in out
    assert "| Fabricated funding | 0/2 (0.0%) | n/a | <= 2% pass |" in out
    assert "| Thin Other profiles | 1/3 (33.3%) | n/a | report |" in out
    assert "Gates: pass." in out
    assert "scored 3 of 3" in out


def test_failed_gate_exits_1_and_misses_show_both_company_names(env, capsys):
    turns = company_turns() + company_turns(category="Other", funded=False, free_text=False)
    turns += company_turns(category="Other")
    code, _ = full_run(env, *turns)
    out = capsys.readouterr().out
    assert code == 1
    assert "| Category accuracy | 2/3 (66.7%) | n/a | >= 80% FAIL |" in out
    assert "Gates: FAIL (category_accuracy)." in out
    assert f"- Gamma AI ({NUMBERS[2]}, Companies House: REGISTERED {NUMBERS[2]} LTD)" in out
    assert "category: golden Cybersecurity; observed Other" in out


def test_fabricated_funding_fails_its_gate(env, capsys):
    turns = company_turns(amount=99) + company_turns(
        category="Other", funded=False, free_text=False
    )
    turns += company_turns()
    code, _ = full_run(env, *turns)
    out = capsys.readouterr().out
    assert code == 1
    assert "| Fabricated funding | 1/2 (50.0%) | n/a | <= 2% FAIL |" in out
    assert "fabricated round:Series A US$99m" in out


def test_gates_are_the_baseline_set():
    assert set(GATES) == {"category_accuracy", "business_status_accuracy", "fabricated_funding"}


def test_missing_anthropic_key_exits_2(env, capsys):
    settings = Settings(None, None, "claude-haiku-5-5", "claude-sonnet-5-5")
    assert env.run("--skip-judge", settings=settings, ch=env.ch) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_company_not_in_db_and_no_companies_house_key_exits_2(env, capsys):
    client = ScriptedClient()
    assert env.run("--skip-judge", client=client, ch=None) == 2
    assert client.calls == 0
    assert "no Companies House key" in capsys.readouterr().err


def test_unknown_only_target_exits_2(env, capsys):
    assert env.run("--skip-judge", "--only", "nobody", client=ScriptedClient(), ch=env.ch) == 2
    assert "nobody" in capsys.readouterr().err


def test_refuses_to_reset_the_live_database(env, capsys):
    code = main(
        ["--db", str(env.settings.db_path), "--skip-judge"],
        settings=env.settings,
        open_services_fn=lambda _s, _stack: Services(ScriptedClient(), None, env.ch),  # type: ignore[arg-type]
    )
    assert code == 2
    assert "live database" in capsys.readouterr().err


# ---- budget, subsets, splits ----------------------------------------------------------------


def test_budget_stop_marks_the_run_incomplete_and_ungated(env, capsys):
    # One 200k-token response costs US$0.02 on Haiku, over the US$0.01 total cap.
    client = ScriptedClient(*company_turns(usage={"input": 200_000})[:1])
    code = env.run("--skip-judge", "--max-total-usd", "0.01", client=client, ch=env.ch)
    out = capsys.readouterr().out
    assert code == 2
    assert client.calls == 1  # the other two companies were never started
    assert "scored 1 of 3" in out
    assert "- Beta Holdings: skipped_budget" in out
    assert "- Gamma AI: skipped_budget" in out
    assert "Gates not evaluated: incomplete run" in out
    assert ">= 80% not evaluated" in out
    assert "| Total cost | USD 0.0200" in out


def test_limit_runs_a_subset_and_does_not_evaluate_gates(env, capsys):
    turns = company_turns(category="Other") + company_turns(category="Other", funded=False)
    code, client = full_run(env, *turns, extra=["--limit", "2"])
    out = capsys.readouterr().out
    assert code == 0  # category accuracy would fail the gate on a full run
    assert client.calls == 4
    assert "scored 2 of 2" in out
    assert "Gates not evaluated: subset run" in out
    assert ">= 80% not evaluated" in out


def test_only_reruns_just_the_named_entry_and_keeps_the_database(env, capsys):
    full_run(env)
    capsys.readouterr()
    client = ScriptedClient(*company_turns(category="Other"))
    code = env.run("--skip-judge", "--only", NUMBERS[0], client=client, ch=env.ch)
    out = capsys.readouterr().out
    assert code == 0
    assert client.calls == 2
    assert "scored 1 of 1" in out
    assert "| Category accuracy | 0/1 (0.0%)" in out


@pytest.mark.parametrize(
    ("split", "names"),
    [("dev", {"Alpha AI", "Gamma AI"}), ("holdout", {"Beta Holdings"})],
)
def test_split_selects_only_its_entries_and_is_not_gated(env, capsys, split, names):
    turns = [
        t
        for name in sorted(names)
        for t in (
            company_turns(category="Other", funded=False, free_text=False)
            if name == "Beta Holdings"
            else company_turns()
        )
    ]
    code, client = full_run(env, *turns, extra=["--split", split])
    out = capsys.readouterr().out
    assert code == 0
    assert client.calls == 2 * len(names)
    assert f"split {split} | scored {len(names)} of {len(names)}" in out
    assert "Gates not evaluated: subset run" in out


# ---- score-only -----------------------------------------------------------------------------


def test_score_only_rescoring_makes_no_enrichment_calls(env, capsys):
    full_run(env)
    capsys.readouterr()
    client = ScriptedClient()
    code = env.run("--score-only", "--skip-judge", client=client, ch=None)
    again = capsys.readouterr().out
    assert code == 0
    assert client.calls == 0
    assert "| Category accuracy | 3/3 (100.0%)" in again
    assert "Gates: pass." in again


def test_score_only_without_a_database_exits_2(env, capsys):
    assert env.run("--score-only", "--skip-judge", client=None, ch=None) == 2
    assert "existing eval database" in capsys.readouterr().err


def test_score_only_on_a_partial_database_is_incomplete(env, capsys):
    full_run(env, *company_turns(), extra=["--only", NUMBERS[0]])
    capsys.readouterr()
    code = env.run("--score-only", "--skip-judge", client=None, ch=None)
    out = capsys.readouterr().out
    assert code == 2
    assert "scored 1 of 3" in out
    assert "- Gamma AI: no run" in out


# ---- judge and calibration ------------------------------------------------------------------


def test_judge_rows_are_uncalibrated_and_missing_candidates_are_not_sent(env, capsys):
    verdicts = [verdict_turn(v) for v in ("correct", "partial", "incorrect", "correct")]
    client = ScriptedClient(*good_run(), *verdicts)
    code = env.run(client=client, ch=env.ch)
    out = capsys.readouterr().out
    assert code == 0
    assert client.calls == 6 + 4  # Beta has no free text: counted missing, never judged
    assert {r["model"] for r in client.requests[6:]} == {"claude-sonnet-5-5"}
    assert (
        "| Judge business_model correct | 1/3 (33.3%); partial 1, incorrect 0, unjudged 0, "
        "missing 1 [uncalibrated] | n/a | report |" in out
    )
    assert "Gamma AI business_model: partial (because)" in out


def test_calibration_at_80_percent_lifts_the_uncalibrated_label(env, capsys):
    full_run(env)
    capsys.readouterr()
    human = [verdict_turn(p.verdict) for p in load_calibration()]
    judged = [verdict_turn("correct")] * 4
    client = ScriptedClient(*human, *judged)
    code = env.run("--score-only", "--calibrate", client=client, ch=None)
    out = capsys.readouterr().out
    assert code == 0
    assert "| Judge calibration | 12/12 (100.0%) | n/a | >= 80% calibrated |" in out
    assert "[uncalibrated]" not in out
    assert "Calibration (human grade -> judge verdict): correct->correct 4" in out


# ---- liveness -------------------------------------------------------------------------------


def observed_citing(urls, fetched):
    facts = tuple(ObservedFact("one_liner", "x", u, None) for u in urls)
    return Observed(has_run=True, sources=tuple((u, u in fetched) for u in urls), all_facts=facts)


def test_liveness_fetches_only_snippet_only_urls_and_classifies_failures():
    urls = ["https://a.example/ok", "https://b.example/gone", "https://c.example/5xx",
            "https://d.example/snippet", "https://e.example/nodns"]  # fmt: skip
    errors = {
        "https://b.example/gone": FetchError("GET returned 404", status_code=404),
        "https://c.example/5xx": FetchError("GET returned 503", status_code=503),
        "https://e.example/nodns": FetchError("could not resolve e.example"),
    }
    asked = []

    class Fetcher:
        def fetch(self, url):
            asked.append(url)
            if url in errors:
                raise errors[url]

    pairs = [(None, observed_citing(urls, fetched={"https://a.example/ok"}))]
    result = check_liveness(Fetcher(), pairs)  # type: ignore[arg-type]
    assert "https://a.example/ok" not in asked
    assert (result.ok, result.dead, result.unchecked) == (2, 2, 1)
    assert set(result.dead_urls) == {"https://b.example/gone", "https://e.example/nodns"}


def test_check_urls_row_is_in_the_table(env, capsys):
    code, _ = full_run(env, extra=["--check-urls"])
    out = capsys.readouterr().out
    assert code == 0
    assert "| Dead cited URLs | 0/1 (0.0%); 1 ok, 0 unchecked | n/a | report |" in out


# ---- delta, noise and --record --------------------------------------------------------------


def test_delta_compares_percentages_and_marks_small_moves_as_noise():
    previous = {"category_accuracy": [28, 32]}  # 87.5%

    def delta(num, den=32):
        return _delta(previous, Metric("category_accuracy", "x", Ratio(num, den)))

    assert delta(28) == "0.0pp"
    assert delta(29) == "+3.1pp (noise)"
    assert delta(27) == "-3.1pp (noise)"
    assert delta(30) == "+6.2pp"
    assert delta(24) == "-12.5pp"
    assert delta(10, den=10) == "+12.5pp"  # a different denominator still compares the percentages
    assert _delta(None, Metric("category_accuracy", "x", Ratio(1, 1))) == "n/a"
    assert _delta(previous, Metric("stage_accuracy", "x", Ratio(1, 1))) == "n/a"


def test_record_appends_a_heading_table_and_parseable_counts(env, capsys):
    full_run(env, extra=["--record"])
    text = env.results.read_text(encoding="utf-8")
    assert text.startswith("# Eval results")
    assert "| Metric | Result | Delta | Gate |" in text
    heading = next(line for line in text.splitlines() if line.startswith("## "))
    assert "claude-haiku-5-5" in heading and "effort low" in heading
    assert "split all | scored 3 of 3" in heading
    counts = previous_counts(env.results, "all")
    assert counts is not None
    assert counts["category_accuracy"] == [3, 3]
    assert counts["full"] is True
    assert counts["scored"] == 3


def test_next_run_reads_the_recorded_counts_for_its_delta_column(env, capsys):
    full_run(env, extra=["--record"])
    capsys.readouterr()
    turns = (
        company_turns(category="Other")
        + company_turns(category="Other", funded=False, free_text=False)
        + company_turns()
    )
    code, _ = full_run(env, *turns, extra=["--record"])
    out = capsys.readouterr().out
    assert code == 1
    assert "| Category accuracy | 2/3 (66.7%) | -33.3pp | >= 80% FAIL |" in out
    assert env.results.read_text(encoding="utf-8").count("# Eval results") == 1
    assert len(run_evals.COUNTS_COMMENT.findall(env.results.read_text(encoding="utf-8"))) == 2


def test_previous_counts_uses_only_full_runs_of_the_same_split(tmp_path):
    path = tmp_path / "results.md"
    for counts in (
        {"split": "all", "full": True, "category_accuracy": [1, 2]},
        {"split": "holdout", "full": True, "category_accuracy": [9, 9]},
        {"split": "all", "full": False, "category_accuracy": [5, 5]},
    ):
        append_record(path, "## run", counts)
    path.write_text(path.read_text() + "<!-- sprout-eval-counts {broken} -->\n")
    assert previous_counts(path, "all")["category_accuracy"] == [1, 2]
    assert previous_counts(path, "dev") is None
    assert previous_counts(tmp_path / "absent.md", "all") is None


def test_limit_run_is_recorded_but_never_becomes_a_baseline(env, capsys):
    full_run(env, *company_turns(), extra=["--limit", "1", "--record"])
    assert json.loads(run_evals.COUNTS_COMMENT.findall(env.results.read_text())[0])["full"] is False
    assert previous_counts(env.results, "all") is None


def test_total_cost_includes_judge_spend():
    extras = Extras(
        enrich_usd=Decimal("0.03"), judge_usd=Decimal("0.01"), injection_usd=Decimal("0")
    )
    assert extras.total_usd == Decimal("0.04")


def test_runner_never_writes_results_without_record(env):
    full_run(env)
    assert not Path(env.results).exists()


# ---- agents on top of enrichment (D28, D41) -------------------------------------------------


def classify_turn(category="Cybersecurity"):
    return tool_use_turn(
        ("submit_classification", {"category": category, "rationale": "Because."}),
        input=1_000,
        output=100,
    )


def classified_run(enriched, classified):
    """Turns for the three companies: enrichment's category, then the classifier's."""
    turns = []
    for number, first, second in zip(NUMBERS, enriched, classified, strict=True):
        funded = number != NUMBERS[1]
        turns += company_turns(category=first, funded=funded, free_text=funded)
        turns.append(classify_turn(second))
    return turns


def test_agents_classify_reports_before_after_and_the_agent_set(env, capsys):
    # Alpha: wrong -> right (corrected). Beta: right -> wrong (broken). Gamma: right -> right.
    turns = classified_run(
        ["Other", "Other", "Cybersecurity"], ["Cybersecurity", "Cybersecurity", "Cybersecurity"]
    )
    code, client = full_run(env, *turns, extra=["--agents", "classify"])
    out = capsys.readouterr().out
    assert client.calls == 9
    assert code == 1  # the gate reads the classifier's labels: 2/3 is below 80%
    assert "| agents classify | split all" in out
    assert "(classify " in out.splitlines()[0]  # classifier prompt hash beside enrichment's
    assert "| Category accuracy | 2/3 (66.7%) | n/a | >= 80% FAIL |" in out
    assert "| Category accuracy before classifier | 2/3 (66.7%) | n/a | report |" in out
    assert "| Classifier corrected | 1/3 (33.3%) | n/a | report |" in out
    assert "| Classifier broken | 1/3 (33.3%); net +0 | n/a | report |" in out
    assert "| Classifier cost per company | mean USD 0.0002" in out
    assert ", classify 0.000" in out


def test_default_run_has_no_agents_and_no_classifier_rows(env, capsys):
    full_run(env)
    out = capsys.readouterr().out
    assert "| agents none | split all" in out
    assert "Classifier" not in out


def test_classifier_failure_keeps_enrichments_category_and_counts_the_cost(env, capsys):
    bad = [text_turn(input=2_000)] * 2  # never submits
    turns = company_turns() + bad + company_turns(category="Other", funded=False, free_text=False)
    turns += [classify_turn("Other")] + company_turns() + [classify_turn()]
    code, client = full_run(env, *turns, extra=["--agents", "classify"])
    out = capsys.readouterr().out
    assert code == 0 and client.calls == 2 + 2 + 2 + 1 + 2 + 1
    assert "| Category accuracy | 3/3 (100.0%)" in out  # Alpha keeps enrichment's right answer
    assert "| Classifier corrected | 0/2 (0.0%)" in out  # only the two that were classified


def test_reuse_enrichment_runs_only_the_agent_and_keeps_the_database(env, capsys):
    turns = company_turns(category="Other")
    turns += company_turns(category="Other", funded=False, free_text=False)
    turns += company_turns()
    full_run(env, *turns)
    capsys.readouterr()
    classified = [classify_turn(c) for c in ("Cybersecurity", "Other", "Cybersecurity")]
    client = ScriptedClient(*classified)
    code = env.run(
        "--skip-judge", "--agents", "classify", "--reuse-enrichment", client=client, ch=env.ch
    )
    out = capsys.readouterr().out
    assert code == 0
    assert client.calls == 3
    assert {r["tools"][-1]["name"] for r in client.requests} == {"submit_classification"}
    assert "agents classify (reused enrichment)" in out
    assert "| Category accuracy | 3/3 (100.0%) | n/a | >= 80% pass |" in out
    assert "| Category accuracy before classifier | 2/3 (66.7%)" in out
    assert "| Classifier corrected | 1/3 (33.3%)" in out
    assert "(enrichment 0.0000, classify" in out  # reused enrichment is not spend of this run


def test_reuse_enrichment_enriches_entries_that_have_no_ok_enrichment(env, capsys):
    full_run(env, *company_turns(), extra=["--only", NUMBERS[0]])
    capsys.readouterr()
    turns = [classify_turn()]  # Alpha reused
    turns += company_turns(category="Other", funded=False, free_text=False) + [
        classify_turn("Other")
    ]
    turns += company_turns() + [classify_turn()]
    client = ScriptedClient(*turns)
    code = env.run("--skip-judge", "--agents", "classify", "--reuse-enrichment",
                   client=client, ch=env.ch)  # fmt: skip
    assert client.calls == 1 + 3 + 3
    assert code == 0


def test_a_second_classify_pass_still_scores_against_the_enrichment_category(env, capsys):
    full_run(env, *company_turns(category="Other") + company_turns(category="Other", funded=False,
             free_text=False) + company_turns())  # fmt: skip
    args = ["--skip-judge", "--agents", "classify", "--reuse-enrichment"]
    env.run(*args, client=ScriptedClient(*[classify_turn()] * 3), ch=env.ch)
    capsys.readouterr()
    env.run(*args, client=ScriptedClient(*[classify_turn()] * 3), ch=env.ch)
    out = capsys.readouterr().out
    assert "| Category accuracy before classifier | 2/3 (66.7%)" in out


def test_reuse_enrichment_needs_agents_and_an_existing_database(env, capsys):
    assert env.run("--skip-judge", "--reuse-enrichment", client=ScriptedClient(), ch=env.ch) == 2
    assert "needs --agents" in capsys.readouterr().err
    code = env.run("--skip-judge", "--agents", "classify", "--reuse-enrichment",
                   client=ScriptedClient(), ch=env.ch)  # fmt: skip
    assert code == 2
    assert "existing eval database" in capsys.readouterr().err


def test_unknown_agent_is_rejected(env):
    with pytest.raises(SystemExit):
        env.run("--agents", "news", client=ScriptedClient(), ch=env.ch)


def test_scoring_a_classified_database_without_agents_is_refused(env, capsys):
    full_run(
        env, *classified_run(["Other"] * 3, ["Cybersecurity"] * 3), extra=["--agents", "classify"]
    )
    capsys.readouterr()
    assert env.run("--score-only", "--skip-judge", client=None, ch=None) == 2
    assert "pass --agents classify" in capsys.readouterr().err
    scored = env.run("--score-only", "--skip-judge", "--agents", "classify", client=None, ch=None)
    assert scored == 1  # scored, and the classifier's Beta label fails the gate


def test_re_enriching_one_entry_drops_its_old_classification_from_the_scores(env, capsys):
    full_run(
        env, *classified_run(["Other"] * 3, ["Cybersecurity"] * 3), extra=["--agents", "classify"]
    )
    capsys.readouterr()
    code = env.run("--skip-judge", "--only", NUMBERS[0], client=ScriptedClient(*company_turns()),
                   ch=env.ch)  # fmt: skip
    assert code == 0
    assert "Classifier" not in capsys.readouterr().out


def test_previous_counts_compares_only_the_same_agent_set(tmp_path):
    path = tmp_path / "results.md"
    for counts in (
        {"split": "all", "full": True, "category_accuracy": [1, 2]},  # old record: no agents key
        {"split": "all", "full": True, "agents": ["classify"], "category_accuracy": [5, 5]},
        {"split": "all", "full": True, "agents": [], "category_accuracy": [3, 4]},
    ):
        append_record(path, "## run", counts)
    assert previous_counts(path, "all")["category_accuracy"] == [3, 4]
    assert previous_counts(path, "all", ["classify"])["category_accuracy"] == [5, 5]
    assert previous_counts(path, "all", ["classify", "news"]) is None


def test_delta_is_not_shown_against_a_run_with_a_different_agent_set(env, capsys):
    full_run(env, extra=["--record"])
    capsys.readouterr()
    full_run(env, *classified_run(["Cybersecurity", "Other", "Cybersecurity"], ["Cybersecurity",
             "Other", "Cybersecurity"]), extra=["--record", "--agents", "classify"])  # fmt: skip
    out = capsys.readouterr().out
    assert "| Category accuracy | 3/3 (100.0%) | n/a |" in out
    counts = previous_counts(env.results, "all", ["classify"])
    assert counts["agents"] == ["classify"] and counts["reuse_enrichment"] is False
