"""User repository for kbox database."""

import logging
from datetime import datetime
from typing import List, Optional

from ..identity import normalize_name, pick_avatar
from ..models import User
from .schema import Database


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
        record (see docs/development/guest-identity.md — this system never
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
