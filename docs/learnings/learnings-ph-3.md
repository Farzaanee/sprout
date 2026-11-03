# Sprout: Learnings - Phase 3 - Observability

**Planned:** structured logging/observability design for agentic systems, designing a failure taxonomy for comparability, building a minimal internal dashboard from operational data.

**Actual (done, with caveats: the live `iteration_cap` check was not achieved, and the Observability tab is verified with `streamlit.testing` only, not in a browser):**

| Topic | Key learning |
| --- | --- |
| Success is NULL, not a category | `RunLog.failure_category` is a nullable column and NULL means success, so "failed" is `IS NOT NULL` everywhere and the eight `FailureCategory` values (one per non-success `Stop`, plus `internal_error`) never need a "none" member. `failure_category_for` maps any unrecognised stop to `internal_error` rather than raising, so a new `Stop` value shows up in the data instead of breaking a run. |
| Taxonomy from stop reasons only | The taxonomy is the loop's stop reason, nothing deeper (A28: `invalid_output` is not split into sub-reasons). That was enough to be useful, but it hides a lot: 7 of 20 runs in the step 3c batch needed a repair and all succeeded, so a 35 percent repair rate is invisible in `failure_category`. Quality drift that the loop recovers from needs its own metric (proposed for Phase 4). |
| Failed runs must keep their steps | `logged_run` rolls back on exceptions, so the steps and category are added and committed in the commit that already precedes `EnrichmentFailed`. If they were written after the raise, the runs most worth diagnosing would have no rows. The same ordering is why `logged_run` falls back to `internal_error` only when the category is still NULL after the rollback reloaded the committed value. |
| One step per response | A step row is one model response, not one tool call. A single response can hold a server-side `web_search` and a custom `lookup_companies_house`, so `tool_name` is comma-joined and `tool_errors` is a count. Step costs then sum exactly to `stats.cost_usd` (checked on runs 43, 44 and the 20-run batch), which is the cheap consistency check for the logging. |
| Loop stays DB-free | `run_agent` returns `LoopResult.steps` and never touches the database or takes a new parameter, and `enrich.py` persists them afterwards. This kept all pre-existing loop tests unchanged and makes the "does not change behaviour" criterion true by construction (the loop never reads `steps`). The cost is that a hard process kill loses that run's steps along with its stats, so live per-worker progress in Phase 5 needs an `on_step` callback. |
| Logged text and untrusted content | `response_text` is capped at 1,000 characters plus a marker, and tool inputs, thinking blocks and URLs are not recorded at all (A27). The UI renders logged text only through `st.code` or dataframes, never markdown, and run labels never include it, so scraped content that reaches the log cannot become markup. |
| `response_text` is empty in practice | All 64 logged live steps had empty text: at `low` effort the model returns no text blocks alongside tool calls. The truncation path has only test evidence, and a Phase 4 judge cannot rely on this field. Diagnosis rests on `tool_name` sequences, `tool_errors` and `stop_reason`. |
| Historic rows and the currency switch | The 40 oldest `enrich` rows store `cost_gbp` and `usd_to_gbp` and no `cost_usd`, so the first queries showed their cost as 0 and the totals looked too low. `_cost_usd` in `queries.py` now converts `cost_gbp / usd_to_gbp` when `cost_usd` is absent. Any change to what `stats` stores needs a reader that handles the old shape, since the dashboard reads every historic row. |
| Backfill must be idempotent and honest | `init_db` classifies old failed rows from `stats.stop`, touching only NULL categories so it can run on every start. A hand-seeded success row needs `stop: "submitted"`, otherwise a row with no stop and `status == "error"` is classed `internal_error`, and one with no stop and no error is skipped. |
| Naive vs aware datetimes | `RunLogStep.created_at` must be timezone-aware on insert (a naive value raises in SQLModel), yet `started_at` reads back naive UTC from SQLite. The loop stamps steps with `datetime.now(UTC)` for that reason, and queries group days by `started_at.date()`. |
| New column on an existing table | `failure_category` is not indexed, because `create_all` would not add an index to an existing table and the additive `ALTER TABLE` path in `init_db` only handles plain nullable columns. At about 65 runs a scan is irrelevant, but an index needs a real migration. |
| Query limits differ by chart | `enrich_runs` and `cost_trend` default to the newest 200 runs, while `failure_counts` and `daily_outcomes` cover all runs. The headline metrics and the category chart can therefore disagree once there are more than 200 runs. Harmless at 65, a trap for Phase 5. |
| Live finding: mixed tool calls break the second request | `--max-iterations 2` ended `error` twice (runs 45 and 46) with an API 400: a `web_search` tool use "without a corresponding `web_search_tool_result` block" on the second request. Iteration 1 had emitted a server-side `web_search` alongside a custom `lookup_companies_house` call. Default-cap runs 43 and 44 accepted the same pattern, so the cap or the wrap-up notice may be the trigger, which was not isolated. This is a loop bug that predates step logging, and it blocked the planned live `iteration_cap` check, which is covered by a scripted test only. |
| Observability found cost, not failure | In the 20-run batch there were 0 failures, and the data's main message was cost: one web search is about US$0.0126 of a US$0.0152 mean run, and two searches roughly double it. Across all 64 enrich rows, 53 succeeded, 7 were `invalid_output` and 1 a `refusal` (all from the 2026-10-19 prompt iteration), and 3 were `api_error`. The category counts plus a cost-per-iteration trend were enough to see this, without any model-text analysis. |
| Pool bias for the golden set | The 20 highest-scoring unenriched candidates were all name-matched "artificial intelligence" shell-style companies: 12 landed in `Other` with only 2 to 3 sourced facts. A golden set built from top scores, or only from famous startups, would misstate category accuracy, so Phase 4 should include a deliberate share of `Other` and non-startup names. |

