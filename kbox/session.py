"""
Session management for kbox.

A session bookends a karaoke party. The current session is whichever
session has `ended_at IS NULL` (most recent by created_at). Clearing the
queue ends the current session and starts a new one.

Consumers get the active session via `SessionManager.get_or_create_current()`
which lazily creates one on first call. Queue adds and history records
attach `session_id` to their rows so future features (history-by-party,
stats) can group data without re-deriving party boundaries.

The guest `access_token` (the secret in the QR code URL) is rotated as
part of this same ritual: a new party should not still be reachable via
the previous party's link.
"""

import logging
import secrets
import threading
from typing import TYPE_CHECKING, Callable, Optional

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
        on_token_rotated: Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            database: Database instance.
            config_manager: Used to read the current party theme when
                starting a new session, and to persist the rotated access
                token. If None, sessions are started without a theme and
                the access token is not rotated.
            on_token_rotated: Called with the new access token after
                end_and_rotate() rotates it (e.g. to regenerate the QR
                code overlay live). Not called if there's no config_manager.
        """
        self.database = database
        self.repository = SessionRepository(database)
        self.config_manager = config_manager
        self.on_token_rotated = on_token_rotated
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
        The guest access token is also rotated so the previous party's
        QR code/link stops working. Returns the newly-created session.
        """
        with self._lock:
            current = self.repository.get_current()
            if current is not None:
                self.repository.end(current.id)
            new_session = self.repository.create(theme=self._current_theme())
            new_token = self._rotate_access_token()

        # Notify outside the lock: regenerating the QR overlay can involve
        # file I/O / GStreamer property sets that shouldn't hold up other
        # session operations.
        if new_token is not None and self.on_token_rotated is not None:
            try:
                self.on_token_rotated(new_token)
            except Exception:
                self.logger.exception("on_token_rotated callback failed")

        return new_session

    def _rotate_access_token(self) -> Optional[str]:
        """Generate and persist a fresh guest access token. Returns it, or
        None if there's no config_manager to persist it to."""
        if self.config_manager is None:
            return None
        new_token = secrets.token_urlsafe(16)
        self.config_manager.set("access_token", new_token)
        self.logger.info("Rotated guest access token for new party session")
        return new_token
