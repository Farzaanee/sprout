"""Write a consistent, verified copy of the live database (the committed snapshot).

Run: `make snapshot`, or `uv run python scripts/make_snapshot.py [--source P] [--target P]`.
The weekly job (Phase 9) calls the same function.

A raw file copy of a WAL database can miss the `-wal` sidecar, so this uses `VACUUM INTO`, which
reads through SQLite and includes uncheckpointed writes. The copy is built beside the target,
checked (`PRAGMA integrity_check` plus per-table row counts against the source) and only then
moved into place, so a failed run never destroys the previous snapshot.
"""

import argparse
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_SOURCE = "data/sprout.db"
DEFAULT_TARGET = "data/sprout_snapshot.db"


class SnapshotError(RuntimeError):
    """The snapshot could not be built or did not match the source."""


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        name
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


def make_snapshot(source: Path, target: Path) -> dict[str, int]:
    """Snapshot `source` to `target`, verify it, and return the per-table row counts."""
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise SnapshotError(f"source database not found: {source}")
    if source.resolve() == target.resolve():
        raise SnapshotError("source and target are the same file")
    target.parent.mkdir(parents=True, exist_ok=True)

    # VACUUM INTO errors if its target exists, so clear any leftover from an earlier failed run.
    partial = target.with_name(target.name + ".partial")
    partial.unlink(missing_ok=True)
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        src.execute("VACUUM INTO ?", (str(partial),))
        source_counts = _row_counts(src)
        copy = sqlite3.connect(partial)
        try:
            verdict = copy.execute("PRAGMA integrity_check").fetchall()
            copy_counts = _row_counts(copy)
        finally:
            copy.close()
    except (sqlite3.Error, SnapshotError):
        partial.unlink(missing_ok=True)
        raise
    finally:
        src.close()

    if verdict != [("ok",)]:
        partial.unlink(missing_ok=True)
        raise SnapshotError(f"integrity_check failed on the snapshot: {verdict}")
    if copy_counts != source_counts:
        partial.unlink(missing_ok=True)
        raise SnapshotError(f"row counts differ: source {source_counts}, snapshot {copy_counts}")

    os.replace(partial, target)
    return copy_counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=Path(DEFAULT_SOURCE))
    parser.add_argument("--target", type=Path, default=Path(DEFAULT_TARGET))
    args = parser.parse_args(argv)
    try:
        counts = make_snapshot(args.source, args.target)
    except (SnapshotError, sqlite3.Error) as exc:
        print(f"snapshot failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.target}: integrity ok, {sum(counts.values())} rows in {len(counts)} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
