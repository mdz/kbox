"""Favorite (starred) song repository for kbox database."""

import logging
from datetime import datetime
from typing import List

from ..models import Favorite, SongMetadata
from .codecs import _decode_metadata, _encode_metadata
from .schema import Database


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
