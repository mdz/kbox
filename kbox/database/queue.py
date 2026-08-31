"""Queue repository for kbox database."""

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..models import QueueItem, SongMetadata, SongSettings, User
from .codecs import _decode_metadata, _decode_settings, _encode_metadata, _encode_settings
from .schema import Database


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
