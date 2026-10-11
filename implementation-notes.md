# Sprout — Implementation Notes

Running log of what was built, why, and what later phases need to know. Newest phase last.

## Phase 0 — Setup & definitions

**Status: done except two items that need your credentials** (see "Open items").

### What was built
| Item | Where |
|---|---|
| uv project, Python 3.12 (`requires-python >=3.12`, `.python-version` = 3.12) | [pyproject.toml](pyproject.toml) |
| Runtime deps: `anthropic`, `pydantic`, `sqlmodel`, `httpx`, `feedparser`, `mcp`, `streamlit`, `python-dotenv` · dev: `pytest`, `ruff` | [pyproject.toml](pyproject.toml), `uv.lock` |
| Settings loader (keys, model IDs) | [src/sprout/config.py](src/sprout/config.py) |
| Taxonomy, stage enum, `Sourced[T]`, `CompanyProfile` | [src/sprout/models.py](src/sprout/models.py) |
| "hello" smoke call to Claude | [src/sprout/hello.py](src/sprout/hello.py) — `uv run python -m sprout.hello` |
| Tests (valid + invalid examples) | [tests/](tests/) |
| Lint/test config, CI | `[tool.ruff]`/`[tool.pytest]` in pyproject, [.github/workflows/ci.yml](.github/workflows/ci.yml) |
| Secrets hygiene | `.env` and `*.db` in [.gitignore](.gitignore); template in [.env.example](.env.example) |

### Acceptance criteria
- [x] `uv run pytest` and `uv run ruff check` pass — 24 passed, 1 skipped. Also verified on a copy of the project without `.venv`/`.git`/`.env` (`uv sync --locked` → ruff → pytest). A true `git clone` check will happen once CI runs.
- [ ] Claude "hello" call — **not verified**: no `ANTHROPIC_API_KEY` in this environment, and the `ant` CLI isn't installed. `python -m sprout.hello` fails with a clear `MissingConfigError` until `.env` exists. The live test in `tests/test_live_api.py` is skipped without a key and will run automatically once there is one.
- [x] Taxonomy and schemas in code with unit tests ([tests/test_models.py](tests/test_models.py)).

### Open items (need you)
1. **Anthropic API key**: create one, set a monthly spend limit in the console *before* the first paid call, `cp .env.example .env`, fill in `ANTHROPIC_API_KEY`, then run `uv run python -m sprout.hello` and `uv run pytest -m live`.
2. **Companies House API key**: register at developer.company-information.service.gov.uk and put it in `COMPANIES_HOUSE_API_KEY` (needed from Phase 1).
3. **Git**: `uv init` ran `git init` on branch `main`; there are no commits yet (nothing was committed). `.gitignore` is in place, so `git add .` will not pick up `.env`.

### Decisions locked in
- **Taxonomy (16, frozen):** AI models and applications, Fintech and payments, Enterprise software and cloud, Cybersecurity, Biotech and pharma, Healthtech and digital health, Electric vehicles and batteries, Climate, energy and cleantech, Semiconductors and AI hardware, Robotics and automation, Defense, aerospace and space, E-commerce and retail tech, Consumer social, media and gaming, Logistics and supply chain, Deep tech and quantum, Other. Changing it later invalidates classifications and the golden set. Tests assert the exact 16 labels and contain `Other`.
- **Stage enum:** Pre-seed, Seed, Series A, Series B, Series C+, Growth, Unknown.
- **`Sourced[T]`** = `value`, `source_url`, `retrieved_at`, `confidence` (low/medium/high), plus an optional `note` (added for Phase 2's "funding in GBP with original currency noted", e.g. `"US$12m converted"`).
  - `value=None` means "not disclosed" and is always valid.
  - **A non-null value without `source_url` fails validation.** This enforces "provenance over completeness" in code, not only in the prompt.
  - `source_url` must be `http(s)`; `retrieved_at` must be timezone-aware; unknown fields are rejected (`extra="forbid"` on every model).
- **Every `Sourced` field defaults to "not disclosed"** (via `default_factory`, so profiles never share an instance). An empty `CompanyProfile(name=...)` is valid and states nothing it cannot back up.
- **`category` is a plain `Category`, not `Sourced`.** It is a classification against our own taxonomy, not a fact found at a URL. Defaults to `Other`. If you'd rather have a per-profile rationale/confidence, add it in Phase 3 with the classifier.
- **Money is whole GBP as `int` (`>= 0`)**, not float, to avoid rounding artefacts; original-currency detail goes in `note`.
- **`last_round`** is `Sourced[FundingRound]` (stage, amount_gbp, announced date, investors): one source covers the whole round. `founded` is bounded to 1800–2100.
- **Models:** `claude-haiku-5-5` (bulk) and `claude-sonnet-5-5` (strong), overridable via `SPROUT_BULK_MODEL` / `SPROUT_STRONG_MODEL`. IDs are taken from the `claude-api` skill's model table (dated 2026-10-06); re-check before Phase 2.
- **Deviation from the plan's layout:** `costs.py` and `db.py` are not created yet (they belong to Phases 1–2). No console-script entry point (`uv init` adds one; removed as unused).

### Things later phases must know
- **`anthropic` is now 1.x, built on `httpx2`, not `httpx`.** Don't pass a plain `httpx.Client` to the SDK; use `DefaultHttpxClient`. Our own code (Companies House, fetcher) can keep using `httpx` — they are separate libraries.
- **Phase 2 (`submit_profile`):** the plan's "final tool whose input schema is the Pydantic model" still works, but on Claude Sonnet 5.5 / Opus 5.5 / Fable 5.1 *forced* `tool_choice` (`any`/`tool`) returns a 400. Use `tool_choice: auto` + a prompt instruction naming the tool, with `strict: true` on the tool, or `output_config.format` structured outputs. Haiku 5.5 should be checked against `shared/model-migration.md` in the skill.
- **Thinking tokens count against `max_tokens`** on the 5.x models (adaptive thinking is on by default; effort defaults to `medium` on Haiku 5.5). `hello.py` uses `effort: low` and `max_tokens=1024` for that reason. Set effort explicitly per agent; budget caps in Phase 2 must count thinking tokens.
- **Server-side web tools** are now `web_search_20260209` / `web_fetch_20260209` on Sonnet 5.5; older type strings in the plan's era are stale. Don't declare `code_execution` alongside them.
- **Pydantic generics:** `Sourced[T]` uses PEP 695 syntax; instantiate as `Sourced[int](...)`. Bare `Sourced(...)` has an unparameterised `T` and won't validate the value type. Database storage (Phase 1) will need to flatten `Sourced` into `fact` rows (value serialised as JSON).
- **Ruff rules:** `E, F, I, UP, B`, line length 100. CI runs `ruff check` and `pytest` only; `ruff format` is applied locally but not enforced.
- **CI action versions** (`actions/checkout@v4`, `astral-sh/setup-uv@v6`) were written from memory and haven't been run yet; confirm the first run is green.
