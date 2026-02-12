"""
Queue management for kbox.

Handles song queue operations with persistence and download management.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List, Optional

from .database import ConfigRepository, Database, QueueRepository, UserRepository
from .models import QueueItem, SongMetadata, SongSettings, User


class DuplicateSongError(Exception):
    """Raised when attempting to add a song that's already in the queue."""

    pass


if TYPE_CHECKING:
    from .song_metadata import SongMetadataExtractor
    from .video_library import VideoLibrary


class QueueManager:
    """Manages the song queue with persistence and downloads."""

    # Download status constants
    STATUS_PENDING = "pending"
    STATUS_DOWNLOADING = "downloading"
    STATUS_READY = "ready"
    STATUS_ERROR = "error"

    # Config key for persisting the cursor
    _CURSOR_CONFIG_KEY = "queue_cursor_song_id"

    def __init__(
        self,
        database: Database,
        video_library: "VideoLibrary",
        metadata_extractor: Optional["SongMetadataExtractor"] = None,
    ):
        """
        Initialize QueueManager.

        Args:
            database: Database instance for persistence
            video_library: VideoLibrary for video search/download
            metadata_extractor: Optional SongMetadataExtractor for LLM-based extraction
        """
        self.database = database
        self.repository = QueueRepository(database)
        self.user_repository = UserRepository(database)
        self._config_repository = ConfigRepository(database)
        self.video_library = video_library
        self.metadata_extractor = metadata_extractor
        self.logger = logging.getLogger(__name__)

        # Queue cursor: the ID of the song currently playing (or last played).
        # Songs before the cursor's position are "played", songs after are "upcoming".
        # Persisted to the config table for restart resilience.
        self._cursor_song_id: Optional[int] = self._load_cursor()

        # Download monitoring
        self._download_timeout = timedelta(minutes=10)
        self._download_monitor_thread = None
        self._monitoring = False
        self._stop_event = threading.Event()  # Used to wake monitor thread on stop

        # Start download monitor
        self._start_download_monitor()

    # =========================================================================
    # Queue Cursor
    # =========================================================================

    def _load_cursor(self) -> Optional[int]:
        """Load cursor song ID from persistent storage."""
        entry = self._config_repository.get(self._CURSOR_CONFIG_KEY)
        if entry and entry.value:
            try:
                return int(entry.value)
            except (ValueError, TypeError):
                return None
        return None

    def _save_cursor(self, song_id: Optional[int]) -> None:
        """Persist cursor song ID to storage."""
        self._config_repository.set(
            self._CURSOR_CONFIG_KEY, str(song_id) if song_id is not None else ""
        )

    def set_cursor(self, song_id: int) -> None:
        """
        Set the queue cursor to a song.

        The cursor tracks where we are in the queue. Songs before the cursor's
        position are "played", songs after are "upcoming".

        Called by PlaybackController._play_song() whenever a song starts playing.

        Args:
            song_id: ID of the song now playing
        """
        self._cursor_song_id = song_id
        self._save_cursor(song_id)
        self.logger.debug("Queue cursor set to song ID %s", song_id)

    def get_cursor(self) -> Optional[int]:
        """Get the current cursor song ID."""
        return self._cursor_song_id

    def get_cursor_position(self) -> Optional[int]:
        """
        Get the position of the cursor song in the queue.

        Returns:
            Position of the cursor song, or None if cursor is unset or song not found.
        """
        if self._cursor_song_id is None:
            return None
        song = self.repository.get_item(self._cursor_song_id)
        return song.position if song else None

    def clear_cursor(self) -> None:
        """Clear the queue cursor (e.g., when queue is cleared)."""
        self._cursor_song_id = None
        self._save_cursor(None)
        self.logger.debug("Queue cursor cleared")

    # =========================================================================
    # Download Monitoring
    # =========================================================================

    def _start_download_monitor(self):
        """Start background thread to monitor queue and trigger downloads."""
        if self._monitoring:
            return

        self._monitoring = True
        self._stop_event.clear()

        def monitor():
            while self._monitoring:
                try:
                    self._process_download_queue()
                    # Sleep before next check (wakes immediately if stop_event is set)
                    self._stop_event.wait(2.0)
                except Exception as e:
                    self.logger.error("Error in download monitor: %s", e, exc_info=True)
                    self._stop_event.wait(5.0)  # Wait longer on error

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

        # Use video library to request the video
        # The callback will handle all status updates (downloading, ready, error)
        cached_path = self.video_library.request(item.video_id, callback=on_status)

        if cached_path:
            # Already cached - callback already marked as ready
            pass

    def _check_stuck_download(self, item: QueueItem):
        """Check if a download is stuck and recover if possible."""
        # Check if file exists (download completed but callback failed)
        cached_path = self.video_library.get_path(item.video_id)
        if cached_path and cached_path.exists():
            self.logger.info(
                "Found completed download for %s (ID: %s), updating status",
                item.metadata.title,
                item.id,
            )
            self.update_download_status(item.id, self.STATUS_READY, download_path=str(cached_path))
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
        if status == "downloading":
            self.update_download_status(item_id, self.STATUS_DOWNLOADING)
        elif status == "ready" and path:
            self.update_download_status(item_id, self.STATUS_READY, download_path=path)
            self.logger.info("Download complete for queue item %s: %s", item_id, path)
            # Trigger storage cleanup after successful download
            self._cleanup_storage()
        elif status == "error" and error:
            self.update_download_status(item_id, self.STATUS_ERROR, error_message=error)
            self.logger.error("Download failed for queue item %s: %s", item_id, error)

    def stop_download_monitor(self):
        """Stop the download monitor thread."""
        if not self._monitoring:
            return

        self.logger.info("Stopping download monitor...")
        self._monitoring = False
        self._stop_event.set()  # Wake the thread if it's sleeping

        if self._download_monitor_thread and self._download_monitor_thread.is_alive():
            self._download_monitor_thread.join(timeout=0.5)
            if self._download_monitor_thread.is_alive():
                self.logger.warning("Download monitor thread did not stop within timeout")

        self.logger.info("Download monitor stopped")

    def _cleanup_storage(self) -> None:
        """Trigger storage cleanup with queue items protected from eviction.

        Protects files for songs at or after the cursor position (current + upcoming).
        Songs before the cursor are eligible for eviction.
        """
        try:
            cursor_position = self.get_cursor_position()
            queue = self.repository.get_all()
            # Protect current song and all upcoming songs
            protected = {
                item.video_id
                for item in queue
                if cursor_position is None or item.position >= cursor_position
            }
            self.video_library.manage_storage(protected)
        except Exception as e:
            self.logger.error("Error during storage cleanup: %s", e, exc_info=True)

    # =========================================================================
    # Queue Operations
    # =========================================================================

    def add_song(
        self,
        user: User,
        video_id: str,
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
            video_id: Opaque video ID (e.g., "youtube:abc123")
            title: Song title (original video title)
            duration_seconds: Duration in seconds (optional)
            thumbnail_url: Thumbnail URL (optional)
            channel: Channel/artist name (optional)
            pitch_semitones: Pitch adjustment in semitones (default 0)

        Returns:
            ID of the created queue item

        Raises:
            DuplicateSongError: If the song is already in the queue
        """
        # Check for duplicate by video_id (only among upcoming songs)
        cursor_position = self.get_cursor_position()
        if self.repository.is_video_in_queue(video_id, after_position=cursor_position):
            raise DuplicateSongError(f"Song is already in the queue: {title}")

        metadata = SongMetadata(
            title=title,
            duration_seconds=duration_seconds,
            thumbnail_url=thumbnail_url,
            channel=channel,
        )
        settings = SongSettings(pitch_semitones=pitch_semitones)

        item_id = self.repository.add(
            user=user, video_id=video_id, metadata=metadata, settings=settings
        )

        self.logger.info(
            "Added song to queue: %s by %s (ID: %s, video_id: %s, pitch: %s)",
            title,
            user.display_name,
            item_id,
            video_id,
            pitch_semitones,
        )

        # Trigger async metadata extraction (if extractor configured)
        if self.metadata_extractor:
            self._start_metadata_extraction(item_id, video_id, title, channel)

        return item_id

    def _start_metadata_extraction(
        self,
        item_id: int,
        video_id: str,
        title: str,
        channel: Optional[str],
    ) -> None:
        """Start background thread to extract metadata for a queue item."""

        def extract_thread():
            try:
                artist, song_name = self.metadata_extractor.extract(
                    video_id=video_id,
                    title=title,
                    description=None,
                    channel=channel,
                )
                if artist and song_name:
                    self.update_extracted_metadata(item_id, artist, song_name)
                    self.logger.info(
                        "Extracted metadata for item %s: '%s' by '%s'",
                        item_id,
                        song_name,
                        artist,
                    )
            except Exception as e:
                self.logger.warning("Metadata extraction failed for item %s: %s", item_id, e)

        thread = threading.Thread(
            target=extract_thread, daemon=True, name=f"MetadataExtract-{item_id}"
        )
        thread.start()

    def remove_song(self, item_id: int) -> bool:
        """Remove a song from the queue."""
        return self.repository.remove(item_id)

    def reorder_song(self, item_id: int, new_position: int) -> bool:
        """Move a song to a new position in the queue."""
        return self.repository.reorder(item_id, new_position)

    def get_queue(self) -> List[QueueItem]:
        """Get the entire queue ordered by position."""
        return self.repository.get_all()

    def get_ready_song_at_offset(
        self, from_song_id: Optional[int], offset: int
    ) -> Optional[QueueItem]:
        """
        Get a ready (downloaded) song at an offset from a reference song.

        Navigation helper - finds songs relative to a position in the queue.
        Only considers songs with download_status == STATUS_READY.

        Args:
            from_song_id: Reference song ID (None = start from beginning/end)
            offset: +1 for next, -1 for previous, 0 for first ready

        Returns:
            The ready song at the offset, or None if not found
        """
        queue = self.repository.get_all()
        ready_songs = [item for item in queue if item.download_status == self.STATUS_READY]

        if not ready_songs:
            return None

        if from_song_id is None:
            # No reference - return first (offset >= 0) or last (offset < 0)
            return ready_songs[0] if offset >= 0 else ready_songs[-1]

        # Find reference song in the ready list
        current_idx = next((i for i, s in enumerate(ready_songs) if s.id == from_song_id), None)

        if current_idx is not None:
            # Reference song is in the ready list - use simple index offset
            target_idx = current_idx + offset
            if 0 <= target_idx < len(ready_songs):
                return ready_songs[target_idx]
            return None

        # Reference song not in ready list (deleted or not ready) - find by position
        ref_song = next((s for s in queue if s.id == from_song_id), None)
        if ref_song is None:
            # Reference song gone entirely - fall back to first/last
            return ready_songs[0] if offset >= 0 else ready_songs[-1]

        ref_position = ref_song.position

        if offset > 0:
            candidates = [s for s in ready_songs if s.position > ref_position]
            if len(candidates) >= offset:
                return candidates[offset - 1]
        elif offset < 0:
            candidates = [s for s in ready_songs if s.position < ref_position]
            candidates.reverse()
            if len(candidates) >= abs(offset):
                return candidates[abs(offset) - 1]

        return None

    def clear_queue(self) -> int:
        """Clear all items from the queue and reset the cursor."""
        self.clear_cursor()
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

    def get_item(self, item_id: int) -> Optional[QueueItem]:
        """Get a specific queue item by ID."""
        return self.repository.get_item(item_id)

    def update_pitch(self, item_id: int, pitch_semitones: int) -> bool:
        """Update pitch adjustment for a queue item."""
        return self.repository.update_pitch(item_id, pitch_semitones)

    def update_extracted_metadata(self, item_id: int, artist: str, song_name: str) -> bool:
        """Update extracted artist/song metadata for a queue item."""
        return self.repository.update_extracted_metadata(item_id, artist, song_name)
