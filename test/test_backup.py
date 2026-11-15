"""Tests for database snapshots (kbox.backup) and the clear-queue hook."""

import sqlite3
from unittest.mock import MagicMock

import pytest

from kbox.backup import (
    create_snapshot,
    list_snapshots,
    main,
    restore_snapshot,
)
from kbox.config_manager import secret_config_keys
from kbox.database import Database
from kbox.queue import QueueManager


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "kbox.db"
    db = Database(db_path=str(path))
    conn = db.get_connection()
    conn.execute("INSERT INTO config (key, value) VALUES ('access_token', 'tok-secret')")
    conn.execute("INSERT INTO config (key, value) VALUES ('llm_api_key', 'key-secret')")
    conn.execute("INSERT INTO config (key, value) VALUES ('web_port', '8000')")
    conn.commit()
    conn.close()
    return path


def _config_keys(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT key FROM config")}
    finally:
        conn.close()


def test_secret_keys_cover_known_secrets():
    assert {"access_token", "session_secret", "llm_api_key", "youtube_api_key"} <= (
        secret_config_keys()
    )


def test_snapshot_scrubs_secrets_by_default(db_path, tmp_path):
    snap = create_snapshot(db_path, tmp_path / "backups")
    assert snap is not None
    assert _config_keys(snap) == {"web_port"}
    assert b"tok-secret" not in snap.read_bytes()
    assert b"key-secret" not in snap.read_bytes()
    # The live database is untouched.
    assert "access_token" in _config_keys(db_path)


def test_snapshot_can_include_secrets(db_path, tmp_path):
    snap = create_snapshot(db_path, tmp_path / "backups", include_secrets=True)
    assert {"access_token", "llm_api_key"} <= _config_keys(snap)


def test_snapshot_is_private(db_path, tmp_path):
    snap = create_snapshot(db_path, tmp_path / "backups")
    assert snap.stat().st_mode & 0o077 == 0
    assert (tmp_path / "backups").stat().st_mode & 0o077 == 0


def test_unchanged_database_is_skipped(db_path, tmp_path):
    backups = tmp_path / "backups"
    assert create_snapshot(db_path, backups) is not None
    assert create_snapshot(db_path, backups) is None
    assert len(list_snapshots(backups)) == 1
    assert not [p for p in backups.iterdir() if p.name.startswith(".tmp-")]


def test_changed_database_gets_new_snapshot(db_path, tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    first = create_snapshot(db_path, backups)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE config SET value = '9000' WHERE key = 'web_port'")
    conn.commit()
    conn.close()
    # Names have one-second resolution; make the second distinct.
    first.rename(backups / "kbox-20000101-000000.db")
    second = create_snapshot(db_path, backups)
    assert second is not None
    assert len(list_snapshots(backups)) == 2


def test_retention_prunes_oldest(db_path, tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    for i in range(5):
        (backups / f"kbox-2000010{i}-000000.db").write_bytes(b"old")
    snap = create_snapshot(db_path, backups, keep=3)
    names = [p.name for p in list_snapshots(backups)]
    assert len(names) == 3
    assert snap.name in names
    assert "kbox-20000100-000000.db" not in names


def test_restore_replaces_database_and_keeps_old(db_path, tmp_path):
    snap = create_snapshot(db_path, tmp_path / "backups")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE config SET value = '1234' WHERE key = 'web_port'")
    conn.commit()
    conn.close()

    aside = restore_snapshot(snap, db_path)

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT value FROM config WHERE key='web_port'").fetchone()[0] == "8000"
    conn.close()
    assert aside is not None and aside.exists()


def test_restore_rejects_corrupt_snapshot(db_path, tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database" * 100)
    with pytest.raises(Exception):
        restore_snapshot(bad, db_path)
    assert "web_port" in _config_keys(db_path)


def test_cli_create_and_list(db_path, tmp_path, capsys):
    assert main(["--db", str(db_path), "create"]) == 0
    assert main(["--db", str(db_path), "list"]) == 0
    out = capsys.readouterr().out
    assert "kbox-" in out
    assert (db_path.parent / "backups").is_dir()


def test_clear_queue_runs_before_clear_hook_first():
    calls = []
    qm = QueueManager(
        MagicMock(), video_library=MagicMock(), before_clear=lambda: calls.append("hook")
    )
    qm.repository = MagicMock()
    qm.repository.clear.side_effect = lambda: calls.append("clear") or 0
    qm.clear_queue()
    assert calls == ["hook", "clear"]


def test_clear_queue_survives_hook_failure():
    def boom():
        raise RuntimeError("disk full")

    qm = QueueManager(MagicMock(), video_library=MagicMock(), before_clear=boom)
    qm.repository = MagicMock()
    qm.repository.clear.return_value = 3
    assert qm.clear_queue() == 3
