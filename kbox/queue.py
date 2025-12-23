"""
Queue management for kbox.

Handles song queue operations with persistence and download management.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List, Optional

from .database import Database, QueueRepository, UserRepository
from .models import QueueItem, SongMetadata, SongSettings, User

if TYPE_CHECKING:
    from .youtube import YouTubeClient


class QueueManager:
    """Manages the song queue with persistence and downloads."""

    # Download status constants
    STATUS_PENDING = "pending"
    STATUS_DOWNLOADING = "downloading"
    STATUS_READY = "ready"
    STATUS_ERROR = "error"

    def __init__(self, database: Database, youtube_client: Optional["YouTubeClient"] = None):
        """
        Initialize QueueManager.

        Args:
            database: Database instance for persistence
            youtube_client: YouTubeClient for downloading videos (optional)
        """
        self.database = database
        self.repository = QueueRepository(database)
        self.user_repository = UserRepository(database)
        self.youtube_client = youtube_client
        self.logger = logging.getLogger(__name__)

        # Download monitoring
        self._download_timeout = timedelta(minutes=10)
        self._download_monitor_thread = None
        self._monitoring = False

        # Start download monitor if youtube_client provided
        if self.youtube_client:
            self._start_download_monitor()

    # =========================================================================
    # Download Monitoring
    # =========================================================================

    def _start_download_monitor(self):
        """Start background thread to monitor queue and trigger downloads."""
        if self._monitoring:
            return

        self._monitoring = True

        def monitor():
            while self._monitoring:
                try:
                    self._process_download_queue()
                    # Sleep before next check
                    threading.Event().wait(2.0)
                except Exception as e:
                    self.logger.error("Error in download monitor: %s", e, exc_info=True)
                    threading.Event().wait(5.0)  # Wait longer on error

        self._download_monitor_thread = threading.Thread(
            target=monitor, daemon=True, name="DownloadMonitor"
        )
        self._download_monitor_thread.start()
        self.logger.info("Download monitor started")

    def _process_download_queue(self):
        """Process pending and stuck downloads."""
        queue = self.get_queue()

        for item in queue:
            if item.download_status == self.STATUS_PENDING:
                self._start_download(item)
            elif item.download_status == self.STATUS_DOWNLOADING:
                self._check_stuck_download(item)

    def _start_download(self, item: QueueItem):
        """Start downloading a queue item."""
        self.logger.info("Starting download for %s (ID: %s)", item.metadata.title, item.id)

        item_id = item.id

        def on_status(status: str, path: Optional[str], error: Optional[str]):
            self._on_download_status(item_id, status, path, error)

        # For YouTube source, use source_id (video ID)
        if item.source == "youtube":
            self.youtube_client.download_video(item.source_id, item.id, status_callback=on_status)
        else:
            self.logger.error("Unsupported source type: %s", item.source)
            self.update_download_status(
                item.id, self.STATUS_ERROR, error_message=f"Unsupported source: {item.source}"
            )
            return

        # Update status to downloading
        self.update_download_status(item.id, self.STATUS_DOWNLOADING)

    def _check_stuck_download(self, item: QueueItem):
        """Check if a download is stuck and recover if possible."""
        # First, check if file exists (download completed but callback failed)
        # Only works for YouTube source currently
        if item.source == "youtube":
            download_path = self.youtube_client.get_download_path(item.source_id)
            if download_path and download_path.exists():
                self.logger.info(
                    "Found completed download for %s (ID: %s), updating status",
                    item.metadata.title,
                    item.id,
                )
                self.update_download_status(
                    item.id, self.STATUS_READY, download_path=str(download_path)
                )
                return

        # Check if download has been stuck for too long
        if item.created_at:
            if datetime.now(item.created_at.tzinfo) - item.created_at > self._download_timeout:
                self.logger.warning(
                    "Download stuck for %s (ID: %s) for more than %s, resetting to pending",
                    item.metadata.title,
                    item.id,
                    self._download_timeout,
                )
                self.update_download_status(item.id, self.STATUS_PENDING)

    def _on_download_status(
        self, item_id: int, status: str, path: Optional[str], error: Optional[str]
    ):
        """Callback for download status updates."""
        if status == "ready" and path:
            self.update_download_status(item_id, self.STATUS_READY, download_path=path)
            self.logger.info("Download complete for queue item %s: %s", item_id, path)
        elif status == "error" and error:
            self.update_download_status(item_id, self.STATUS_ERROR, error_message=error)
            self.logger.error("Download failed for queue item %s: %s", item_id, error)

    def stop_download_monitor(self):
        """Stop the download monitor thread."""
        if not self._monitoring:
            return

        self.logger.info("Stopping download monitor...")
        self._monitoring = False

        if self._download_monitor_thread and self._download_monitor_thread.is_alive():
            self._download_monitor_thread.join(timeout=2.0)
            if self._download_monitor_thread.is_alive():
                self.logger.warning("Download monitor thread did not stop within timeout")

        self.logger.info("Download monitor stopped")

    # =========================================================================
    # Queue Operations
    # =========================================================================

    def add_song(
        self,
        user: User,
        source: str,
        source_id: str,
        title: str,
        duration_seconds: Optional[int] = None,
        thumbnail_url: Optional[str] = None,
        channel: Optional[str] = None,
        pitch_semitones: int = 0,
    ) -> int:
        """
        Add a song to the end of the queue.

        Args:
            user: User who requested the song
            source: Source type (e.g., 'youtube')
            source_id: Source-specific identifier (e.g., video ID)
            title: Song title
            duration_seconds: Duration in seconds (optional)
            thumbnail_url: Thumbnail URL (optional)
            channel: Channel/artist name (optional)
            pitch_semitones: Pitch adjustment in semitones (default 0)

        Returns:
            ID of the created queue item
        """
        metadata = SongMetadata(
            title=title,
            duration_seconds=duration_seconds,
            thumbnail_url=thumbnail_url,
            channel=channel,
        )
        settings = SongSettings(pitch_semitones=pitch_semitones)

        item_id = self.repository.add(
            user=user, source=source, source_id=source_id, metadata=metadata, settings=settings
        )

        self.logger.info(
            "Added song to queue: %s by %s (ID: %s, source: %s, pitch: %s)",
            title,
            user.display_name,
            item_id,
            source,
            pitch_semitones,
        )
        return item_id

    def remove_song(self, item_id: int) -> bool:
        """Remove a song from the queue."""
        return self.repository.remove(item_id)

    def reorder_song(self, item_id: int, new_position: int) -> bool:
        """Move a song to a new position in the queue."""
        return self.repository.reorder(item_id, new_position)

    def get_queue(self, include_played: bool = True) -> List[QueueItem]:
        """Get the entire queue ordered by position."""
        return self.repository.get_all(include_played=include_played)

    def get_next_song(self) -> Optional[QueueItem]:
        """Get the next ready song in the queue."""
        return self.repository.get_next_ready()

    def get_next_song_after(self, current_song_id: int) -> Optional[QueueItem]:
        """Get the next ready song in the queue after the specified song."""
        return self.repository.get_next_after(current_song_id)

    def get_previous_song_before(self, current_song_id: int) -> Optional[QueueItem]:
        """Get the previous ready song in the queue before the specified song."""
        return self.repository.get_previous_before(current_song_id)

    def clear_queue(self) -> int:
        """Clear all items from the queue."""
        return self.repository.clear()

    def update_download_status(
        self,
        item_id: int,
        status: str,
        download_path: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Update download status for a queue item."""
        return self.repository.update_status(item_id, status, download_path, error_message)

    def mark_played(self, item_id: int) -> bool:
        """Mark a queue item as played."""
        return self.repository.mark_played(item_id)

    def get_item(self, item_id: int) -> Optional[QueueItem]:
        """Get a specific queue item by ID."""
        return self.repository.get_item(item_id)

    def update_pitch(self, item_id: int, pitch_semitones: int) -> bool:
        """Update pitch adjustment for a queue item."""
        return self.repository.update_pitch(item_id, pitch_semitones)
