"""CLI entry point: `uv run python -m sprout.enrich NAME_OR_NUMBER ...`."""

import sys

from sprout.agents.enrich import main

if __name__ == "__main__":
    sys.exit(main())
