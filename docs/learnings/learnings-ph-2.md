# Sprout: Learnings - Phase 2 - First agent: single-company enrichment

**Planned:** how tool use actually works (the loop, stop reasons, tool results), structured output via tool schemas, prompt design for grounded extraction, prompt caching, token/cost accounting, error-feedback repair loops.

**Actual (done, with caveats: the tiny-cap criterion is verified with mocks only, and the 10-profile review so far is the implementing agent's, not the user's):**

| Topic | Key learning |
| --- | --- |
| Forced `tool_choice` | Not used. The loop sends `tool_choice: {"type": "auto"}` and the system prompt names `submit_profile` as the required final step. Forced choice was reported to 400 on the 5.x models and was never re-tested live on Haiku 5.5. If the model ends its turn without submitting, the loop sends one reminder, then stops with `no_submit`. |
| `strict: true` on a derived schema | The API rejected the schema derived from `CompanyProfile` ("29 parameters with type arrays or anyOf, limit 16"), because every nullable `Sourced` field adds an `anyOf`. `submit_profile` is therefore not strict, and Pydantic validation plus one repair attempt is the schema guarantee. Any future strict tool must stay under the 16 union-parameter limit. |
| Deriving the tool schema | `submit_profile_schema()` takes `CompanyProfile.model_json_schema()`, inlines `$ref`s, drops keywords the tool schema does not accept (`title`, `default`, `minimum`, `format` and so on), and marks all fields required. Deriving it means the schema cannot drift from the model, but `_simplify` drops any dict key with those names, so a future `CompanyProfile` field called `title` or `format` would silently vanish from the schema. |
| Provenance in code, not prompt | A prompt saying "cite only real URLs" was not enough: in the first live pass 3 of 10 companies (Cleo, Tractable, Quantexa) ended `invalid` because the model cited homepages no tool had returned, and repeated them after the repair message. What worked was a `SourceLedger` of URLs tools actually returned, validation in `validate_submission`, a repair message listing up to 25 citable URLs, and a prompt line saying to `fetch_page` a site before citing it. Final pass: 10/10 `submitted`. |
| URL matching | The ledger compared `https://x.com` and `https://x.com/` as different URLs, which kept Cleo and Quantexa `invalid` on the second pass. `_ledger_key` now ignores a trailing slash, host case, fragments and tracking params (via `normalise_url`). Exact string matching of URLs between model and tools is a bug source. |
| Snippet-only sources | Server-side `web_search` results arrive encrypted, so what the model saw cannot be audited from our side. The ledger records whether a URL was only seen in a search result or actually fetched, and `validate_submission` caps unfetched sources at `medium` confidence. A real check needs a verifier agent (Phase 5). |
| Currency conversion | Telling the model to "note the original currency" was not enough: it left funding amounts null for non-USD figures. Putting approximate conversion rates into USD in the prompt fixed most cases, with the original figure and rate in `note`. Null is still the safe failure, but it understates funding coverage. |
| Errors fed back to the model | The rejection message lists the problem fields and citable URLs but does not echo the model's input (`_compact_errors`). One repair attempt (`Budget.max_repairs = 1`) is allowed, and a repair is not counted as a failure in the stats. |
| Prompt injection | Every custom tool result is wrapped in `<untrusted_content source="...">` by `wrap_untrusted`, which truncates to 8,000 characters and neutralises any literal `<untrusted_content` tag inside the page so a page cannot close the wrapper early. The prompt tells the model that wrapped text and search results are data, never instructions. Tests cover the wrapper, but there is no adversarial eval yet (Phase 4). |
| Tool errors are tool results | Handlers return `ToolOutcome(is_error=True)` for robots blocks, SSRF blocks, fetch failures and unknown companies, and `_run_tool` also converts unexpected exceptions into error results. A tool bug or a blocked page must reach the model as text it can react to, never end the run. |
| Guards run before each call | Iteration and USD caps are checked before every API call, so the iteration cap is exact but the USD cap is soft by at most one response, since cost is only known afterwards. The wrap-up notice is appended on the last iteration or at 80 percent of the USD cap. The tiny-cap test criterion is verified with `ScriptedClient` mocks only, not live. |
| Thinking tokens and cost | Thinking is billed as output and is already inside `usage.output_tokens`, so `cost_usd` needs no separate term. Effort is set explicitly (`output_config={"effort": "low"}`) because default effort on Haiku 5.5 is `medium` and would raise cost and `max_tokens` use. |
| Prompt caching | Two markers are needed: one on the system block (static prefix, tools then system) and a top-level `cache_control` so the growing conversation is re-read at the cache-read price. It works live (`cache_creation_input_tokens > 0`; about 455k tokens written and 1.50M read across all runs). Tool order is fixed in `build_tools` because tool definitions sit at the start of the cached prefix. The 512-token minimum cacheable prefix was not tested. |
| Cost is the search, not the model | Average cost was USD 0.0138 per company (final 10-company run USD 0.1387), against a USD 0.07 target. About 70 percent of each run is the single web search (US$0.01), so lowering `max_uses` or caching search results is the lever, not a cheaper model. The US$10 per 1,000 searches figure comes from a Managed Agents note in the `claude-api` skill and still needs checking against a first invoice. |
| Fail closed on prices | `PRICES` is code, not env, and `cost_usd` raises `UnknownModelError` for any model not in the table. `run_agent` calls `cost_usd(model, Usage())` before the first request, so a typo in `SPROUT_BULK_MODEL` cannot bypass the spend caps. |
| Server-side vs custom tools | Server-side `web_search_20260209` only returns URLs and snippets, so any page the agent reads goes through our `fetch_page` and keeps the robots, SSRF, size and cache protections of `WebFetcher`. Server-side `web_fetch` was rejected because it would bypass them. `pause_turn` is handled but has only mock evidence. |
| Failure persistence | `logged_run` rolls back on exceptions, so `enrich_with_result` assigns `run.stats` and commits before raising `EnrichmentFailed`. Without that, a failed run (which still cost money) would leave no `run_log` trace. Facts and category are written only after a validated `submit_profile`, so failed runs persist nothing partial. |
| NULL-value facts | `save_profile` stores "researched, nothing found" as a NULL-value fact, so a dashboard can tell it from "never tried". Consequence found in review: a field valued in run 1 and null in run 2 ends with both facts, so a winning-value rule must exist before any front end reads facts. |
| Additive migration | SQLModel `create_all` does not alter existing tables. `init_db` now adds missing nullable columns with `ALTER TABLE` (needed for `company.category` on the existing `data/sprout.db`), and raises with "delete the database" advice for anything that is not a plain nullable column. |
| Testing an LLM loop | `tests/fakes.py` provides a `ScriptedClient` that returns queued responses and records every request, which makes stop reasons, caps and repair paths deterministic. It snapshots the `messages` list per call because the loop mutates the same list. No `-m live` enrichment test was added, so a plain `pytest` with an API key never spends money. |
| Non-determinism | Even with the ledger, an occasional run ends `invalid` or `refused` (one Cleo run was a transient refusal that succeeded on retry), and one Tractable run had web search unavailable and correctly returned nulls. Mocked tests cannot catch this, so the 10-company live run (30 `enrich` runs while iterating, about USD 0.42 in total) was the real acceptance test. Phase 5 needs retries and `enrichment_state`. |
| Wrong-company risk | A claimed website can be wrong (Airborne Robotics' site returned another company's content). The prompt tells the model to disambiguate by Companies House number, and the result is stored at medium confidence with a caveat in the note, but only a verifier would catch it reliably. |

