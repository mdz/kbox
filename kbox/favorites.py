"""
Favorites (starred songs) management.

Lets a user bookmark a song they might want to sing sometime, independent of
and without affecting the queue.
"""

import logging
from typing import List

from .database import Database, FavoriteRepository
from .models import Favorite, SongMetadata


class FavoritesManager:
    """
    Manages a user's favorited songs.

    Favorites are purely a personal bookmark - starring a song never touches
    the queue, and favorites are private to the user who starred them.
    """

    def __init__(self, database: Database):
        """
        Initialize favorites manager.

        Args:
            database: Database instance for persistence
        """
        self.database = database
        self.repository = FavoriteRepository(database)
        self.logger = logging.getLogger(__name__)

    def add_favorite(self, user_id: str, video_id: str, metadata: SongMetadata) -> None:
        """Star a song for a user."""
        self.repository.add(user_id, video_id, metadata)

    def remove_favorite(self, user_id: str, video_id: str) -> bool:
        """Unstar a song for a user. Returns False if it wasn't favorited."""
        return self.repository.remove(user_id, video_id)

    def get_user_favorites(self, user_id: str) -> List[Favorite]:
        """Get all favorites for a user, newest first."""
        return self.repository.get_user_favorites(user_id)
