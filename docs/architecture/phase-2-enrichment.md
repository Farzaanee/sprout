# Phase 2: Single-company enrichment agent

Status: done, with caveats. Part of the [Sprout architecture docs](README.md). Synced against the code on 2026-10-19.

```mermaid
flowchart TB
    CLI["python -m sprout.enrich (shim): resolve_company maps a name or company number to a company row"]
    subgraph AGENT["agents/ (anthropic is imported only by loop.py)"]
        ENR["enrich.py:<br/>system prompt, submit_profile schema and validation,<br/>enrich_company, enrich_with_result,<br/>CLI main, run_log stats"]
        LOOP["loop.py: run_agent, stop-reason handling, iteration and USD guards, one repair"]
        TOOLS["tools.py:<br/>build_tools, tool handlers,<br/>SourceLedger, untrusted-content wrapping"]
    end
    subgraph TOOLSET["Tools offered to the model"]
        T1["web_search:<br/>Anthropic server-side,<br/>executed and billed by Anthropic"]
        T2["fetch_page: custom, wraps WebFetcher.fetch"]
        T3["lookup_companies_house:<br/>custom, wraps get_company<br/>and filing_history"]
        T4["submit_profile:<br/>custom, terminal, not strict,<br/>schema derived from CompanyProfile"]
    end
    LLM(["Anthropic API"])
    COST["costs.py: price table, Usage, cost_usd"]
    CHECK["validate_submission:<br/>Pydantic, source URL in ledger,<br/>funding note present"]
    DBW["db.py:<br/>save_profile (one save_fact per Sourced field,<br/>funding_rounds to its own table, company.category)<br/>add_company_by_number<br/>logged_run kind enrich"]
    SQL[("SQLite: company, fact, run_log")]
    WF["sources/web.py: WebFetcher"]
    CHC["sources/companies_house.py"]
    SRCX(["Public websites, Companies House"])
    T1 ~~~ T2
    T2 ~~~ T3
    T3 ~~~ T4
    WF ~~~ CHC
    CLI --> ENR
    ENR --> LOOP
    ENR --> TOOLS
    LOOP <--> LLM
    LOOP --> TOOLS
    TOOLS --> T2
    TOOLS --> T3
    TOOLS --> T4
    LLM -. "server-side search, results returned in the same response" .-> T1
    T2 --> WF
    T3 --> CHC
    WF --> SRCX
    CHC --> SRCX
    T4 --> CHECK
    CHECK -- "invalid: error fed back, one repair" --> LOOP
    CHECK -- "valid" --> ENR
    ENR --> DBW
    DBW --> SQL
    LOOP --> COST
    ENR --> COST
    LOOP -- "on_search_urls callback" --> ENR
```

## Design notes

- **Search backend (resolves A1 and D13 for Phase 2, built as designed): Anthropic server-side `web_search_20260209` (`max_uses` 3); custom `fetch_page` and `lookup_companies_house`; no server-side `web_fetch`.** Server-side `web_fetch` would fetch pages from Anthropic's side and bypass the robots, SSRF, size and cache protections of `WebFetcher`. Server-side `web_search` only returns result URLs and snippets; any page the agent then reads goes through our `fetch_page`, so the protections still apply. It also needed no new key, dependency or adapter. The SDK also ships `web_search_20260318`, not documented in the `claude-api` skill and not adopted.
- Options compared (verdicts updated with the Phase 2 outcome):

| Option | For | Against | Verdict |
|---|---|---|---|
| Server-side `web_search` (type string `web_search_20260209`, confirmed by live calls) | No extra key, adapter or tests; results are in the same response; `max_uses` is a hard cap | Billed per search on top of tokens (USD 10 per 1,000 is assumed from the `claude-api` skill and not yet checked against an invoice); results come back in blocks we do not wrap; adds `server_tool_use` and `pause_turn` handling; results arrive encrypted so snippet-only claims cannot be audited by us; not cacheable by us | Built. Measured average USD 0.0138 per company against the USD 0.07 target; search is about 70 percent of that |
| Custom search tool (Brave or Tavily) | Our own cache and rate limit; results wrapped as untrusted; cost may be lower | New key, adapter, tests and a pricing check | Not needed: cost is far below target. Reconsider only if auditability of snippets matters more than the extra adapter |
| Server-side `web_fetch` | Zero code | Bypasses `WebFetcher` protections | Rejected |

