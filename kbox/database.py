"""
Database module for kbox.

Handles SQLite database initialization, schema creation, and connection management.
Includes repository classes that encapsulate all SQL operations.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .identity import normalize_name, pick_avatar
from .models import (
    ConfigEntry,
    Favorite,
    HistoryRecord,
    QueueItem,
    Session,
    SongMetadata,
    SongSettings,
    User,
)


class Database:
    """Manages SQLite database connection and schema."""

    # Schema version for migrations
    SCHEMA_VERSION = 9  # Incremented for name-keyed identity (normalized_name/icon/color)

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file. If None, uses ~/.kbox/kbox.db
        """
        self.logger = logging.getLogger(__name__)

        if db_path is None:
            # Default to ~/.kbox/kbox.db
            home = Path.home()
            kbox_dir = home / ".kbox"
            kbox_dir.mkdir(exist_ok=True)
            db_path = str(kbox_dir / "kbox.db")

        self.db_path = db_path
        self._ensure_schema()
        self.logger.info("Database initialized at %s", self.db_path)

    def _ensure_schema(self):
        """Ensure database schema exists and is up to date (thread-safe)."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check current schema version
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
        """)
        cursor.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()
        current_version = row["version"] if row else 0

        # Users table - UUID-based identity with display name
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Configuration table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        if current_version < 2:
            # Version 2: Migrate to video_id from (source, source_id)
            self._migrate_to_video_id(cursor, conn)

        if current_version < 3:
            # Version 3: Add song_metadata_cache table
            self._create_song_metadata_cache(cursor)

        if current_version < 4:
            # Version 4: Add user_events table for interaction logging,
            # and theme column to playback_history
            self._create_user_events_table(cursor)
            self._add_history_theme_column(cursor)

        if current_version < 5:
            # Version 5: Add sessions table and session_id columns on
            # queue_items and playback_history. Backfill existing rows to
            # a single retroactive session.
            self._create_sessions_table(cursor)
            self._add_session_id_columns(cursor)

        if current_version < 6:
            # Version 6: Add favorites table
            self._create_favorites_table(cursor)

        if current_version < 7:
            # Version 7: Add trailing-silence detection columns to
            # song_metadata_cache, and relax artist/song_name to nullable
            # (a video can now get a cache row from silence analysis alone,
            # before/without metadata extraction ever running for it).
            self._add_silence_detection_columns(cursor)

        if current_version < 8:
            # Version 8: Add loudness-measurement columns to
            # song_metadata_cache for volume normalization. artist/song_name
            # are already nullable as of version 7, so this is a plain
            # additive ALTER TABLE - no table recreation needed.
            self._add_loudness_columns(cursor)

        if current_version < 9:
            # Version 9: Add normalized_name/icon/color/last_seen_at to users
            # for name-keyed identity lookup, and backfill existing rows.
            self._add_user_identity_columns(cursor)

        # Store current schema version
        cursor.execute("DELETE FROM schema_version")
        cursor.execute("INSERT INTO schema_version (version) VALUES (?)", (self.SCHEMA_VERSION,))

        conn.commit()
        conn.close()
        self.logger.debug("Database schema created/verified (version %d)", self.SCHEMA_VERSION)

    def _migrate_to_video_id(self, cursor, conn):
        """Migrate from (source, source_id) to video_id schema."""
        self.logger.info("Migrating database to video_id schema...")

        # Check if queue_items table exists with old schema
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='queue_items'")
        queue_exists = cursor.fetchone() is not None

        if queue_exists:
            # Check if it has the old schema (source column)
            cursor.execute("PRAGMA table_info(queue_items)")
            columns = {row["name"] for row in cursor.fetchall()}

            if "source" in columns and "video_id" not in columns:
                self.logger.info("Migrating queue_items table...")
                # Create new table with video_id
                cursor.execute("""
                    CREATE TABLE queue_items_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        position INTEGER NOT NULL,
                        download_status TEXT DEFAULT 'pending',
                        played_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        user_id TEXT NOT NULL,
                        user_name TEXT NOT NULL,
                        video_id TEXT NOT NULL,
                        song_metadata_json TEXT NOT NULL,
                        settings_json TEXT NOT NULL DEFAULT '{}',
                        download_json TEXT
                    )
                """)

                # Copy data, combining source and source_id into video_id
                cursor.execute("""
                    INSERT INTO queue_items_new
                    (id, position, download_status, played_at, created_at, user_id, user_name,
                     video_id, song_metadata_json, settings_json, download_json)
                    SELECT
                        id, position, download_status, played_at, created_at, user_id, user_name,
                        source || ':' || source_id, song_metadata_json, settings_json, download_json
                    FROM queue_items
                """)

                # Drop old table and rename new one
                cursor.execute("DROP TABLE queue_items")
                cursor.execute("ALTER TABLE queue_items_new RENAME TO queue_items")
                self.logger.info("queue_items migration complete")
            elif "video_id" not in columns:
                # Table exists but doesn't have either - create fresh
                cursor.execute("DROP TABLE queue_items")
                queue_exists = False

        if not queue_exists:
            # Create queue_items table with new schema
            cursor.execute("""
                CREATE TABLE queue_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position INTEGER NOT NULL,
                    download_status TEXT DEFAULT 'pending',
                    played_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    song_metadata_json TEXT NOT NULL,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    download_json TEXT
                )
            """)

        # Check if playback_history table exists with old schema
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='playback_history'"
        )
        history_exists = cursor.fetchone() is not None

        if history_exists:
            cursor.execute("PRAGMA table_info(playback_history)")
            columns = {row["name"] for row in cursor.fetchall()}

            if "source" in columns and "video_id" not in columns:
                self.logger.info("Migrating playback_history table...")
                # Create new table with video_id
                cursor.execute("""
                    CREATE TABLE playback_history_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        video_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        user_name TEXT NOT NULL,
                        performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        song_metadata_json TEXT NOT NULL,
                        settings_json TEXT NOT NULL DEFAULT '{}',
                        performance_json TEXT NOT NULL
                    )
                """)

                # Copy data
                cursor.execute("""
                    INSERT INTO playback_history_new
                    (id, video_id, user_id, user_name, performed_at,
                     song_metadata_json, settings_json, performance_json)
                    SELECT
                        id, source || ':' || source_id, user_id, user_name, performed_at,
                        song_metadata_json, settings_json, performance_json
                    FROM playback_history
                """)

                # Drop old table and rename
                cursor.execute("DROP TABLE playback_history")
                cursor.execute("ALTER TABLE playback_history_new RENAME TO playback_history")
                self.logger.info("playback_history migration complete")
            elif "video_id" not in columns:
                cursor.execute("DROP TABLE playback_history")
                history_exists = False

        if not history_exists:
            cursor.execute("""
                CREATE TABLE playback_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    song_metadata_json TEXT NOT NULL,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    performance_json TEXT NOT NULL,
                    theme TEXT
                )
            """)

        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_user_id_video
            ON playback_history(user_id, video_id, performed_at DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_time
            ON playback_history(performed_at DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_user_id
            ON playback_history(user_id, performed_at DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_user_id
            ON queue_items(user_id)
        """)

        self.logger.info("Database migration to video_id complete")

    def _create_song_metadata_cache(self, cursor):
        """Create song_metadata_cache table for caching LLM extractions."""
        self.logger.info("Creating song_metadata_cache table...")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS song_metadata_cache (
                video_id TEXT PRIMARY KEY,
                artist TEXT NOT NULL,
                song_name TEXT NOT NULL,
                extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.logger.info("song_metadata_cache table created")

    def _add_silence_detection_columns(self, cursor):
        """Add trailing-silence columns to song_metadata_cache.

        Also relaxes artist/song_name from NOT NULL to nullable: a row can
        now be created by silence analysis alone (which runs independently
        of, and may complete before, LLM metadata extraction for the same
        video). SQLite can't drop a NOT NULL constraint in place, so the
        table is recreated.
        """
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='song_metadata_cache'"
        )
        if cursor.fetchone() is None:
            # Fresh database - _create_song_metadata_cache hasn't run yet on
            # older code paths, but on a new DB we create it directly here.
            cursor.execute("""
                CREATE TABLE song_metadata_cache (
                    video_id TEXT PRIMARY KEY,
                    artist TEXT,
                    song_name TEXT,
                    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    trailing_silence_start_seconds INTEGER,
                    silence_analyzed_at TIMESTAMP
                )
            """)
            self.logger.info("song_metadata_cache table created (with silence columns)")
            return

        cursor.execute("PRAGMA table_info(song_metadata_cache)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "trailing_silence_start_seconds" in columns:
            return  # Already migrated

        self.logger.info("Migrating song_metadata_cache for silence detection...")

        cursor.execute("""
            CREATE TABLE song_metadata_cache_new (
                video_id TEXT PRIMARY KEY,
                artist TEXT,
                song_name TEXT,
                extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                trailing_silence_start_seconds INTEGER,
                silence_analyzed_at TIMESTAMP
            )
        """)

        # extracted_at may not exist on older/hand-built schemas -- copy it
        # only if present, and let the new column default otherwise.
        if "extracted_at" in columns:
            cursor.execute("""
                INSERT INTO song_metadata_cache_new (video_id, artist, song_name, extracted_at)
                SELECT video_id, artist, song_name, extracted_at FROM song_metadata_cache
            """)
        else:
            cursor.execute("""
                INSERT INTO song_metadata_cache_new (video_id, artist, song_name)
                SELECT video_id, artist, song_name FROM song_metadata_cache
            """)

        cursor.execute("DROP TABLE song_metadata_cache")
        cursor.execute("ALTER TABLE song_metadata_cache_new RENAME TO song_metadata_cache")

        self.logger.info("song_metadata_cache migration complete")

    def _add_loudness_columns(self, cursor):
        """Add loudness-measurement columns to song_metadata_cache.

        artist/song_name are already nullable as of the version-7 migration
        (_add_silence_detection_columns), which also guarantees the table
        exists by the time this runs - so this is a plain additive
        ALTER TABLE, no recreation needed.
        """
        cursor.execute("PRAGMA table_info(song_metadata_cache)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "integrated_lufs" in columns:
            return  # Already migrated

        self.logger.info("Adding loudness columns to song_metadata_cache...")
        cursor.execute("ALTER TABLE song_metadata_cache ADD COLUMN integrated_lufs REAL")
        cursor.execute("ALTER TABLE song_metadata_cache ADD COLUMN true_peak_dbtp REAL")
        cursor.execute("ALTER TABLE song_metadata_cache ADD COLUMN loudness_analyzed_at TIMESTAMP")

    def _create_user_events_table(self, cursor):
        """Create user_events table for interaction logging (search queries, etc.)."""
        self.logger.info("Creating user_events table...")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_events_user
            ON user_events (user_id, created_at DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_events_type
            ON user_events (event_type, created_at DESC)
        """)

        self.logger.info("user_events table created")

    def _create_favorites_table(self, cursor):
        """Create favorites table for user-starred songs."""
        self.logger.info("Creating favorites table...")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                user_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                song_metadata_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, video_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_favorites_user_id
            ON favorites (user_id, created_at DESC)
        """)

        self.logger.info("favorites table created")

    def _add_user_identity_columns(self, cursor):
        """Add normalized_name/icon/color/last_seen_at to users, and backfill
        existing rows so name-keyed lookup works for pre-existing guests too.

        normalized_name isn't declared NOT NULL at the SQLite level (adding a
        NOT NULL column requires a full table rebuild in SQLite) — it's kept
        non-null in practice by UserRepository.create()/update_display_name()
        always setting it, same as the denormalized user_name columns
        elsewhere in this schema.
        """
        cursor.execute("PRAGMA table_info(users)")
        columns = {row["name"] for row in cursor.fetchall()}

        if "normalized_name" not in columns:
            self.logger.info("Adding normalized_name column to users...")
            cursor.execute("ALTER TABLE users ADD COLUMN normalized_name TEXT")
        if "icon" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN icon TEXT")
        if "color" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN color TEXT")
        if "last_seen_at" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN last_seen_at TIMESTAMP")

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_normalized_name ON users(normalized_name)"
        )

        # Backfill any row missing the new fields (fresh ALTER columns above,
        # or a row that predates this migration). Safe to re-run: only rows
        # still missing normalized_name are touched.
        cursor.execute(
            "SELECT id, display_name, created_at FROM users WHERE normalized_name IS NULL"
        )
        rows = cursor.fetchall()
        for row in rows:
            icon, color = pick_avatar(row["id"])
            cursor.execute(
                """
                UPDATE users
                SET normalized_name = ?, icon = ?, color = ?,
                    last_seen_at = COALESCE(last_seen_at, created_at, CURRENT_TIMESTAMP)
                WHERE id = ?
                """,
                (normalize_name(row["display_name"]), icon, color, row["id"]),
            )
        if rows:
            self.logger.info("Backfilled identity columns for %d existing user(s)", len(rows))

    def _add_history_theme_column(self, cursor):
        """Add theme column to playback_history for existing databases."""
        cursor.execute("PRAGMA table_info(playback_history)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "theme" not in columns:
            self.logger.info("Adding theme column to playback_history...")
            cursor.execute("ALTER TABLE playback_history ADD COLUMN theme TEXT")

    def _create_sessions_table(self, cursor):
        """Create sessions table."""
        self.logger.info("Creating sessions table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP,
                theme TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_current
            ON sessions (ended_at, created_at DESC)
        """)

    def _add_session_id_columns(self, cursor):
        """Add session_id columns to queue_items and playback_history and
        backfill existing rows to a single retroactive session."""
        # Check which tables need the column
        cursor.execute("PRAGMA table_info(queue_items)")
        queue_cols = {row["name"] for row in cursor.fetchall()}
        cursor.execute("PRAGMA table_info(playback_history)")
        history_cols = {row["name"] for row in cursor.fetchall()}

        need_queue = "session_id" not in queue_cols
        need_history = "session_id" not in history_cols

        if not (need_queue or need_history):
            return

        # Find earliest created_at / performed_at across existing rows to
        # use as the retroactive session's created_at.
        earliest = None
        if need_queue:
            cursor.execute("SELECT MIN(created_at) AS m FROM queue_items")
            row = cursor.fetchone()
            if row and row["m"]:
                earliest = row["m"]
        if need_history:
            cursor.execute("SELECT MIN(performed_at) AS m FROM playback_history")
            row = cursor.fetchone()
            if row and row["m"]:
                if earliest is None or row["m"] < earliest:
                    earliest = row["m"]

        # Check whether there's any existing row that needs backfilling.
        has_existing = False
        if need_queue:
            cursor.execute("SELECT COUNT(*) AS c FROM queue_items")
            if cursor.fetchone()["c"] > 0:
                has_existing = True
        if not has_existing and need_history:
            cursor.execute("SELECT COUNT(*) AS c FROM playback_history")
            if cursor.fetchone()["c"] > 0:
                has_existing = True

        retro_session_id = None
        if has_existing:
            # Create a single closed session for all pre-existing rows.
            if earliest is not None:
                cursor.execute(
                    "INSERT INTO sessions (created_at, ended_at) VALUES (?, CURRENT_TIMESTAMP)",
                    (earliest,),
                )
            else:
                cursor.execute("INSERT INTO sessions (ended_at) VALUES (CURRENT_TIMESTAMP)")
            retro_session_id = cursor.lastrowid
            self.logger.info(
                "Created retroactive session %s for pre-existing queue/history rows",
                retro_session_id,
            )

        if need_queue:
            cursor.execute("ALTER TABLE queue_items ADD COLUMN session_id INTEGER")
            if retro_session_id is not None:
                cursor.execute(
                    "UPDATE queue_items SET session_id = ? WHERE session_id IS NULL",
                    (retro_session_id,),
                )

        if need_history:
            cursor.execute("ALTER TABLE playback_history ADD COLUMN session_id INTEGER")
            if retro_session_id is not None:
                cursor.execute(
                    "UPDATE playback_history SET session_id = ? WHERE session_id IS NULL",
                    (retro_session_id,),
                )

    def get_connection(self):
        """
        Get a new database connection (thread-safe).

        Each thread should get its own connection. Caller is responsible
        for closing the connection when done.
        """
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def close(self):
        """Close database connection (no-op since we use per-thread connections)."""
        # No-op since we create connections per-thread now
        pass

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