**Key files:**

`failures.py`

| Aspect | Detail |
| --- | --- |
| Role | `FailureCategory` (a `StrEnum`) and `failure_category_for(stop)`. Pure, with no `sprout` imports, so `loop`, `enrich` and `db` can all share it without a cycle. |
| Design | The `_BY_STOP` dict is the single mapping from `Stop` to category. `submitted` returns None. A stop not in the table becomes `internal_error`. The order of enum members is also the tie-break order for `failure_counts`. |

`agents/loop.py` additions

| Aspect | Detail |
| --- | --- |
| `StepRecord` | One per model response: iteration, timestamp, `stop_reason`, tool names, `tool_errors`, `Usage`, `cost_usd`, truncated `response_text`. `tool_errors` is incremented when a tool outcome is an error, after the record is created. |
| `LoopResult` | Gains `steps` and `error_type` (the exception class name, or `unexpected_stop_reason`), set through the `finish` helper. |
| Constraint | The loop must stay DB-free and must not read `steps`. Anything that wants live step output needs an optional callback, not a database handle. |

`agents/enrich.py` additions

| Aspect | Detail |
| --- | --- |
| Wiring | `_stats` gains `error_type`. `enrich_with_result` sets `failure_category`, adds `_step_row` rows and commits before the existing `EnrichmentFailed` raise. |
| Later phases | Phase 5 workers will each need the same ordering (commit steps, then raise). Do not move the step insert after the raise. |

`db.py` additions

| Aspect | Detail |
| --- | --- |
| Schema | `RunLog.failure_category` and the `RunLogStep` table, with a unique constraint on `(run_log_id, iteration)`. |
| `init_db` | Runs `_add_missing_columns`, then `_backfill_failure_categories`, so an existing `data/sprout.db` upgrades in place. |
| `logged_run` | After rollback, sets `internal_error` if the category is still NULL, so an escaped exception is never recorded as a success. |

`app/queries.py` additions

| Aspect | Detail |
| --- | --- |
| Functions | `enrich_runs`, `failure_counts`, `cost_trend`, `daily_outcomes`, `run_steps`, returning small dataclasses (`EnrichRun`, `TrendPoint`, `DayOutcome`) rather than ORM rows. Streamlit-free, so Phase 6 can reuse them. |
| Details | `enrich_runs` with both a category and `failures_only=True` applies only the category filter. `cost_per_iteration` is None when iterations is 0. The file moves to `sprout/queries.py` before Phase 7. |

`app/dashboard.py` additions

| Aspect | Detail |
| --- | --- |
| Tab | `render_observability` sits after News and before the gated Run & test tab. Tabs are now `(name, renderer)` pairs zipped with `strict=True`, so a name and renderer cannot drift apart. |
| Content | Four metrics, failures by category (chart and table), cost per iteration and failures per day, a filterable runs table, and a per-run step drill-down. Runs from before this phase have no steps, and the tab says so. |
