"""Playback history repository for kbox database."""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..models import HistoryRecord, SongMetadata, SongSettings
from .codecs import _decode_metadata, _decode_settings, _encode_metadata, _encode_settings
from .schema import Database


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
