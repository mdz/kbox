"""User interaction event repository for kbox database."""

import json
import logging
from dataclasses import asdict

from ..user_events import UserEvent
from .schema import Database


class EventRepository:
    """Repository for user interaction events (see kbox/user_events.py)."""

    def __init__(self, database: Database):
        self.database = database
        self.logger = logging.getLogger(__name__)

    def record(self, user_id: str, event: UserEvent) -> int:
        """Record a user interaction event, returning its row id."""
        data = asdict(event)
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO user_events (user_id, event_type, data_json)
                VALUES (?, ?, ?)
            """,
                (user_id, event.event_type, json.dumps(data) if data else None),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
