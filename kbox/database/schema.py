"""
Database schema module for kbox.

Handles SQLite database initialization, schema creation, and connection management.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from ..identity import normalize_name, pick_avatar


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