- **Swap point:** the tool list is built in one function, `build_tools(fetcher, ch, ledger)` in `agents/tools.py`; replacing the search entry with a custom handler touches that function, the price table and nothing else. The lever for lowering cost is `WEB_SEARCH_MAX_USES` (or caching search results), not the model.
- **Server-side tool mechanics the loop handles:** the response may contain `server_tool_use` and `web_search_tool_result` blocks, which are appended to the history unchanged (they carry encrypted content needed on the next turn). `stop_reason: "pause_turn"` appends the assistant content and calls again without a user message, and counts as an iteration (covered by a scripted-client test only; not seen in the live runs recorded in the notes). Search errors (`max_uses_exceeded`, `unavailable`, and similar) arrive as result blocks, not exceptions; one live Tractable run had search unavailable and the model submitted honest nulls. `usage.server_tool_use.web_search_requests` is added to cost. URLs in search results reach the seen-URL ledger through the `on_search_urls` callback passed to `run_agent` (the loop stays generic; `enrich.py` supplies the callback).
- **Model facts carried into Phase 2 (verified live on Haiku 5.5):** bulk `claude-haiku-5-5` is the default enrichment model, `claude-sonnet-5-5` is selectable (`--strong`, not tested live). The loop sends `tool_choice: {"type": "auto"}`, and the system prompt names `submit_profile` as the required final step. Forced `tool_choice` was reported to return 400 and was not re-tested. `submit_profile` is **not** `strict`: the API rejected the derived schema ("29 parameters with type arrays or anyOf, limit 16"), so Pydantic validation plus one repair attempt is the enforcement (the fallback the design allowed; see D18 in the [overview](overview.md)). Thinking tokens are billed as output, so they are inside `usage.output_tokens` and the USD budget. Effort is passed as `output_config={"effort": ...}`, default `low` (CLI `--effort low|medium|high`).
- **Prompt caching (confirmed live):** the system block carries `cache_control: ephemeral` and so does a top-level `cache_control` on `messages.create`, which caches the growing conversation so each iteration re-reads earlier turns at the read price. Across the live runs about 455k tokens were written and 1.50M read, so `cache_creation_input_tokens > 0` holds. The tool list order is fixed because tool definitions sit at the start of the cached prefix.
- **Prompt injection:** every custom tool result (page text, Companies House data) is wrapped in `<untrusted_content source="URL">...</untrusted_content>`, any opening or closing delimiter inside the text is neutralised before wrapping, results are truncated to 8,000 characters with a `[truncated]` marker, and the system prompt states that anything inside is data and never instructions. Search snippets arrive outside our wrapping, so the same system-prompt rule covers them. No tool the model can call writes anything; DB writes happen in our code after validation. Residual risk: `fetch_page` takes a model-chosen URL, so injected text could try to leak context through a query string. There are no secrets in context; the allow/deny list and injection fixtures are Phase 4 (design on the [Phase 4 page](phase-4-evals-guardrails.md), not built).
- **Provenance enforced in code, beyond Pydantic (D16, built in `validate_submission`):** `retrieved_at` is not model output. The submit schema omits it and our code stamps it from the ledger (time the URL was fetched or seen in search). A `source_url` must be a URL the tools actually returned; matching ignores tracking params, fragments, host case and a trailing slash (a plain `https://x.com` versus `https://x.com/` mismatch caused false rejections before this was fixed). A URL the model produced from memory is rejected and fed back as a repair whose message lists up to 25 citable URLs. A funding fact (`total_raised_million_usd`, `valuation_million_usd`, `funding_rounds`) must carry a `note` with the original figure and currency. A source seen only in a search snippet and never fetched caps confidence at `medium`. The check applies to non-null values only; a null value with no source always passes.
- **[AMBIGUOUS], resolved:** `submit_profile`'s schema is generated from `CompanyProfile` (`submit_profile_schema()`): `$ref`s are inlined, `name`, `news` and `retrieved_at` are removed, all remaining properties are made required, and the keywords `title`, `default`, `minimum`, `maximum`, `minLength`, `format` and `description` are dropped, so numeric bounds on `founded` and `amount_million_usd` are enforced by Pydantic only. Because strict mode refused the schema anyway, nothing relies on the API enforcing it.
- **Caveats on the pilot:** the 10-company result (10/10 `submitted`, average USD 0.0138) is the final pass; the first pass was 6/10 and the review of the profiles was done by the implementing agent, not the user. Non-determinism remains: an occasional run ends `invalid` or `refused`, and the model sometimes leaves funding amounts null for non-USD figures despite the prompt (safe but understates funding coverage).

