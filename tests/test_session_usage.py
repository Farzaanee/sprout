import json
from pathlib import Path

import pytest

from sprout.session_usage import (
    format_table,
    main,
    parse_timestamp,
    project_dir,
    sum_usage,
)


def _row(msg_id: str, ts: str, *, model: str = "m1", inp=1, out=10, read=100, write=5) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": msg_id,
            "model": model,
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": write,
            },
        },
    }


def _write(path: Path, rows: list[dict], *, extra: str = "") -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n" + extra)
    return path


def test_blocks_of_one_message_are_counted_once(tmp_path):
    row = _row("a", "2026-10-15T10:00:00Z")
    path = _write(tmp_path / "s.jsonl", [row, row, _row("b", "2026-10-15T10:01:00Z")])
    totals = sum_usage([path])["m1"]
    assert (totals.requests, totals.input_tokens, totals.output_tokens) == (2, 2, 20)
    assert (totals.cache_read, totals.cache_write) == (200, 10)


def test_same_message_in_two_files_is_counted_once(tmp_path):
    rows = [_row("a", "2026-10-15T10:00:00Z")]
    paths = [_write(tmp_path / "1.jsonl", rows), _write(tmp_path / "2.jsonl", rows)]
    assert sum_usage(paths)["m1"].requests == 1


def test_window_is_inclusive_start_exclusive_end(tmp_path):
    rows = [_row(f"m{i}", f"2026-10-15T10:0{i}:00Z") for i in range(4)]
    path = _write(tmp_path / "s.jsonl", rows)
    totals = sum_usage(
        [path],
        since=parse_timestamp("2026-10-15T10:01:00Z"),
        until=parse_timestamp("2026-10-15T10:03:00Z"),
    )
    assert totals["m1"].requests == 2


def test_groups_by_model_and_ignores_other_rows(tmp_path):
    rows = [
        _row("a", "2026-10-15T10:00:00Z", model="x"),
        _row("b", "2026-10-15T10:00:01Z", model="y"),
        {"type": "user", "timestamp": "2026-10-15T10:00:02Z", "message": {"content": "hi"}},
    ]
    path = _write(tmp_path / "s.jsonl", rows, extra='{"type": "assistant", "trunc')
    assert set(sum_usage([path])) == {"x", "y"}


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        parse_timestamp("2026-10-15T10:00:00")


def test_project_dir_maps_path_separators_to_dashes(tmp_path):
    assert project_dir(Path("/Users/sol/Desktop/sprout"), tmp_path) == (
        tmp_path / "projects" / "-Users-sol-Desktop-sprout"
    )


def test_main_prints_markdown_table(tmp_path, capsys):
    _write(tmp_path / "s.jsonl", [_row("a", "2026-10-15T10:00:00Z", out=1234)])
    assert main(["--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "| m1 | 1 | 1 | 1,234 | 100 | 5 |" in out


def test_main_fails_without_transcripts(tmp_path):
    assert main(["--dir", str(tmp_path)]) == 1


def test_format_table_has_header_only_columns_we_measure():
    header = format_table({}).splitlines()[0]
    assert header == "| Model | Requests | Input (fresh) | Output | Cache read | Cache write |"
