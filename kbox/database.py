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

from .models import ConfigEntry, HistoryRecord, QueueItem, SongMetadata, SongSettings, User


class Database:
    """Manages SQLite database connection and schema."""

    # Schema version for migrations
    SCHEMA_VERSION = 3  # Incremented for song_metadata_cache table

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
                    performance_json TEXT NOT NULL
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
# Repository Classes
# ============================================================================


class UserRepository:
    """Repository for user operations."""

    def __init__(self, database: Database):
        self.database = database
        self.logger = logging.getLogger(__name__)

    def get_by_id(self, user_id: str) -> Optional[User]:
        """Get a user by ID."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, display_name, created_at FROM users WHERE id = ?", (user_id,)
            )
            row = cursor.fetchone()
            if row:
                return User(
                    id=row["id"],
                    display_name=row["display_name"],
                    created_at=datetime.fromisoformat(row["created_at"])
                    if row["created_at"]
                    else None,
                )
            return None
        finally:
            conn.close()

    def create(self, user_id: str, display_name: str) -> User:
        """Create a new user."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (id, display_name) VALUES (?, ?)", (user_id, display_name)
            )
            conn.commit()

            # Fetch the created record
            cursor.execute(
                "SELECT id, display_name, created_at FROM users WHERE id = ?", (user_id,)
            )
            row = cursor.fetchone()
            self.logger.info("Created new user: %s (%s)", display_name, user_id)

            return User(
                id=row["id"],
                display_name=row["display_name"],
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            )
        finally:
            conn.close()

    def update_display_name(self, user_id: str, display_name: str) -> bool:
        """Update a user's display name."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET display_name = ? WHERE id = ?", (display_name, user_id)
            )
            updated = cursor.rowcount > 0
            conn.commit()
            if updated:
                self.logger.info("Updated display name for user %s: %s", user_id, display_name)
            return updated
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
    def _encode_metadata(metadata: SongMetadata) -> str:
        """Encode metadata to JSON."""
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

    @staticmethod
    def _decode_metadata(metadata_json: str) -> SongMetadata:
        """Decode metadata from JSON."""
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

    @staticmethod
    def _encode_settings(settings: SongSettings) -> str:
        """Encode settings to JSON."""
        return json.dumps({"pitch_semitones": settings.pitch_semitones})

    @staticmethod
    def _decode_settings(settings_json: str) -> SongSettings:
        """Decode settings from JSON."""
        if not settings_json:
            return SongSettings()
        try:
            data = json.loads(settings_json)
            return SongSettings(pitch_semitones=data.get("pitch_semitones", 0))
        except (json.JSONDecodeError, TypeError):
            return SongSettings()

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
                    performance_json
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    user_id,
                    user_name,
                    video_id,
                    self._encode_metadata(metadata),
                    self._encode_settings(settings),
                    self._encode_performance(performance),
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
                return self._decode_settings(result["settings_json"])
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
                    performance_json
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
                        metadata=self._decode_metadata(row["song_metadata_json"]),
                        settings=self._decode_settings(row["settings_json"]),
                        performance=self._decode_performance(row["performance_json"]),
                        performed_at=datetime.fromisoformat(row["performed_at"])
                        if row["performed_at"]
                        else None,
                    )
                )
            return records
        finally:
            conn.close()


class QueueRepository:
    """Repository for queue operations."""

    # Download status constants
    STATUS_PENDING = "pending"
    STATUS_DOWNLOADING = "downloading"
    STATUS_READY = "ready"
    STATUS_ERROR = "error"

    def __init__(self, database: Database):
        self.database = database
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _encode_metadata(metadata: SongMetadata) -> str:
        """Encode metadata to JSON."""
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

    @staticmethod
    def _decode_metadata(metadata_json: str) -> SongMetadata:
        """Decode metadata from JSON."""
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

    @staticmethod
    def _encode_settings(settings: SongSettings) -> str:
        """Encode settings to JSON."""
        return json.dumps({"pitch_semitones": settings.pitch_semitones})

    @staticmethod
    def _decode_settings(settings_json: str) -> SongSettings:
        """Decode settings from JSON."""
        if not settings_json:
            return SongSettings()
        try:
            data = json.loads(settings_json)
            return SongSettings(pitch_semitones=data.get("pitch_semitones", 0))
        except (json.JSONDecodeError, TypeError):
            return SongSettings()

    @staticmethod
    def _encode_download_info(download_info: Dict[str, Any]) -> str:
        """Encode download info to JSON."""
        return json.dumps(download_info)

    @staticmethod
    def _decode_download_info(download_json: Optional[str]) -> Dict[str, Any]:
        """Decode download info from JSON."""
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
        download_json = self._row_get(row, "download_json")
        download_info = self._decode_download_info(download_json)
        created_at = self._row_get(row, "created_at")
        return QueueItem(
            id=row["id"],
            position=row["position"],
            user_id=row["user_id"],
            user_name=row["user_name"],
            video_id=row["video_id"],
            metadata=self._decode_metadata(row["song_metadata_json"]),
            settings=self._decode_settings(row["settings_json"]),
            download_status=row["download_status"],
            download_path=download_info.get("download_path"),
            error_message=download_info.get("error_message"),
            created_at=datetime.fromisoformat(created_at) if created_at else None,
        )

    def add(
        self,
        user: User,
        video_id: str,
        metadata: SongMetadata,
        settings: SongSettings,
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
                 settings_json, download_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    next_position,
                    user.id,
                    user.display_name,
                    video_id,
                    self._encode_metadata(metadata),
                    self._encode_settings(settings),
                    self.STATUS_PENDING,
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
                       download_status, created_at
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
        download_path: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Update download status for a queue item."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()

            # Get current download_json to merge updates
            cursor.execute("SELECT download_json FROM queue_items WHERE id = ?", (item_id,))
            result = cursor.fetchone()
            if not result:
                self.logger.warning("Queue item %s not found for status update", item_id)
                return False

            download_info = self._decode_download_info(result["download_json"])

            # Update download info
            if download_path is not None:
                download_info["download_path"] = download_path
            if error_message is not None:
                download_info["error_message"] = error_message
            elif status != self.STATUS_ERROR:
                # Clear error message if status is not error
                download_info.pop("error_message", None)

            # Update database
            cursor.execute(
                """
                UPDATE queue_items
                SET download_status = ?, download_json = ?
                WHERE id = ?
            """,
                (status, self._encode_download_info(download_info), item_id),
            )

            updated = cursor.rowcount > 0
            conn.commit()

            if updated:
                self.logger.debug("Updated download status for item %s: %s", item_id, status)
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

            settings = self._decode_settings(result["settings_json"])
            settings.pitch_semitones = pitch_semitones

            # Update settings in queue item
            cursor.execute(
                """
                UPDATE queue_items
                SET settings_json = ?
                WHERE id = ?
            """,
                (self._encode_settings(settings), item_id),
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

            metadata = self._decode_metadata(result["song_metadata_json"])
            metadata.artist = artist
            metadata.song_name = song_name

            # Update metadata in queue item
            cursor.execute(
                """
                UPDATE queue_items
                SET song_metadata_json = ?
                WHERE id = ?
            """,
                (self._encode_metadata(metadata), item_id),
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
                       download_status, created_at
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
