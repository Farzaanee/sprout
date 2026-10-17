"""Sum Claude Code token usage from local session transcripts.

Run: `uv run python scripts/session_usage.py --since <UTC timestamp>`.

Claude Code writes one JSONL transcript per session under `~/.claude/projects/<project>/`. Every
assistant message carries the API `usage` block, but a message with several content blocks is
written once per block with the same usage repeated, so rows are deduplicated by message id
(also across files, since resumed sessions copy history). Only measured token counts are
reported; cost and wall time are deliberately not derived.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class Totals:
    requests: int = 0
    input_tokens: int = 0  # fresh (uncached) input only
    output_tokens: int = 0  # includes thinking tokens
    cache_read: int = 0
    cache_write: int = 0


def project_dir(cwd: Path, config_dir: Path | None = None) -> Path:
    """Where Claude Code keeps transcripts for `cwd` (path separators become dashes)."""
    base = config_dir or Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return base / "projects" / str(cwd.resolve()).replace("/", "-")


def parse_timestamp(value: str) -> datetime:
    """ISO 8601, with `Z` accepted; naive values are rejected so windows are never ambiguous."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp needs a timezone (e.g. 2026-10-15T18:00:00Z): {value!r}")
    return parsed.astimezone(UTC)


def _messages(path: Path) -> Iterable[tuple[str, datetime, str, dict[str, Any]]]:
    """Yield (message id, timestamp, model, usage) for assistant rows that carry usage."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a transcript still being written can end mid-line
            message = row.get("message")
            if row.get("type") != "assistant" or not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict) or not message.get("id") or not row.get("timestamp"):
                continue
            yield (
                message["id"],
                parse_timestamp(row["timestamp"]),
                message.get("model") or "unknown",
                usage,
            )


def sum_usage(
    paths: Iterable[Path], *, since: datetime | None = None, until: datetime | None = None
) -> dict[str, Totals]:
    """Token totals per model for messages with `since <= timestamp < until`."""
    seen: set[str] = set()
    totals: dict[str, Totals] = defaultdict(Totals)
    for path in paths:
        for message_id, when, model, usage in _messages(path):
            if message_id in seen:
                continue
            seen.add(message_id)
            if (since and when < since) or (until and when >= until):
                continue
            row = totals[model]
            row.requests += 1
            row.input_tokens += usage.get("input_tokens") or 0
            row.output_tokens += usage.get("output_tokens") or 0
            row.cache_read += usage.get("cache_read_input_tokens") or 0
            row.cache_write += usage.get("cache_creation_input_tokens") or 0
    return dict(totals)


def format_table(totals: dict[str, Totals]) -> str:
    header = (
        "| Model | Requests | Input (fresh) | Output | Cache read | Cache write |\n"
        "|---|---|---|---|---|---|"
    )
    rows = [
        f"| {model} | {t.requests:,} | {t.input_tokens:,} | {t.output_tokens:,} "
        f"| {t.cache_read:,} | {t.cache_write:,} |"
        for model, t in sorted(totals.items())
    ]
    return "\n".join([header, *rows])


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="session_usage.py", description=__doc__)
    parser.add_argument(
        "--since", type=parse_timestamp, help="window start, e.g. 2026-10-15T18:00:00Z"
    )
    parser.add_argument("--until", type=parse_timestamp, help="window end (exclusive)")
    parser.add_argument("--dir", type=Path, help="transcript folder (default: this project's)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    folder = args.dir or project_dir(Path.cwd())
    paths = sorted(folder.rglob("*.jsonl")) if folder.is_dir() else []
    if not paths:
        print(f"no transcripts found in {folder}", file=sys.stderr)
        return 1
    totals = sum_usage(paths, since=args.since, until=args.until)
    if not totals:
        print("no assistant messages in that window", file=sys.stderr)
        return 1
    print(format_table(totals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