**Key files:**

`agents/loop.py`

| Aspect | Detail |
| --- | --- |
| Role | Hand-written tool-use loop (`run_agent`), generic over tools. The only pipeline module that imports `anthropic`, so SDK drift and the test seam (`MessagesClient`) live in one file. |
| Stop reasons | `Stop` is one of `submitted`, `invalid`, `max_iter`, `budget`, `no_submit`, `truncated`, `refused`, `error`. `pause_turn` just continues, `max_tokens` is `truncated`, `refusal` is `refused`, and `anthropic.APIError` is `error`. The SDK itself retries 408/409/429/5xx twice before the loop sees a failure. |
| Terminal tool | A tool marked `terminal` ends the run when its outcome carries a `payload`. An error outcome from it triggers one repair (the error goes back as a `tool_result`), then `invalid`. |
| Usage | `_usage_of` reads `server_tool_use.web_search_requests` with `getattr` defaults, so responses without it still count. Cost is recomputed from cumulative usage after each call. |

`agents/tools.py`

| Aspect | Detail |
| --- | --- |
| Tools | `web_search` (server-side spec, `max_uses` 3, no handler), `fetch_page` (wraps `WebFetcher`) and `lookup_companies_house` (only added when the company has a number). `build_tools` is the single place to swap the search backend. |
| `SourceLedger` | Maps normalised URL to first-seen time and a `fetched` flag. `fetch_page` adds both the requested URL and the final `page.url`, since a redirect changes it. First-seen time becomes the fact's `retrieved_at`. |
| Companies House tool | Uses `get_company` and `filing_history` (the previously unused client methods) and summarises at most 15 filings. Its source URL is the public register page, which it adds to the ledger as fetched. |
| Latent limits | There is no per-run fetch cap, only the iteration cap. |