# ============================================================================
# Shared JSON Codecs
# ============================================================================


def _encode_metadata(metadata: SongMetadata) -> str:
    """Encode SongMetadata to JSON for storage."""
    return json.dumps(
        {
            "title": metadata.title,
            "duration_seconds": metadata.duration_seconds,
            "thumbnail_url": metadata.thumbnail_url,
            "channel": metadata.channel,
            "artist": metadata.artist,
            "song_name": metadata.song_name,
        }
    )


def _decode_metadata(metadata_json: str) -> SongMetadata:
    """Decode SongMetadata from JSON."""
    if not metadata_json:
        return SongMetadata(title="Unknown")
    try:
        data = json.loads(metadata_json)
        return SongMetadata(
            title=data.get("title", "Unknown"),
            duration_seconds=data.get("duration_seconds"),
            thumbnail_url=data.get("thumbnail_url"),
            channel=data.get("channel"),
            artist=data.get("artist"),
            song_name=data.get("song_name"),
        )
    except (json.JSONDecodeError, TypeError):
        return SongMetadata(title="Unknown")


def _encode_settings(settings: SongSettings) -> str:
    """Encode SongSettings to JSON for storage."""
    return json.dumps({"pitch_semitones": settings.pitch_semitones})


def _decode_settings(settings_json: str) -> SongSettings:
    """Decode SongSettings from JSON."""
    if not settings_json:
        return SongSettings()
    try:
        data = json.loads(settings_json)
        return SongSettings(pitch_semitones=data.get("pitch_semitones") or 0)
    except (json.JSONDecodeError, TypeError):
        return SongSettings()