## Built interfaces (signatures as in the code)

```python
# costs.py - pure: no I/O, no imports from sprout
@dataclass(frozen=True)
class Price: input: Decimal; output: Decimal; cache_write: Decimal; cache_read: Decimal  # USD per million tokens
PRICES: dict[str, Price]       # keyed by exact model ID (haiku-5-5, sonnet-5-5); WEB_SEARCH_USD_EACH = Decimal("0.01")
@dataclass
class Usage:                   # supports +, accumulated per run
    input_tokens: int; output_tokens: int; cache_read_tokens: int; cache_write_tokens: int; web_searches: int
def cost_usd(model: str, usage: Usage) -> Decimal   # raises UnknownModelError for an unpriced model

# agents/loop.py - the only pipeline module importing anthropic
class MessagesClient(Protocol):            # what the loop needs; the test seam
    messages: Any                          # .create(**kwargs) -> response with .content, .stop_reason, .usage
def make_client(api_key: str) -> MessagesClient       # real anthropic.Anthropic, 120 s timeout, SDK retries on
@dataclass(frozen=True)
class Budget: max_iterations: int; max_usd: Decimal; max_repairs: int = 1   # validated: iterations >= 1, usd > 0
@dataclass(frozen=True)
class Tool:
    spec: dict                             # sent to the API (custom tool or server-side tool type)
    handler: Callable[[dict], ToolOutcome] | None = None   # None for server-side tools
    terminal: bool = False                 # submit_profile
@dataclass(frozen=True)
class ToolOutcome: text: str; is_error: bool = False; payload: Any = None   # payload set => accepted submission
@dataclass
class LoopResult:
    payload: Any | None; usage: Usage; cost_usd: Decimal; iterations: int; repairs: int; tool_calls: dict[str, int]
    stop: Literal["submitted","invalid","max_iter","budget","no_submit","truncated","refused","error"]
    error: str | None
def run_agent(client, *, model, system, messages, tools, budget, max_tokens, effort,
              on_search_urls: Callable[[list[str]], None] | None = None) -> LoopResult

# agents/tools.py - plain dicts and handlers; no SDK import
def build_tools(fetcher: WebFetcher, ch: CompaniesHouseClient | None, ledger: SourceLedger) -> list[Tool]
    # web_search (server-side, max_uses 3), fetch_page, lookup_companies_house (only if ch is not None); submit_profile is added by enrich.py
class SourceLedger:                               # URLs the tools returned, with first-seen time and fetched-or-snippet
    def add(url, *, fetched: bool, at: datetime | None = None); def get(url) -> LedgerEntry | None; def urls() -> list[str]
def wrap_untrusted(source_url: str, text: str, max_chars: int = 8_000) -> str    # delimiters, neutralise tags, truncate

# agents/enrich.py
DEFAULT_BUDGET = Budget(max_iterations=12, max_usd=Decimal("0.13"))   # kept after the live run; MAX_OUTPUT_TOKENS = 6_000
@dataclass(frozen=True)
class Deps: client; fetcher; ch; settings; model: str; effort: str = "low"
def enrich_company(session, company, deps, budget=DEFAULT_BUDGET) -> CompanyProfile   # raises EnrichmentFailed(stop, cost)
def enrich_with_result(session, company, deps, budget=DEFAULT_BUDGET) -> tuple[CompanyProfile, LoopResult]
def main(argv) -> int        # python -m sprout.enrich NAME_OR_NUMBER...  --strong  --max-usd  --max-iterations  --effort  --show

# db.py additions
def save_profile(session, company, profile: CompanyProfile) -> None     # save_fact per Sourced field; company.category
def add_company_by_number(session, number: str, data: dict) -> Company   # for pilot companies absent from the 876
```