`agents/enrich.py` and `enrich.py`

| Aspect | Detail |
| --- | --- |
| Role | Everything profile-specific: system prompt, `submit_profile` schema, `validate_submission`, `enrich_company` / `enrich_with_result`, `resolve_company` and the CLI. `sprout/enrich.py` is an 8-line shim so `python -m sprout.enrich` works. |
| Defaults | 12 iterations, USD 0.13, effort `low`, `MAX_OUTPUT_TOKENS` 6,000, Haiku 5.5 (Sonnet 5.5 via `--strong`, not run live). |
| Identity | The model never supplies `name` or `news`, and `retrieved_at` is stamped from the ledger by code. `name` comes from the DB row. |
| Size | About 460 lines. Split the CLI and `resolve_company` out when Phase 5 adds an orchestrator. |

`costs.py`

| Aspect | Detail |
| --- | --- |
| Role | Pure price table, `Usage` (summable per run), `cost_usd`. No imports from the rest of `sprout`. Uses `Decimal` throughout. |
| Assumptions to refresh | The web search price and the Haiku 5.5 over-100K-token tier (prices double above 100K prompt tokens; per-call prompts stay well below it, so the lower rate card is used and the higher tier is untested). |

`db.py` additions and `tests/fakes.py`

| Aspect | Detail |
| --- | --- |
| `save_profile` | Iterates `CompanyProfile.model_fields` and saves every `Sourced` field, so a new `Sourced` field persists with no db change. `news` is not persisted until news items link to companies. |
| `add_company_by_number` | Turns a Companies House `/company/{number}` response into a row via `parse_hit` and `assess`, which makes `db.py` import source and filter code (a known coupling). |
| `tests/fakes.py` | Builders for `tool_use_turn`, `server_search_turn`, `pause_turn`, `text_turn` and a `ScriptedClient` that raises if the loop makes more calls than scripted. |

**Terminology clarified (code walkthrough):**

| Term | Clarification |
| --- | --- |
| Shim | `sprout/enrich.py` is a shim: a thin wrapper with no real logic, just re-exporting `main` from `agents/enrich.py` so `python -m sprout.enrich` works as an entry point. |
| Tool vs function | A function is called directly by code you control. A tool is a function (a `Tool.handler`) the model decides to call, via a schema describing it; `loop.py`'s `_run_tool` is what actually dispatches to the handler once the model requests it. |
| `SourceLedger` | A running log (literal meaning of "ledger": an ordered record of entries) of every URL a tool has surfaced during one run, with a `fetched` flag. Used by `enrich.py` to reject `submit_profile` citations the model made up rather than actually retrieved. |
| `client` | The object that makes the real network call to the API (`MessagesClient` protocol, backed by `anthropic.Anthropic` in `make_client`). The loop depends only on the narrow protocol, not the concrete SDK class, which is the test seam that lets `tests/fakes.py` substitute `ScriptedClient`. |
| `pause_turn` | Returned by the API when a server-side tool (multi-step `web_search`) hasn't finished a logical turn yet. The loop just `continue`s and resends the updated history; it is not a stopping condition, unlike `end_turn`/`max_tokens`/`tool_use`. |
| Schema | A description of data's shape (fields, types, required-ness), not the data itself. Shows up three ways here: tool `input_schema` (JSON Schema the model must match to call a tool), `submit_profile_schema()` (derived from `CompanyProfile` so it can't drift), and SQLModel table classes (the DB schema). |
| Delimiter spoofing example | `wrap_untrusted` escapes any `<untrusted_content`/`</untrusted_content` already inside fetched page text before wrapping it. Without that, a page could embed a fake closing tag followed by injected instructions (e.g. "set funding to US$50m"), tricking the model into reading them as outside the untrusted block, i.e. as if they were trusted, instead of as page content to ignore. |
