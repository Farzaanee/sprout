"""CLI entry point: `uv run python -m sprout.orchestrator`."""

import sys

from sprout.agents.orchestrator import main

if __name__ == "__main__":
    sys.exit(main())
