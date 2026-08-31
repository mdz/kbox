"""Party session repository for kbox database."""

import logging
import sqlite3
from datetime import datetime
from typing import Optional

from ..models import Session
from .schema import Database


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