# ============================================================================
# Repository Classes
# ============================================================================


class UserRepository:
    """Repository for user operations."""

    _COLUMNS = "id, display_name, created_at, normalized_name, icon, color, last_seen_at"

    def __init__(self, database: Database):
        self.database = database
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _row_to_user(row) -> User:
        return User(
            id=row["id"],
            display_name=row["display_name"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            normalized_name=row["normalized_name"],
            icon=row["icon"],
            color=row["color"],
            last_seen_at=datetime.fromisoformat(row["last_seen_at"])
            if row["last_seen_at"]
            else None,
        )

    def get_by_id(self, user_id: str) -> Optional[User]:
        """Get a user by ID."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {self._COLUMNS} FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return self._row_to_user(row) if row else None
        finally:
            conn.close()

    def find_by_normalized_name(self, normalized: str) -> List[User]:
        """Find all users whose name normalizes to `normalized`.

        Returns the recognition-list candidates for a typed name — most
        recently seen first, since that's the guest most likely to be typing
        again right now. Not unique by design: a shared name is an expected
        collision this list exists to help a guest resolve, not an error.
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT {self._COLUMNS} FROM users
                WHERE normalized_name = ?
                ORDER BY last_seen_at DESC
                """,
                (normalized,),
            )
            return [self._row_to_user(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def create(self, user_id: str, display_name: str) -> User:
        """Create a new user."""
        icon, color = pick_avatar(user_id)
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users
                    (id, display_name, normalized_name, icon, color, last_seen_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (user_id, display_name, normalize_name(display_name), icon, color),
            )
            conn.commit()

            cursor.execute(f"SELECT {self._COLUMNS} FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            self.logger.info("Created new user: %s (%s)", display_name, user_id)
            return self._row_to_user(row)
        finally:
            conn.close()

    def update_display_name(self, user_id: str, display_name: str) -> bool:
        """Update a user's display name (and the normalized_name derived from it)."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET display_name = ?, normalized_name = ? WHERE id = ?",
                (display_name, normalize_name(display_name), user_id),
            )
            updated = cursor.rowcount > 0
            conn.commit()
            if updated:
                self.logger.info("Updated display name for user %s: %s", user_id, display_name)
            return updated
        finally:
            conn.close()

    def touch_last_seen(self, user_id: str) -> None:
        """Update last_seen_at to now — called whenever a session (re)binds to this user."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,)
            )
            conn.commit()
        finally:
            conn.close()

    def merge_users(self, keep_id: str, merge_id: str) -> None:
        """Fold merge_id's history into keep_id, then delete merge_id.

        For coalescing identities that predate name-keyed lookup, or any
        ghost identity an operator confirms is the same person as another
        record (see docs/design/guest-identity-continuity.md — this system never
        merges automatically). There are no SQL foreign-key constraints
        anywhere in this schema, so this is plain per-table reassignment,
        not a cascade.
        """
        if keep_id == merge_id:
            raise ValueError("keep_id and merge_id must be different users")

        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()

            cursor.execute("SELECT 1 FROM users WHERE id = ?", (keep_id,))
            if cursor.fetchone() is None:
                raise ValueError(f"keep_id {keep_id!r} is not a known user")
            cursor.execute("SELECT 1 FROM users WHERE id = ?", (merge_id,))
            if cursor.fetchone() is None:
                raise ValueError(f"merge_id {merge_id!r} is not a known user")

            # favorites has a (user_id, video_id) PRIMARY KEY — a song
            # favorited under both identities would collide on reassignment,
            # so drop merge_id's duplicate rather than fail the whole merge
            # over one already-favorited song.
            cursor.execute(
                """
                DELETE FROM favorites
                WHERE user_id = ? AND video_id IN (
                    SELECT video_id FROM favorites WHERE user_id = ?
                )
                """,
                (merge_id, keep_id),
            )
            cursor.execute(
                "UPDATE favorites SET user_id = ? WHERE user_id = ?", (keep_id, merge_id)
            )

            for table in ("queue_items", "playback_history", "user_events"):
                cursor.execute(
                    f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (keep_id, merge_id)
                )

            cursor.execute("DELETE FROM users WHERE id = ?", (merge_id,))
            conn.commit()
            self.logger.info("Merged user %s into %s", merge_id, keep_id)
        finally:
            conn.close()


class ConfigRepository:
    """Repository for configuration operations."""

    def __init__(self, database: Database):
        self.database = database
        self.logger = logging.getLogger(__name__)

    def get(self, key: str) -> Optional[ConfigEntry]:
        """Get a configuration entry by key."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM config WHERE key = ?", (key,))
            result = cursor.fetchone()
            if result:
                return ConfigEntry(key=result["key"], value=result["value"])
            return None
        finally:
            conn.close()

    def set(self, key: str, value: str) -> bool:
        """Set a configuration value."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO config (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
            """,
                (key, value),
            )
            conn.commit()
            self.logger.debug("Set config %s = %s", key, value)
            return True
        finally:
            conn.close()

    def get_all(self) -> List[ConfigEntry]:
        """Get all configuration entries."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value, updated_at FROM config")
            entries = []
            for row in cursor.fetchall():
                entries.append(
                    ConfigEntry(
                        key=row["key"],
                        value=row["value"],
                        updated_at=datetime.fromisoformat(row["updated_at"])
                        if row["updated_at"]
                        else None,
                    )
                )
            return entries
        finally:
            conn.close()

    def initialize_defaults(self, defaults: Dict[str, Any]) -> None:
        """Initialize default values in database if they don't exist."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            for key, value in defaults.items():
                cursor.execute("SELECT key FROM config WHERE key = ?", (key,))
                if not cursor.fetchone():
                    cursor.execute(
                        """
                        INSERT INTO config (key, value)
                        VALUES (?, ?)
                    """,
                        (key, str(value) if value is not None else ""),
                    )
            conn.commit()
            self.logger.debug("Configuration defaults initialized")
        finally:
            conn.close()