**[DRIFT]** against the earlier design of this section: `build_tools(fetcher, ch, ledger)` replaces `build_tools(deps, ledger)` (so `tools.py` does not import `Deps` from `enrich.py`); `Deps` gained `model` and `effort`; `run_agent` gained `on_search_urls` and `LoopResult` gained `repairs`; `enrich_with_result` was added because `enrich_company` returns only the profile and the CLI needs cost and iterations; `cost_usd` is exposed; `SourceLedger.add` takes an optional `at` so a fetched page keeps the fetcher's `retrieved_at`; the CLI gained `--effort` and `--show` and a Companies House name search for "did you mean" hints. `Deps.ch` is `CompaniesHouseClient | None` and is dropped for a company with no number (`lookup_companies_house` is then absent from the tool list).

## Loop contract (stop-reason handling; built as designed, covered by scripted-client tests)

Each iteration: check the guards, call `messages.create`, add `usage` to the running total, then act on `stop_reason`.

| Condition | Action |
|---|---|
| `tool_use` | Run each custom `tool_use` block; return all results in one user message (`tool_result`, `is_error` for failures). Handler exceptions become error strings, never raised. A terminal tool that returns a payload ends the run (`submitted`). A terminal tool that returns `is_error` is the repair path: one allowed (`max_repairs`), a second failure ends the run (`invalid`). |
| `pause_turn` | Append the assistant content unchanged and call again; counts as an iteration. |
| `end_turn` without a submission (also `tool_use` with no `tool_use` block) | One reminder to call `submit_profile`, otherwise `no_submit`. Any other unknown stop reason gives `error`. |
| `max_tokens` | `truncated` (a cut-off `tool_use` input is not trusted). |
| `refusal` | `refused`. |
| SDK `APIError` after the SDK's own retries | `error`; usage so far is still returned. |
| Guards | Checked before every call: iterations used >= `max_iterations` gives `max_iter`; spent >= `max_usd` gives `budget`. On the last allowed iteration, or once spend reaches 80 percent of the cap, the next user message adds "budget nearly used, call submit_profile now". The USD cap is soft by at most one response (cost is known only after a call); `max_tokens` bounds that overshoot, and tests assert the exact call count with a tiny cap. |

Only a validated `submit_profile` carries facts, so on any non-`submitted` stop nothing is written to `fact` or `category`; the cost is still recorded. The only per-run caps are iterations, USD, search `max_uses` (3) and the tool-result character cap.

**[DRIFT]** Earlier design text also promised a per-run fetch cap and "partial" facts on a cap stop. Neither exists: page fetches are bounded only by the iteration cap, and no partial profile is ever written. There is also no batch-level USD cap: the CLI enforces `--max-usd` per company and only prints the total, so a long target list can spend `n * max_usd`. A `BatchBudget` is designed for Phase 4 (see the [Phase 4 page](phase-4-evals-guardrails.md), D24) so the evals have a batch cap, and the Phase 5 orchestrator must reuse it.

## Other notes

- Agents receive a client through a `Deps` dataclass; tests inject a scripted fake client (see [Testing](overview.md#44-testing) in the overview).
- Tool handlers return error outcomes and never raise into the loop (`run_agent` also catches any handler exception and turns it into an error result).
- `enrich_with_result` writes facts and the `enrich` run_log row in one session: it assigns `run.stats` and **commits before raising** on failure, because `logged_run` rolls back on exceptions and would otherwise discard the stats that record what a failed run cost; on success it then calls `save_profile`. On failure `logged_run` also marks the row `status="error"` with the exception text, while the stats stay on the row. Note the status therefore means "run did not produce a profile", and `stats.stop` says why.
- Move `app/queries.py` to `sprout/queries.py` (keep a re-export) before Phase 7 so MCP does not import from the UI package.

Planned Phase 5/7 modules that build on this phase are on the [Phase 5 page](phase-5-multi-agent.md).
