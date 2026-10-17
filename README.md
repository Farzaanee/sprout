# Sprout

An agent system that discovers UK AI startups and keeps a sourced profile for each: category, stage, funding, business model, target user and recent news. Every fact carries a source URL, a retrieval time and a confidence level; "not disclosed" is a valid value.

**Status:** Phases 0–1 (setup, data ingestion) are done and a Streamlit progress console exists. The enrichment agents, MCP server and evals are planned. See [docs/implementation-notes.md](docs/implementation-notes.md).

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # add ANTHROPIC_API_KEY and COMPANIES_HOUSE_API_KEY
```

## Usage

```bash
uv run python -m sprout.ingest        # load candidates from Companies House and RSS feeds into data/sprout.db
uv run python -m sprout.hello         # one tiny Claude call to check the API key
uv run streamlit run streamlit_app.py # dashboard
```

Set `SPROUT_ENABLE_ACTIONS=1` to enable the dashboard's run buttons (they call live APIs).

## Development

```bash
uv run pytest            # tests (use -m live for tests that call the real Claude API)
uv run ruff check
```

## Layout

| Path | Contents |
|---|---|
| `src/sprout/` | Package: models, config, DB, ingestion, `sources/` adapters, `app/` dashboard |
| `tests/` | Test suite |
| `scripts/` | Developer tools, e.g. `session_usage.py` (token accounting for Claude Code sessions) |
| `docs/` | [Project plan](docs/project-plan.md), [architecture](docs/architecture.md), [implementation notes](docs/implementation-notes.md) |
| `streamlit_app.py` | Dashboard entry point |

Secrets live in `.env` (never committed). Scraped content is treated as untrusted data.