class HistoryRepository:
    """Repository for playback history operations."""

    def __init__(self, database: Database):
        self.database = database
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _encode_performance(performance: Dict[str, Any]) -> str:
        """Encode performance metrics to JSON."""
        return json.dumps(performance)

    @staticmethod
    def _decode_performance(performance_json: str) -> Dict[str, Any]:
        """Decode performance metrics from JSON."""
        if not performance_json:
            return {}
        try:
            return json.loads(performance_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    def record(
        self,
        user_id: str,
        user_name: str,
        video_id: str,
        metadata: SongMetadata,
        settings: SongSettings,
        performance: Dict[str, Any],
        theme: Optional[str] = None,
        session_id: Optional[int] = None,
    ) -> int:
        """Record a performance in history."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO playback_history (
                    user_id,
                    user_name,
                    video_id,
                    song_metadata_json,
                    settings_json,
                    performance_json,
                    theme,
                    session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    user_id,
                    user_name,
                    video_id,
                    _encode_metadata(metadata),
                    _encode_settings(settings),
                    self._encode_performance(performance),
                    theme or None,
                    session_id,
                ),
            )
            conn.commit()
            history_id = cursor.lastrowid
            self.logger.info(
                "Recorded history: %s sang %s (video_id=%s)",
                user_name,
                metadata.title,
                video_id,
            )
            return history_id
        finally:
            conn.close()

    def get_last_settings(self, video_id: str, user_id: str) -> Optional[SongSettings]:
        """Get the last used settings for a song from playback history."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT settings_json
                FROM playback_history
                WHERE video_id = ? AND user_id = ?
                ORDER BY performed_at DESC, id DESC
                LIMIT 1
            """,
                (video_id, user_id),
            )
            result = cursor.fetchone()
            if result:
                return _decode_settings(result["settings_json"])
            return None
        finally:
            conn.close()

    def get_user_history(self, user_id: str, limit: int = 50) -> List[HistoryRecord]:
        """Get playback history for a specific user."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    id,
                    video_id,
                    user_id,
                    user_name,
                    performed_at,
                    song_metadata_json,
                    settings_json,
                    performance_json,
                    theme,
                    session_id
                FROM playback_history
                WHERE user_id = ?
                ORDER BY performed_at DESC, id DESC
                LIMIT ?
            """,
                (user_id, limit),
            )

            records = []
            for row in cursor.fetchall():
                records.append(
                    HistoryRecord(
                        id=row["id"],
                        video_id=row["video_id"],
                        user_id=row["user_id"],
                        user_name=row["user_name"],
                        metadata=_decode_metadata(row["song_metadata_json"]),
                        settings=_decode_settings(row["settings_json"]),
                        performance=self._decode_performance(row["performance_json"]),
                        performed_at=datetime.fromisoformat(row["performed_at"])
                        if row["performed_at"]
                        else None,
                        theme=row["theme"],
                        session_id=row["session_id"],
                    )
                )
            return records
        finally:
            conn.close()


class FavoriteRepository:
    """Repository for favorite (starred) song operations."""

    def __init__(self, database: Database):
        self.database = database
        self.logger = logging.getLogger(__name__)

    def add(self, user_id: str, video_id: str, metadata: SongMetadata) -> None:
        """Star a song for a user (idempotent - re-starring refreshes metadata)."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO favorites (user_id, video_id, song_metadata_json)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, video_id) DO UPDATE SET
                    song_metadata_json = excluded.song_metadata_json
            """,
                (user_id, video_id, _encode_metadata(metadata)),
            )
            conn.commit()
            self.logger.info("Favorited video_id=%s for user %s", video_id, user_id)
        finally:
            conn.close()

    def remove(self, user_id: str, video_id: str) -> bool:
        """Unstar a song for a user."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM favorites WHERE user_id = ? AND video_id = ?",
                (user_id, video_id),
            )
            removed = cursor.rowcount > 0
            conn.commit()
            if removed:
                self.logger.info("Unfavorited video_id=%s for user %s", video_id, user_id)
            return removed
        finally:
            conn.close()

    def get_user_favorites(self, user_id: str) -> List[Favorite]:
        """Get all favorites for a user, newest first."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, video_id, song_metadata_json, created_at
                FROM favorites
                WHERE user_id = ?
                ORDER BY created_at DESC, rowid DESC
            """,
                (user_id,),
            )

            favorites = []
            for row in cursor.fetchall():
                favorites.append(
                    Favorite(
                        user_id=row["user_id"],
                        video_id=row["video_id"],
                        metadata=_decode_metadata(row["song_metadata_json"]),
                        created_at=datetime.fromisoformat(row["created_at"])
                        if row["created_at"]
                        else None,
                    )
                )
            return favorites
        finally:
            conn.close()


