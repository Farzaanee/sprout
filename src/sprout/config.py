"""Runtime configuration, read from the environment (and a local .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_DB_PATH = "data/sprout.db"
DEFAULT_SNAPSHOT_DB_PATH = "data/sprout_snapshot.db"
DEFAULT_CACHE_DIR = ".cache/http"

# Cheap model for bulk extraction/classification; stronger one for orchestration,
# verification and synthesis. IDs are exact strings from the Anthropic model table.
DEFAULT_BULK_MODEL = "claude-haiku-5-5"
DEFAULT_STRONG_MODEL = "claude-sonnet-5-5"


class MissingConfigError(RuntimeError):
    """A required setting (usually an API key) is not set."""


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    companies_house_api_key: str | None
    bulk_model: str
    strong_model: str
    db_path: Path = Path(DEFAULT_DB_PATH)
    cache_dir: Path = Path(DEFAULT_CACHE_DIR)

    def require_anthropic_key(self) -> str:
        if not self.anthropic_api_key:
            raise MissingConfigError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env.")
        return self.anthropic_api_key

    def require_companies_house_key(self) -> str:
        if not self.companies_house_api_key:
            raise MissingConfigError(
                "COMPANIES_HOUSE_API_KEY is not set. Copy .env.example to .env."
            )
        return self.companies_house_api_key


def get_settings() -> Settings:
    load_dotenv()
    live, snapshot = Path(DEFAULT_DB_PATH), Path(DEFAULT_SNAPSHOT_DB_PATH)
    default_db_path = live if live.exists() or not snapshot.exists() else snapshot
    env_db_path = os.environ.get("SPROUT_DB_PATH")
    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        companies_house_api_key=os.environ.get("COMPANIES_HOUSE_API_KEY") or None,
        bulk_model=os.environ.get("SPROUT_BULK_MODEL", DEFAULT_BULK_MODEL),
        strong_model=os.environ.get("SPROUT_STRONG_MODEL", DEFAULT_STRONG_MODEL),
        db_path=Path(env_db_path) if env_db_path else default_db_path,
        cache_dir=Path(os.environ.get("SPROUT_CACHE_DIR") or DEFAULT_CACHE_DIR),
    )
