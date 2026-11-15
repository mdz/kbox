"""
Database snapshots for kbox.

A snapshot is a consistent copy of the SQLite database, taken with SQLite's
online backup API so it is safe while the app is running. One is taken just
before the queue is cleared (the ritual that ends a party), so each party
leaves a point-in-time copy that is useful for debugging and recovery.

Snapshots are written to ``<app data dir>/backups/``. They are complete
copies, so restoring one gives back a working install. That means they
contain the guest access token, session secret and API keys (LLM, YouTube)
as well as guest first names and play history: keep them private, and store
them off-device only somewhere private. For a copy to share (say for
debugging), ``--scrub-secrets`` removes the secret config rows; the app then
regenerates the token and session secret on start, and API keys must be
re-entered.

Command line (run inside the container for a Docker install)::

    python -m kbox.backup create [--include-secrets]
    python -m kbox.backup list
    python -m kbox.backup restore <snapshot-file>
"""

import argparse
import hashlib
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .config_manager import secret_config_keys

logger = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "kbox-"
SNAPSHOT_SUFFIX = ".db"
DEFAULT_KEEP = 20


def default_db_path() -> Path:
    return Path.home() / ".kbox" / "kbox.db"


def list_snapshots(backup_dir: Path) -> List[Path]:
    """Snapshots in backup_dir, oldest first (names embed a sortable timestamp)."""
    if not backup_dir.is_dir():
        return []
    return sorted(
        p
        for p in backup_dir.iterdir()
        if p.name.startswith(SNAPSHOT_PREFIX) and p.name.endswith(SNAPSHOT_SUFFIX)
    )


def _content_fingerprint(path: Path) -> str:
    """Hash of a database's logical content (independent of file layout)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        digest = hashlib.sha256()
        for line in conn.iterdump():
            digest.update(line.encode("utf-8"))
        return digest.hexdigest()
    finally:
        conn.close()


def _same_content(a: Path, b: Path) -> bool:
    try:
        return _content_fingerprint(a) == _content_fingerprint(b)
    except sqlite3.DatabaseError:
        return False  # an unreadable snapshot is never "the same"


def create_snapshot(
    db_path: Path,
    backup_dir: Path,
    scrub_secrets: bool = False,
    keep: int = DEFAULT_KEEP,
) -> Optional[Path]:
    """Write a snapshot of db_path into backup_dir and prune old ones.

    Returns the new snapshot's path, or None if the database is unchanged
    since the newest existing snapshot (nothing new to record).
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)

    fd, tmp_name = tempfile.mkstemp(dir=backup_dir, prefix=".tmp-", suffix=SNAPSHOT_SUFFIX)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            if scrub_secrets:
                secrets_keys = sorted(secret_config_keys())
                placeholders = ",".join("?" * len(secrets_keys))
                dst.execute(f"DELETE FROM config WHERE key IN ({placeholders})", secrets_keys)
            dst.commit()
            dst.execute("VACUUM")  # drop freed pages so deleted secrets leave no residue
        finally:
            dst.close()
            src.close()

        existing = list_snapshots(backup_dir)
        if existing and _same_content(existing[-1], tmp):
            logger.debug(
                "[DEBUG] Database unchanged since %s; skipping snapshot", existing[-1].name
            )
            tmp.unlink()
            return None

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        final = backup_dir / f"{SNAPSHOT_PREFIX}{stamp}{SNAPSHOT_SUFFIX}"
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    for old in list_snapshots(backup_dir)[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    logger.info("Database snapshot written to %s", final)
    return final


def restore_snapshot(snapshot: Path, db_path: Path) -> Optional[Path]:
    """Replace db_path with snapshot. The app must be stopped first.

    An existing database is moved aside to ``<db>.pre-restore`` (returned).
    """
    # Validate before touching anything.
    conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError(f"{snapshot} failed the SQLite integrity check")
    finally:
        conn.close()

    aside = None
    if db_path.exists():
        aside = db_path.with_name(db_path.name + ".pre-restore")
        shutil.move(db_path, aside)
    # Stale journal files from the old database must not be applied to the new one.
    for suffix in ("-wal", "-shm", "-journal"):
        db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
    shutil.copy2(snapshot, db_path)
    return aside


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kbox.backup", description=__doc__.split("\n")[1]
    )
    parser.add_argument("--db", type=Path, default=default_db_path(), help="database file")
    parser.add_argument(
        "--backup-dir", type=Path, default=None, help="snapshot folder (default: <db dir>/backups)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="take a snapshot now")
    create.add_argument(
        "--scrub-secrets",
        action="store_true",
        help="remove access token, session secret and API keys (for sharing; not restorable as-is)",
    )
    sub.add_parser("list", help="list snapshots")
    restore = sub.add_parser("restore", help="replace the database with a snapshot (app stopped)")
    restore.add_argument("snapshot", type=Path)
    args = parser.parse_args(argv)

    backup_dir = args.backup_dir or args.db.parent / "backups"
    if args.command == "create":
        path = create_snapshot(args.db, backup_dir, scrub_secrets=args.scrub_secrets)
        print(path if path else "Database unchanged since the last snapshot; nothing written.")
    elif args.command == "list":
        for p in list_snapshots(backup_dir):
            print(p)
    else:
        aside = restore_snapshot(args.snapshot, args.db)
        print(f"Restored {args.snapshot} to {args.db}")
        if aside:
            print(f"Previous database kept at {aside}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
