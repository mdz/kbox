"""
Session management for kbox.

A session bookends a karaoke party. The current session is whichever
session has `ended_at IS NULL` (most recent by created_at). Clearing the
queue ends the current session and starts a new one.

Consumers get the active session via `SessionManager.get_or_create_current()`
which lazily creates one on first call. Queue adds and history records
attach `session_id` to their rows so future features (history-by-party,
stats) can group data without re-deriving party boundaries.
"""

import logging
import threading
from typing import TYPE_CHECKING, Optional

from .database import Database, SessionRepository
from .models import Session

if TYPE_CHECKING:
    from .config_manager import ConfigManager


class SessionManager:
    """Manages the lifecycle of party sessions."""

    def __init__(
        self,
        database: Database,
        config_manager: Optional["ConfigManager"] = None,
    ):
        """
        Args:
            database: Database instance.
            config_manager: Used to read the current party theme when
                starting a new session. If None, sessions are started
                without a theme.
        """
        self.database = database
        self.repository = SessionRepository(database)
        self.config_manager = config_manager
        self.logger = logging.getLogger(__name__)
        self._lock = threading.Lock()

    def _current_theme(self) -> Optional[str]:
        if self.config_manager is None:
            return None
        theme = self.config_manager.get("suggestion_theme")
        return theme or None

    def get_or_create_current(self) -> Session:
        """Return the current open session, creating one if none exists."""
        with self._lock:
            current = self.repository.get_current()
            if current is not None:
                return current
            return self.repository.create(theme=self._current_theme())

    def get_current(self) -> Optional[Session]:
        """Return the current open session, or None if none exists."""
        return self.repository.get_current()

    def end_and_rotate(self) -> Session:
        """End the current session (if any) and start a fresh one.

        The new session snapshots the current configured party theme.
        Returns the newly-created session.
        """
        with self._lock:
            current = self.repository.get_current()
            if current is not None:
                self.repository.end(current.id)
            return self.repository.create(theme=self._current_theme())
