"""Configuration repository for kbox database."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..models import ConfigEntry
from .schema import Database


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
