import sqlite3
from pathlib import Path

import pytest
from make_snapshot import SnapshotError, main, make_snapshot


@pytest.fixture
def wal_db(tmp_path: Path):
    """A WAL database whose latest write is still only in the `-wal` file."""
    path = tmp_path / "live.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE company (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE note (id INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO company (name) VALUES (?)", [("a",), ("b",)])
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    yield path, conn
    conn.close()


def test_snapshot_contains_uncheckpointed_wal_write_that_a_raw_copy_misses(wal_db, tmp_path):
    path, conn = wal_db
    conn.execute("INSERT INTO company (name) VALUES ('wal-only')")
    conn.commit()
    assert Path(f"{path}-wal").stat().st_size > 0

    raw = tmp_path / "raw.db"
    raw.write_bytes(path.read_bytes())
    raw_conn = sqlite3.connect(raw)
    assert raw_conn.execute("SELECT COUNT(*) FROM company").fetchone()[0] == 2
    raw_conn.close()

    target = tmp_path / "out" / "snap.db"
    counts = make_snapshot(path, target)

    assert counts == {"company": 3, "note": 0}
    snap = sqlite3.connect(target)
    assert snap.execute("SELECT name FROM company ORDER BY id").fetchall() == [
        ("a",),
        ("b",),
        ("wal-only",),
    ]
    snap.close()
    assert not target.with_name("snap.db-wal").exists()
    assert not target.with_name("snap.db.partial").exists()


def test_snapshot_replaces_an_existing_target(wal_db, tmp_path):
    path, _ = wal_db
    target = tmp_path / "snap.db"
    target.write_text("stale")

    make_snapshot(path, target)

    snap = sqlite3.connect(target)
    assert snap.execute("SELECT COUNT(*) FROM company").fetchone()[0] == 2
    snap.close()


def test_missing_source_is_refused_and_creates_nothing(tmp_path):
    target = tmp_path / "snap.db"
    with pytest.raises(SnapshotError, match="not found"):
        make_snapshot(tmp_path / "nope.db", target)
    assert not target.exists()
    assert not (tmp_path / "nope.db").exists()


def test_same_file_is_refused(wal_db):
    path, _ = wal_db
    with pytest.raises(SnapshotError, match="same file"):
        make_snapshot(path, path)


def test_failed_verification_keeps_the_previous_snapshot(wal_db, tmp_path, monkeypatch):
    path, _ = wal_db
    target = tmp_path / "snap.db"
    make_snapshot(path, target)
    good = target.read_bytes()

    monkeypatch.setattr("make_snapshot._row_counts", lambda conn: {"x": id(conn)})
    with pytest.raises(SnapshotError, match="row counts differ"):
        make_snapshot(path, target)

    assert target.read_bytes() == good
    assert not target.with_name("snap.db.partial").exists()


def test_main_reports_success_and_failure(wal_db, tmp_path, capsys):
    path, _ = wal_db
    assert main(["--source", str(path), "--target", str(tmp_path / "s.db")]) == 0
    assert "integrity ok" in capsys.readouterr().out

    assert main(["--source", str(tmp_path / "nope.db"), "--target", str(tmp_path / "t.db")]) == 1
    assert "snapshot failed" in capsys.readouterr().err
