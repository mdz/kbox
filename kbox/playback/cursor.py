"""
The queue cursor: where we are in the queue.

Songs before the cursor's position are "played", songs after are "upcoming".
It is persisted to the config table so a restart mid-party resumes in the
right place rather than replaying the queue from the top.

QueueCursor owns the cached value and the persistence, and nothing else. It
takes a ConfigRepository, so it can be constructed and tested on its own.
"""

import logging
from typing import Optional

from ..database import ConfigRepository


class QueueCursor:
    """The ID of the song currently playing (or last played), persisted."""

    # Config key for persisting the cursor
    CONFIG_KEY = "queue_cursor_song_id"

    def __init__(self, config_repository: ConfigRepository):
        """
        Args:
            config_repository: repository for the config table the cursor is
                persisted to. The stored value is read once, here.
        """
        self._config_repository = config_repository
        self.logger = logging.getLogger(__name__)
        self._song_id: Optional[int] = self._load()

    def _load(self) -> Optional[int]:
        """Load cursor song ID from persistent storage."""
        entry = self._config_repository.get(self.CONFIG_KEY)
        if entry and entry.value:
            try:
                return int(entry.value)
            except (ValueError, TypeError):
                return None
        return None

    def _save(self, song_id: Optional[int]) -> None:
        """Persist cursor song ID to storage."""
        self._config_repository.set(self.CONFIG_KEY, str(song_id) if song_id is not None else "")

    def set(self, song_id: int) -> None:
        """
        Set the queue cursor to a song.

        The cursor tracks where we are in the queue. Songs before the cursor's
        position are "played", songs after are "upcoming".

        Called by _play_song() whenever a song starts playing.

        Args:
            song_id: ID of the song now playing
        """
        self._song_id = song_id
        self._save(song_id)
        self.logger.debug("Queue cursor set to song ID %s", song_id)

    def get(self) -> Optional[int]:
        """Get the current cursor song ID."""
        return self._song_id

    def clear(self) -> None:
        """Clear the queue cursor (e.g., when queue is cleared)."""
        self._song_id = None
        self._save(None)
        self.logger.debug("Queue cursor cleared")