class QueueRepository:
    """Repository for queue operations."""

    STATUS_PENDING = "pending"
    STATUS_PREPARING = "preparing"
    STATUS_READY = "ready"
    STATUS_ERROR = "error"

    def __init__(self, database: Database):
        self.database = database
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _encode_content_info(content_info: Dict[str, Any]) -> str:
        """Encode content info to JSON for the download_json DB column."""
        return json.dumps(content_info)

    @staticmethod
    def _decode_content_info(download_json: Optional[str]) -> Dict[str, Any]:
        """Decode content info from the download_json DB column."""
        if not download_json:
            return {}
        try:
            return json.loads(download_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _row_get(row: sqlite3.Row, key: str, default=None):
        """Get value from sqlite3.Row with default, handling NULL values."""
        try:
            value = row[key]
            return value if value is not None else default
        except (KeyError, IndexError):
            return default

    def _row_to_queue_item(self, row: sqlite3.Row) -> QueueItem:
        """Convert a database row to a QueueItem."""
        content_info = self._decode_content_info(self._row_get(row, "download_json"))
        created_at = self._row_get(row, "created_at")
        return QueueItem(
            id=row["id"],
            position=row["position"],
            user_id=row["user_id"],
            user_name=row["user_name"],
            video_id=row["video_id"],
            metadata=_decode_metadata(row["song_metadata_json"]),
            settings=_decode_settings(row["settings_json"]),
            content_status=row["download_status"],
            content_path=content_info.get("download_path"),
            error_message=content_info.get("error_message"),
            created_at=datetime.fromisoformat(created_at) if created_at else None,
            session_id=self._row_get(row, "session_id"),
        )

    def add(
        self,
        user: User,
        video_id: str,
        metadata: SongMetadata,
        settings: SongSettings,
        session_id: Optional[int] = None,
    ) -> int:
        """Add a song to the end of the queue."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()

            # Get the highest position
            cursor.execute("SELECT MAX(position) as max_pos FROM queue_items")
            result = cursor.fetchone()
            next_position = (result["max_pos"] or 0) + 1

            # Insert new item
            cursor.execute(
                """
                INSERT INTO queue_items
                (position, user_id, user_name, video_id, song_metadata_json,
                 settings_json, download_status, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    next_position,
                    user.id,
                    user.display_name,
                    video_id,
                    _encode_metadata(metadata),
                    _encode_settings(settings),
                    self.STATUS_PENDING,
                    session_id,
                ),
            )

            item_id = cursor.lastrowid
            conn.commit()
            self.logger.info(
                "Added song to queue: %s by %s (ID: %s, video_id: %s)",
                metadata.title,
                user.display_name,
                item_id,
                video_id,
            )
            return item_id
        finally:
            conn.close()

    def replace(self, item_id: int, video_id: str, metadata: SongMetadata) -> bool:
        """
        Replace the video/metadata of an existing queue item, in place.

        Keeps position, user attribution, and settings unchanged. Resets
        download status so the new video is (re)downloaded, and clears any
        previous content path/error and extracted artist/song metadata.
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE queue_items
                SET video_id = ?, song_metadata_json = ?, download_status = ?, download_json = NULL
                WHERE id = ?
            """,
                (video_id, _encode_metadata(metadata), self.STATUS_PENDING, item_id),
            )

            if cursor.rowcount == 0:
                self.logger.warning("Queue item %s not found for replace", item_id)
                return False

            conn.commit()
            self.logger.info("Replaced queue item %s with video_id: %s", item_id, video_id)
            return True
        finally:
            conn.close()

    def remove(self, item_id: int) -> bool:
        """Remove a song from the queue."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()

            # Get position of item to remove
            cursor.execute("SELECT position FROM queue_items WHERE id = ?", (item_id,))
            result = cursor.fetchone()

            if not result:
                self.logger.warning("Queue item %s not found", item_id)
                return False

            removed_position = result["position"]

            # Delete the item
            cursor.execute("DELETE FROM queue_items WHERE id = ?", (item_id,))

            # Decrement positions of items after the removed one
            cursor.execute(
                """
                UPDATE queue_items
                SET position = position - 1
                WHERE position > ?
            """,
                (removed_position,),
            )

            conn.commit()
            self.logger.info("Removed queue item %s", item_id)
            return True
        finally:
            conn.close()

    def get_all(self) -> List[QueueItem]:
        """Get the entire queue ordered by position."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, position, user_id, user_name, video_id,
                       song_metadata_json, settings_json, download_json,
                       download_status, created_at, session_id
                FROM queue_items
                ORDER BY position
            """)

            items = []
            for row in cursor.fetchall():
                items.append(self._row_to_queue_item(row))

            return items
        finally:
            conn.close()

    def update_status(
        self,
        item_id: int,
        status: str,
        content_path: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Update content status for a queue item."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()

            cursor.execute("SELECT download_json FROM queue_items WHERE id = ?", (item_id,))
            result = cursor.fetchone()
            if not result:
                self.logger.warning("Queue item %s not found for status update", item_id)
                return False

            content_info = self._decode_content_info(result["download_json"])

            if content_path is not None:
                content_info["download_path"] = content_path
            if error_message is not None:
                content_info["error_message"] = error_message
            elif status != self.STATUS_ERROR:
                content_info.pop("error_message", None)

            cursor.execute(
                """
                UPDATE queue_items
                SET download_status = ?, download_json = ?
                WHERE id = ?
            """,
                (status, self._encode_content_info(content_info), item_id),
            )

            updated = cursor.rowcount > 0
            conn.commit()

            if updated:
                self.logger.debug("Updated content status for item %s: %s", item_id, status)
            else:
                self.logger.warning("Queue item %s not found for status update", item_id)

            return updated
        finally:
            conn.close()

    def reorder(self, item_id: int, new_position: int) -> bool:
        """Move a song to a new position in the queue."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()

            # Get current position
            cursor.execute("SELECT position FROM queue_items WHERE id = ?", (item_id,))
            result = cursor.fetchone()

            if not result:
                self.logger.warning("Queue item %s not found", item_id)
                return False

            old_position = result["position"]

            if old_position == new_position:
                self.logger.debug("Item %s already at position %s", item_id, new_position)
                return True

            # Get max position
            cursor.execute("SELECT MAX(position) as max_pos FROM queue_items")
            max_pos = cursor.fetchone()["max_pos"] or 0

            if new_position < 1 or new_position > max_pos:
                self.logger.warning("Invalid position %s (max: %s)", new_position, max_pos)
                return False

            # Shift items to make room
            if new_position > old_position:
                # Moving down: shift items up
                cursor.execute(
                    """
                    UPDATE queue_items
                    SET position = position - 1
                    WHERE position > ? AND position <= ?
                """,
                    (old_position, new_position),
                )
            else:
                # Moving up: shift items down
                cursor.execute(
                    """
                    UPDATE queue_items
                    SET position = position + 1
                    WHERE position >= ? AND position < ?
                """,
                    (new_position, old_position),
                )

            # Update the item's position
            cursor.execute(
                "UPDATE queue_items SET position = ? WHERE id = ?", (new_position, item_id)
            )

            conn.commit()
            self.logger.info(
                "Moved queue item %s from position %s to %s", item_id, old_position, new_position
            )
            return True
        finally:
            conn.close()

    def update_pitch(self, item_id: int, pitch_semitones: int) -> bool:
        """Update pitch adjustment for a queue item."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()

            # Get current settings to merge
            cursor.execute("SELECT settings_json FROM queue_items WHERE id = ?", (item_id,))
            result = cursor.fetchone()
            if not result:
                self.logger.warning("Queue item %s not found", item_id)
                return False

            settings = _decode_settings(result["settings_json"])
            settings.pitch_semitones = pitch_semitones

            # Update settings in queue item
            cursor.execute(
                """
                UPDATE queue_items
                SET settings_json = ?
                WHERE id = ?
            """,
                (_encode_settings(settings), item_id),
            )

            updated = cursor.rowcount > 0
            conn.commit()

            if updated:
                self.logger.debug(
                    "Updated pitch for item %s: %s semitones", item_id, pitch_semitones
                )
            else:
                self.logger.warning("Queue item %s not found", item_id)

            return updated
        finally:
            conn.close()

    def update_extracted_metadata(self, item_id: int, artist: str, song_name: str) -> bool:
        """Update extracted artist/song metadata for a queue item."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()

            # Get current metadata to merge
            cursor.execute("SELECT song_metadata_json FROM queue_items WHERE id = ?", (item_id,))
            result = cursor.fetchone()
            if not result:
                self.logger.warning("Queue item %s not found", item_id)
                return False

            metadata = _decode_metadata(result["song_metadata_json"])
            metadata.artist = artist
            metadata.song_name = song_name

            # Update metadata in queue item
            cursor.execute(
                """
                UPDATE queue_items
                SET song_metadata_json = ?
                WHERE id = ?
            """,
                (_encode_metadata(metadata), item_id),
            )

            updated = cursor.rowcount > 0
            conn.commit()

            if updated:
                self.logger.debug(
                    "Updated extracted metadata for item %s: '%s' by '%s'",
                    item_id,
                    song_name,
                    artist,
                )

            return updated
        finally:
            conn.close()

    def clear(self) -> int:
        """Clear all items from the queue."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM queue_items")
            count = cursor.fetchone()["count"]
            cursor.execute("DELETE FROM queue_items")
            conn.commit()
            self.logger.info("Cleared queue (%s items removed)", count)
            return count
        finally:
            conn.close()

    def get_item(self, item_id: int) -> Optional[QueueItem]:
        """Get a specific queue item by ID."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, position, user_id, user_name, video_id,
                       song_metadata_json, settings_json, download_json,
                       download_status, created_at, session_id
                FROM queue_items
                WHERE id = ?
            """,
                (item_id,),
            )

            result = cursor.fetchone()
            if not result:
                return None

            return self._row_to_queue_item(result)
        finally:
            conn.close()


class SessionRepository:
    """Repository for party session operations.

    A session represents a bounded period of karaoke activity, bookended
    by the clear-queue action. Queue items and playback history rows carry
    a session_id so future features can group per-party data.
    """

    def __init__(self, database: Database):
        self.database = database
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            theme=row["theme"],
        )

    def create(self, theme: Optional[str] = None) -> Session:
        """Create a new session with the given theme and return it."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO sessions (theme) VALUES (?)",
                (theme or None,),
            )
            session_id = cursor.lastrowid
            conn.commit()
            cursor.execute(
                "SELECT id, created_at, ended_at, theme FROM sessions WHERE id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            self.logger.info("Created session %s (theme=%r)", session_id, theme)
            return self._row_to_session(row)
        finally:
            conn.close()

    def get_current(self) -> Optional[Session]:
        """Return the most recent still-open session, if any."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, created_at, ended_at, theme
                FROM sessions
                WHERE ended_at IS NULL
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            return self._row_to_session(row) if row else None
        finally:
            conn.close()

    def get_by_id(self, session_id: int) -> Optional[Session]:
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, created_at, ended_at, theme FROM sessions WHERE id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            return self._row_to_session(row) if row else None
        finally:
            conn.close()

    def end(self, session_id: int) -> bool:
        """Mark a session ended. No-op if already ended. Returns True if it
        was open and is now closed, False otherwise."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sessions
                SET ended_at = CURRENT_TIMESTAMP
                WHERE id = ? AND ended_at IS NULL
                """,
                (session_id,),
            )
            updated = cursor.rowcount > 0
            conn.commit()
            if updated:
                self.logger.info("Ended session %s", session_id)
            return updated
        finally:
            conn.close()


class EventRepository:
    """Repository for user interaction events (search queries, etc.)."""

    def __init__(self, database: Database):
        self.database = database
        self.logger = logging.getLogger(__name__)

    def record(self, user_id: str, event_type: str, data: Optional[Dict[str, Any]] = None) -> int:
        """Record a user interaction event."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO user_events (user_id, event_type, data_json)
                VALUES (?, ?, ?)
            """,
                (user_id, event_type, json.dumps(data) if data else None),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
