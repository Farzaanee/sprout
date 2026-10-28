# Human to-dos, Phase 4 (Evals & guardrails)

Source: `docs/architecture.md` section 6.3 and the Phase 4 row in section 7, cross-checked against `docs/project-plan.md`. Phase 4 is design-only right now, nothing in `evals/` is built yet. (Renamed from `human-todos-phase3.md`: Phase 3 is now Observability, inserted ahead of evals.)

## Decisions only you can make (before or during implementation)

- [ ] **Hand-label the golden set**, about 30 companies, in `evals/golden.yaml` (category, stage, last-round amount if public, target user). Record the label date and source per the plan's caveat. Flagged as critical path, about 1.5 hours (A21).
- [ ] **Approve the fabricated-funding definition**: number-match against the cited page text as the model saw it, not "value present with no source" (that one is always 0 percent by construction). (A18, D21)
- [ ] **Approve the last-round rule**, i.e. how "latest funding round" is picked when sources disagree. (A19)
- [ ] **Approve the two-tier injection acceptance**: offline `ScriptedClient` cases plus a live canary tier gated on `SPROUT_RUN_LIVE_INJECTION=1`. (A20)
- [ ] **Choose the golden-set file format**: `pyyaml` or TOML. (A24)
- [ ] **Confirm the eval budget and default cap**: US$1.50 default. (A23, A26)
- [ ] **Confirm the judge calibration plan**: Sonnet 5.5 judging Haiku 5.5 output, about 12 graded triples, 80 percent agreement threshold before judge rows are trusted. (A26)
- [ ] **Veto or accept the other design decisions** if you disagree with any: repo-root `evals/` layout (D19), separate `data/eval.db` (D20), judge calls through `run_agent` with a `submit_verdict` tool (D23), `BatchBudget` (D24), `UrlPolicy` (D25), the overall injection design (D26).
- [ ] **Decide on retry and partial-facts behaviour for failed enrichment runs.** Today a failed run (`invalid`, `max_iter`, `budget`, `refused`, `error`, ...) persists nothing but the `run_log` row, costs money, and is never retried automatically; you have to rerun the CLI by hand. The docs note an earlier "keep partial facts" design was dropped (`docs/implementation-notes.md:198`), and currently scope retry plus an `enrichment_state` table to Phase 5 (`docs/architecture.md`, section 6.1/6.3). Decide whether Phase 4 should pull any of this forward (even a simple retry-on-rerun) or leave it fully for Phase 5, since it affects how you read the eval numbers (repeated failures vs. one-shot).

## Actions that need you specifically (not just review)

- [ ] **Spot-check the LLM judge** against your own judgement on the free-text fields (business model, target user), not just trust the calibration percentage.
- [ ] **Explicitly opt in to live injection testing** by setting `SPROUT_RUN_LIVE_INJECTION=1` when you want the live canary tier to run (it spends money and is skipped otherwise).
- [ ] **Review false positives** from the number-match fabrication check after the first real run (it can miss spelled-out or summed figures, so expect some to review manually).
- [ ] **Run the baseline eval**, then set `GATES` one company below the result, and record the run's table in `evals/results.md`. Repeat as you iterate on prompts.
- [ ] **Confirm the eval budget cap in practice**, not just approve the number above, by watching actual spend on the first few runs.

## Carried over from earlier phases, still outstanding

- [ ] **Review the 10 live pilot profiles yourself** (the existing review was done by the implementing agent, not you). (Phase 2)
- [ ] **Check the first Anthropic/search console invoice** against the assumed US$10 per 1,000 searches price used in `costs.py` (D14). (Phase 2)
- [ ] **Decide whether `data/sprout.db` stays tracked by git.** (Phase 2)
- [ ] **Decide on the observability design** (Phase 3, not yet detailed): failure-taxonomy shape, `run_log.stats` extension vs. a new step-level table, whether the dashboard page extends the existing Streamlit progress console or is new. See `docs/architecture.md` 2.4.

## Final acceptance check (do this last)

- [ ] `uv run python -m evals.run_evals` runs in one command and prints the metrics table.
- [ ] Baseline thresholds hold: category accuracy ≥ 85%, fabricated-funding rate ≤ 3% (adjust once, after the first run, then treat as a regression gate).
- [ ] All injection fixtures leave the output unchanged, each as a pytest case.
- [ ] SSRF/private-IP URLs are rejected in tests.
