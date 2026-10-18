# Sprout: Learnings

Skills and lessons picked up phase by phase. Planned learnings come from `docs/project-plan.md`; where a phase is actually done (see `docs/implementation-notes.md`), its planned learnings are followed by what was actually learned while building it.

## Phase 0 - Setup & definitions

**Planned:** modern Python packaging (`uv`, `pyproject.toml`), secrets handling, Pydantic data modelling, designing a schema before building agents.

**Actual (done):**
- `uv` needs Python 3.12 set explicitly (`requires-python`, `.python-version`); macOS ships 3.9, so everything must run through `uv run` or the wrong interpreter gets picked up.
- Enforcing "provenance over completeness" in the schema itself, not just the prompt, works: `Sourced[T]` rejects a non-null value without a `source_url`, so a model can't silently skip citing a fact.
- PEP 695 generics (`Sourced[T]`) must be instantiated parametrised (`Sourced[int](...)`); bare `Sourced(...)` leaves `T` unchecked.
- `anthropic` 1.x is built on `httpx2`, not `httpx`; a plain `httpx.Client` can't be passed to the SDK.
- On the 5.x models, forced `tool_choice` (`any`/`tool`) 400s; thinking tokens also count against `max_tokens`, so budgets must account for them.
- Locking the taxonomy and schema before any agent code avoids churn: changing either later invalidates classifications and the golden set.
- **Pydantic data modelling:**

| Topic | Key learning |
| --- | --- |
| `BaseModel` vs plain class/`dataclass` | `BaseModel` validates and coerces types at the point data enters the system and raises `ValidationError` on mismatch. A plain class does none of this. `dataclass` gives `__init__`/`__eq__`/`__repr__` for free but still ignores type hints at runtime, bad data passes through silently until it breaks something downstream. |
| `Field()` | Attaches per-field constraints and metadata on top of a type annotation: `min_length`/`max_length`, `ge`/`le`/`gt`/`lt`, `pattern`, `alias`, `description`. Complements, doesn't replace, the type-level contract the annotation already gives. |
| `_Strict(BaseModel)` + `ConfigDict(extra="forbid")` | A shared internal base class that rejects any field not explicitly declared on the model, instead of silently ignoring (`"ignore"`, the default) or accepting (`"allow"`) it. Defined once, inherited everywhere, so malformed/hallucinated LLM output fails loudly rather than passing through unnoticed. |
| `default_factory` | Required, not just style, for mutable defaults (`list`, `dict`, `set`): Pydantic v2 rejects a bare mutable default outright because Python evaluates default values once at definition time, so every instance would otherwise share and mutate the same object. Also fits any default that must be computed per instance rather than once at class definition (e.g. `uuid.uuid4()`, `datetime.now()`). Mutually exclusive with `default`, passing both raises an error. |
| Typed fields (`AnyHttpUrl`, `AwareDatetime`) | Push format and timezone validation into the schema itself: `AnyHttpUrl` rejects malformed URLs, `AwareDatetime` rejects naive (timezone-less) datetimes. Moves a class of bugs from "caught later, maybe" to "caught at the model boundary, always." |
| Class inheritance refresher | `class _Strict(BaseModel):` makes `_Strict` a subclass of `BaseModel`; it inherits all of `BaseModel`'s behaviour and overrides only `model_config`. Other models then inherit from `_Strict` instead of `BaseModel` directly, picking up the strictness rule without repeating `ConfigDict(extra="forbid")` on every model (DRY). |
| Why `value: T \| None` on `Sourced[T]` | `None` means a genuinely absent fact, not a design gap: a company with no funding round yet, or a field the agent researched and found no sourced claim for. Every `CompanyProfile` field is a `Sourced[T]`, so if `value` were required, each field would need wrapping in `Optional[Sourced[T]]` instead, doubling the optionality for the same concept. The validator only forbids the one invalid combination, a value with no `source_url`, leaving "no claim at all" (`value=None`) as a legitimate state. |

## Phase 1 - Data foundations

**Planned:** REST API consumption, HTTP resilience (retries, backoff, rate limits, caching), idempotent ingestion, ORM/SQL modelling, web-scraping etiquette and its legal/ethical limits.

**Actual (done):**
- Companies House has separate sandbox and live environments; a key issued for a "test" application only works against the sandbox and returns no real companies, even though the error (401) looks like a bad key.
- SIC codes say little about AI: most AI startups register under generic software codes, so a name-based keyword filter (tiered strong/weak keywords, weak ones needing a tech SIC code too) is needed to keep false positives down, and the result is still a noisy candidate pool, not a verified list.
- `/advanced-search/companies` returns SIC codes in results where plain `/search/companies` does not, which matters when SIC is part of the filter.
- One HTTP entry point with per-call cache TTLs, a sliding-window rate limiter, and retry-with-backoff makes ingestion idempotent for free: a second run makes zero HTTP calls and adds zero rows.
- In-place JSON mutation on SQLModel columns isn't tracked by SQLAlchemy; a whole new dict/list must be assigned to trigger a write.
- Timezone-naive datetimes are rejected outright by this SQLModel version, so "UTC everywhere" has to be enforced from the first write.
- Web-scraping etiquette is a security boundary as much as a courtesy: resolving every host (and every redirect hop) to a public IP blocks SSRF via DNS rebinding to loopback/private/metadata addresses, on top of honouring `robots.txt`.

## Streamlit progress & test console (ahead of Phase 6)

Not a plan phase, but built early to check progress. **Actual learnings:**
- Streamlit Community Cloud has no persistent disk and the database is gitignored, so a deployed copy always starts empty; a bundled snapshot DB is unavoidable for a populated public demo.
- Gating state-changing UI (an "ingest now" button) behind an environment variable keeps a public deployment read-only by default without separate code paths.
- Driving a "progress" view off the shape of the docs themselves (status/checkbox conventions in this file) is cheap but brittle: the convention has to be kept consistent or the view misreports.

## Phase 2 - First agent: single-company enrichment

**Planned:** how tool use actually works (the loop, stop reasons, tool results), structured output via tool schemas, prompt design for grounded extraction, prompt caching, token/cost accounting, error-feedback repair loops.

**Status:** not started yet; no actual learnings to add.

## Phase 3 - Multi-agent pipeline at scale

**Planned:** multi-agent orchestration patterns (supervisor/worker, specialisation), model routing for cost, concurrency with `asyncio`, resumable pipelines, verification/cross-checking patterns, failure isolation.

**Status:** not started yet; no actual learnings to add.

## Phase 4 - MCP server

**Planned:** the Model Context Protocol (tools vs resources vs prompts, transports, capability negotiation), writing tool descriptions that LLMs use well, MCP client + server roles, separating read-only from side-effecting tools.

**Status:** not started yet; no actual learnings to add.

## Phase 5 - Evals & guardrails

**Planned:** evaluating LLM systems (golden sets, metrics, LLM-as-judge and its limits), regression testing prompts, prompt-injection and SSRF defences, treating model output as untrusted too.

**Status:** not started yet; no actual learnings to add.

## Phase 6 - Dashboard

**Planned:** data-app development with Streamlit (state, caching), presenting uncertain data honestly, reusing a single service layer across UI and MCP.

**Status:** not started yet (beyond the early progress console above); no actual learnings to add.

## Phase 7 - Automation, deployment & documentation

**Planned:** CI/CD with GitHub Actions, secrets in CI, containerisation, deployment of data apps, technical writing and documenting trade-offs.

**Status:** not started yet; no actual learnings to add.
