# Phase 0: Setup and definitions

Status: done, one criterion unverified. Part of the [Sprout architecture docs](README.md).

```mermaid
flowchart LR
    ENV[".env: API keys, SPROUT_* overrides"]
    CFG["config.py: Settings, get_settings, require_*_key"]
    MODELS["models.py: Category, Stage, Confidence, Sourced of T, FundingRound, NewsItem, CompanyProfile"]
    HELLO["hello.py: smoke call"]
    TESTS["tests: test_models, test_config, test_live_api"]
    CI["GitHub Actions ci.yml: uv sync, ruff, pytest"]
    CLAUDE(["Anthropic API"])
    ENV --> CFG
    CFG --> HELLO
    HELLO --> CLAUDE
    MODELS --> TESTS
    CFG --> TESTS
    TESTS --> CI
```

The taxonomy, stage enum, `Sourced[T]` and `CompanyProfile` are the contract every later phase depends on.

See [Status per phase](overview.md#7-status-per-phase) for outstanding items (the Claude hello call and CI action versions are unconfirmed).
