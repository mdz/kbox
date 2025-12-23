"""
Playback history management.

Maintains a permanent record of performances, including settings used
and playback metrics.
"""

import logging
from typing import List, Optional

from .database import Database, HistoryRepository
from .models import HistoryRecord, SongMetadata, SongSettings


class HistoryManager:
    """
    Manages playback history.

    Records performances, retrieves past settings, and provides history queries.
    Operates independently from the queue - history is permanent, queue is ephemeral.
    """

    def __init__(self, database: Database):
        """
        Initialize history manager.

        Args:
            database: Database instance for persistence
        """
        self.database = database
        self.repository = HistoryRepository(database)
        self.logger = logging.getLogger(__name__)

    def record_performance(
        self,
        user_id: str,
        user_name: str,
        source: str,
        source_id: str,
        metadata: SongMetadata,
        settings: SongSettings,
        played_duration_seconds: int,
        playback_end_position_seconds: int,
        completion_percentage: float,
    ) -> int:
        """
        Record a performance in history.

        Args:
            user_id: User ID
            user_name: User display name
            source: Source type (e.g., 'youtube')
            source_id: Source-specific identifier
            metadata: Song metadata
            settings: Song settings
            played_duration_seconds: How long the song played
            playback_end_position_seconds: Position where playback ended
            completion_percentage: Percentage of song completed

        Returns:
            ID of the created history record
        """
        # Build performance metrics
        performance = {
            "played_duration_seconds": played_duration_seconds,
            "playback_end_position_seconds": playback_end_position_seconds,
            "completion_percentage": completion_percentage,
        }

        history_id = self.repository.record(
            user_id=user_id,
            user_name=user_name,
            source=source,
            source_id=source_id,
            metadata=metadata,
            settings=settings,
            performance=performance,
        )

        self.logger.info(
            "Recorded history: %s sang %s (source=%s, id=%s, %.1f%% complete)",
            user_name,
            metadata.title,
            source,
            source_id,
            completion_percentage,
        )

        return history_id

    def get_last_settings(
        self, source: str, source_id: str, user_id: str
    ) -> Optional[SongSettings]:
        """
        Get the last used settings for a song from playback history for a specific user.

        Args:
            source: Source type (e.g., 'youtube')
            source_id: Source-specific identifier
            user_id: User ID

        Returns:
            SongSettings if found, None otherwise
        """
        return self.repository.get_last_settings(source, source_id, user_id)

    def get_user_history(self, user_id: str, limit: int = 50) -> List[HistoryRecord]:
        """
        Get playback history for a specific user.

        Args:
            user_id: User ID
            limit: Maximum number of records to return (default 50)

        Returns:
            List of history records
        """
        return self.repository.get_user_history(user_id, limit)
