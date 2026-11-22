"""
Queue management for kbox.

Handles song queue operations with persistence and content preparation.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from .alerts import KIND_DOWNLOAD_FAILED, Alert, AlertCenter
from .database import Database, QueueRepository, UserRepository
from .models import QueueItem, SongMetadata, SongSettings, User
from .periodic import PeriodicThread

if TYPE_CHECKING:
    from .loudness import LoudnessAnalyzer
    from .session import SessionManager
    from .silence_detection import TrailingSilenceAnalyzer
    from .song_metadata import SongMetadataExtractor
    from .video_library import VideoLibrary

# How often the content monitor looks for items to download/prepare.
CONTENT_MONITOR_INTERVAL_SECONDS = 2.0
# Wait longer after a pass raised, so a persistent failure doesn't spin.
CONTENT_MONITOR_ERROR_BACKOFF_SECONDS = 5.0
# After a failed download, wait this long before each further attempt. The
# song only counts as failed -- and its singer is told -- once every retry
# has failed too. Downloads mostly fail for reasons that pass in seconds
# (YouTube once offered nothing but storyboard images for a video that
# downloaded fine the next day), so a quick retry fixes most of them without
# anyone noticing, and the short total keeps the singer's time to fix a real
# failure long.
DOWNLOAD_RETRY_DELAYS_SECONDS = (10.0, 60.0)


class MoveOutcome(Enum):
    """How a relative move ended."""

    MOVED = "moved"
    ALREADY_AT_BOUNDARY = "already_at_boundary"  # Already first/last; nothing to do
    NOT_FOUND = "not_found"
    FAILED = "failed"  # Found, but the reorder itself did not take


@dataclass(frozen=True)
class MoveResult:
    """
    The outcome of a relative move, plus the positions involved.

    Deliberately not a response dict: which of "moved_up"/"moved_down" or
    "already_at_start"/"already_at_end" to call this depends on the
    direction, which the caller knows and the queue does not. The HTTP
    wording lives in the route.
    """

    outcome: MoveOutcome
    old_position: Optional[int] = None
    new_position: Optional[int] = None


class QueueManager:
    """Manages the song queue with persistence and content preparation."""

    STATUS_PENDING = "pending"
    STATUS_PREPARING = "preparing"
    STATUS_READY = "ready"
    STATUS_ERROR = "error"

    def __init__(
        self,
        database: Database,
        video_library: "VideoLibrary",
        metadata_extractor: Optional["SongMetadataExtractor"] = None,
        silence_analyzer: Optional["TrailingSilenceAnalyzer"] = None,
        loudness_analyzer: Optional["LoudnessAnalyzer"] = None,
        session_manager: Optional["SessionManager"] = None,
        before_clear: Optional[Callable[[], None]] = None,
        alerts: Optional[AlertCenter] = None,
        clock: Callable[[], datetime] = datetime.now,
    ):
        """
        Initialize QueueManager.

        Args:
            database: Database instance for persistence
            video_library: VideoLibrary for video search/download
            metadata_extractor: Optional SongMetadataExtractor for LLM-based extraction
            silence_analyzer: Optional TrailingSilenceAnalyzer for trailing-silence detection
            loudness_analyzer: Optional LoudnessAnalyzer for volume normalization
            session_manager: Optional SessionManager; when provided, new queue
                items are tagged with the current session and clear_queue
                rotates the session.
            before_clear: Optional callback run just before clear_queue
                deletes anything (used to snapshot the database). Failures
                are logged and never block the clear.
            alerts: Optional AlertCenter. When provided, a song whose download
                has failed for good raises an alert to its singer and the
                operators, resolved once the song is fixed or gone.
            clock: Returns the current time; tests pass a fake one to step
                through download retries without waiting.
        """
        self.database = database
        self.before_clear = before_clear
        self.repository = QueueRepository(database)
        self.user_repository = UserRepository(database)
        self.video_library = video_library
        self.metadata_extractor = metadata_extractor
        self.silence_analyzer = silence_analyzer
        self.loudness_analyzer = loudness_analyzer
        self.session_manager = session_manager
        self.alerts = alerts
        self._clock = clock
        self.logger = logging.getLogger(__name__)

        self._content_timeout = timedelta(minutes=10)
        self._content_monitor = PeriodicThread(
            self._process_pending_content,
            interval_seconds=CONTENT_MONITOR_INTERVAL_SECONDS,
            error_backoff_seconds=CONTENT_MONITOR_ERROR_BACKOFF_SECONDS,
            name="ContentMonitor",
        )

        # When the current content-preparation attempt for an item started,
        # keyed by item id. Tracks the active attempt (reset on every restart,
        # including after a replace), unlike the item's created_at, which
        # never changes. Falls back to created_at in _check_stuck_content for
        # items that reach STATUS_PREPARING without going through
        # _start_content_preparation (e.g. restored from a prior process).
        self._prep_started_at: Dict[int, datetime] = {}

        # Download retries, keyed by item id: how many attempts have failed,
        # and the earliest time the next attempt may start. Only in memory: a
        # restart gives a song that was mid-retry a fresh set of attempts.
        self._failed_attempts: Dict[int, int] = {}
        self._retry_not_before: Dict[int, datetime] = {}

        self._start_content_monitor()

    # =========================================================================
    # Content Monitoring
    # =========================================================================

    def _start_content_monitor(self):
        """Start background thread to monitor queue and trigger content preparation."""
        self._content_monitor.start()

    def _process_pending_content(self):
        """Process pending and stuck content preparations."""
        if not self.video_library.has_provider:
            return

        queue = self.get_queue()

        for item in queue:
            if item.content_status == self.STATUS_PENDING:
                not_before = self._retry_not_before.get(item.id)
                if not_before is not None and self._clock() < not_before:
                    continue  # Waiting out the delay before a retry
                self._retry_not_before.pop(item.id, None)
                self._start_content_preparation(item)
            elif item.content_status == self.STATUS_PREPARING:
                self._check_stuck_content(item)

    def _start_content_preparation(self, item: QueueItem):
        """Start preparing content for a queue item."""
        self.logger.info(
            "Starting content preparation for %s (ID: %s)", item.metadata.title, item.id
        )

        item_id = item.id
        video_id = item.video_id

        # Mark PREPARING synchronously, before handing off to video_library,
        # so the next poll (as little as ~2s away) sees this item as already
        # in flight rather than still PENDING. video_library.request() only
        # flips the status via the "downloading" callback once its
        # single-worker download executor actually picks the job up, which can
        # lag behind a poll cycle while an earlier download is still running
        # — without this, each lagging poll queues another competing download
        # for the same video.
        #
        # Guarded by video_id: `item` is from the start of this poll, and if
        # it has been replaced since, this video is no longer wanted.
        if not self.update_content_status(item_id, self.STATUS_PREPARING, video_id=video_id):
            return
        self._prep_started_at[item_id] = datetime.now()

        # The callback carries the video it was requested for, so a result
        # arriving after the item was replaced is recognised and dropped.
        def on_status(status: str, path: Optional[str], error: Optional[str]):
            self._on_content_status(item_id, video_id, status, path, error)

        self.video_library.request(video_id, callback=on_status)

    def _check_stuck_content(self, item: QueueItem):
        """Check if content preparation is stuck and recover if possible."""
        cached_path = self.video_library.get_path(item.video_id)
        if cached_path and cached_path.exists():
            self.logger.info(
                "Found completed content for %s (ID: %s), updating status",
                item.metadata.title,
                item.id,
            )
            if self.update_content_status(
                item.id,
                self.STATUS_READY,
                content_path=str(cached_path),
                video_id=item.video_id,
            ):
                self._run_post_download_analysis(item.id, item.video_id, str(cached_path))
                self._prep_started_at.pop(item.id, None)
            return

        # Prefer the current prep attempt's start time over created_at: a
        # replace (or any other restart of preparation) doesn't change
        # created_at, so comparing against it would flag a long-queued item
        # as instantly "stuck" the moment its (fresh) replacement prep begins.
        started_at = self._prep_started_at.get(item.id, item.created_at)
        if started_at:
            if datetime.now(started_at.tzinfo) - started_at > self._content_timeout:
                self.logger.warning(
                    "Content preparation stuck for %s (ID: %s) for more than %s, resetting to pending",
                    item.metadata.title,
                    item.id,
                    self._content_timeout,
                )
                self.update_content_status(item.id, self.STATUS_PENDING, video_id=item.video_id)
                self._prep_started_at.pop(item.id, None)

    def _on_content_status(
        self,
        item_id: int,
        video_id: str,
        status: str,
        path: Optional[str],
        error: Optional[str],
    ):
        """
        Callback for content preparation status updates.

        Runs on the download worker, possibly long after the request: every
        update is guarded by the video_id it was requested for, and a result
        for a video the item no longer holds (it was replaced mid-download)
        is dropped rather than applied to the replacement.
        """
        if status == "downloading":
            self.update_content_status(item_id, self.STATUS_PREPARING, video_id=video_id)
        elif status == "ready" and path:
            if not self.update_content_status(
                item_id, self.STATUS_READY, content_path=path, video_id=video_id
            ):
                return
            self.logger.info("Content ready for queue item %s: %s", item_id, path)
            self._prep_started_at.pop(item_id, None)
            self._forget_failures(item_id)
            self._run_post_download_analysis(item_id, video_id, path)
            self._cleanup_storage()
        elif status == "error" and error:
            self._on_download_failed(item_id, video_id, error)

    def _on_download_failed(self, item_id: int, video_id: str, error: str) -> None:
        """
        One download attempt failed: schedule another, or give up.

        While a retry is pending the item stays PENDING, so the queue (and
        the guest's phone) treats it as still getting ready. Only after the
        last retry fails does it become STATUS_ERROR, and only then is
        anyone told.

        Like every callback update, both are guarded by video_id: the failure
        of a video the item no longer holds (it was replaced mid-download)
        is dropped, and counts against nothing.
        """
        attempts = self._failed_attempts.get(item_id, 0) + 1

        if attempts <= len(DOWNLOAD_RETRY_DELAYS_SECONDS):
            delay = DOWNLOAD_RETRY_DELAYS_SECONDS[attempts - 1]
            # Set before the item goes back to PENDING, so the monitor can't
            # start the retry early in between.
            self._retry_not_before[item_id] = self._clock() + timedelta(seconds=delay)
            if not self.update_content_status(item_id, self.STATUS_PENDING, video_id=video_id):
                self._retry_not_before.pop(item_id, None)
                return
            self._failed_attempts[item_id] = attempts
            self._prep_started_at.pop(item_id, None)
            self.logger.warning(
                "Download attempt %d failed for queue item %s, retrying in %.0fs: %s",
                attempts,
                item_id,
                delay,
                error,
            )
            return

        if not self.update_content_status(
            item_id, self.STATUS_ERROR, error_message=error, video_id=video_id
        ):
            return
        self._failed_attempts[item_id] = attempts
        self._retry_not_before.pop(item_id, None)
        self._prep_started_at.pop(item_id, None)
        self.logger.error(
            "Content preparation failed for queue item %s after %d attempts: %s",
            item_id,
            attempts,
            error,
        )
        self._raise_download_failed_alert(item_id)

    @staticmethod
    def _download_failed_key(item_id: int) -> str:
        return f"{KIND_DOWNLOAD_FAILED}:{item_id}"

    def _raise_download_failed_alert(self, item_id: int) -> None:
        """Tell the singer, the operators and the room that a song can't play."""
        if self.alerts is None:
            return
        item = self.get_item(item_id)
        if item is None:
            return
        song = item.metadata.song_name or item.metadata.title
        self.alerts.publish(
            Alert(
                key=self._download_failed_key(item_id),
                kind=KIND_DOWNLOAD_FAILED,
                message=f"“{song}” couldn't be loaded. Tap to pick another version.",
                user_ids=frozenset({item.user_id}),
                operator_message=f"{item.user_name}'s song couldn't be loaded.",
                # No title: only the singer and operators see titles.
                room_message=f"{item.user_name}: your song couldn't load. Check your phone.",
                queue_item_id=item_id,
            )
        )

    def _forget_failures(self, item_id: int) -> None:
        """Drop retry bookkeeping and any alert for an item that is fixed or gone."""
        self._failed_attempts.pop(item_id, None)
        self._retry_not_before.pop(item_id, None)
        if self.alerts is not None:
            self.alerts.resolve(self._download_failed_key(item_id))

    def retry_download(self, item_id: int) -> bool:
        """
        Try a failed download again, with a fresh set of retries.

        Returns:
            True if the item had failed and is now waiting to download again,
            False if it doesn't exist or hadn't failed.
        """
        item = self.get_item(item_id)
        if item is None or item.content_status != self.STATUS_ERROR:
            return False
        self.logger.info("Retrying download for queue item %s on request", item_id)
        self._forget_failures(item_id)
        return self.update_content_status(item_id, self.STATUS_PENDING, video_id=item.video_id)

    def _run_post_download_analysis(self, item_id: int, video_id: str, path: str) -> None:
        """Run per-video analysis steps once a queue item's content is ready.

        Runs synchronously on the caller's thread. Each step is independent
        and failures are logged, never allowed to break queue operations.

        Every result is keyed by video_id, the video that `path` holds, never
        by whatever the item holds now. The analyzers cache per video and
        never re-run, so analysing one video's file under another's ID would
        give that other video the wrong trim point and gain for good.
        """
        item = self.get_item(item_id)
        if item is None or item.video_id != video_id:
            return

        if self.metadata_extractor:
            try:
                artist, song_name = self.metadata_extractor.extract(
                    video_id=video_id,
                    title=item.metadata.title,
                    description=None,
                    channel=item.metadata.channel,
                )
                if artist and song_name:
                    if self.update_extracted_metadata(
                        item_id, artist, song_name, video_id=video_id
                    ):
                        self.logger.info(
                            "Extracted metadata for item %s: '%s' by '%s'",
                            item_id,
                            song_name,
                            artist,
                        )
            except Exception as e:
                self.logger.warning("Metadata extraction failed for item %s: %s", item_id, e)

        if self.silence_analyzer:
            try:
                self.silence_analyzer.analyze(video_id, path)
            except Exception as e:
                self.logger.warning("Silence analysis failed for item %s: %s", item_id, e)

        if self.loudness_analyzer:
            try:
                self.loudness_analyzer.analyze(video_id, path)
            except Exception as e:
                self.logger.warning("Loudness analysis failed for item %s: %s", item_id, e)

    def stop_content_monitor(self):
        """Stop the content monitor thread and wait for it to exit."""
        self._content_monitor.stop()

    def _cleanup_storage(self) -> None:
        """Trigger storage cleanup with all queue items protected from eviction."""
        try:
            queue = self.repository.get_all()
            protected = {item.video_id for item in queue}
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
        """
        metadata = SongMetadata(
            title=title,
            duration_seconds=duration_seconds,
            thumbnail_url=thumbnail_url,
            channel=channel,
        )
        settings = SongSettings(pitch_semitones=pitch_semitones)

        session_id = None
        if self.session_manager is not None:
            session_id = self.session_manager.get_or_create_current().id

        item_id = self.repository.add(
            user=user,
            video_id=video_id,
            metadata=metadata,
            settings=settings,
            session_id=session_id,
        )

        self.logger.info(
            "Added song to queue: %s by %s (ID: %s, video_id: %s, pitch: %s)",
            title,
            user.display_name,
            item_id,
            video_id,
            pitch_semitones,
        )

        return item_id

    def replace_song(
        self,
        item_id: int,
        video_id: str,
        title: str,
        duration_seconds: Optional[int] = None,
        thumbnail_url: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> bool:
        """
        Replace the video for an existing queue item, in place.

        Keeps the item's position and user attribution unchanged. Resets
        content status so the new video is downloaded; metadata extraction
        and silence analysis for the new video run once it's ready, same as
        any newly-added song.

        Args:
            item_id: ID of the queue item to replace
            video_id: Opaque video ID of the replacement video
            title: Replacement song title (original video title)
            duration_seconds: Duration in seconds (optional)
            thumbnail_url: Thumbnail URL (optional)
            channel: Channel/artist name (optional)

        Returns:
            True if the item was replaced, False if it wasn't found
        """
        metadata = SongMetadata(
            title=title,
            duration_seconds=duration_seconds,
            thumbnail_url=thumbnail_url,
            channel=channel,
        )

        if not self.repository.replace(item_id, video_id, metadata):
            return False
        self._forget_failures(item_id)

        self.logger.info("Replaced queue item %s with: %s (video_id: %s)", item_id, title, video_id)

        return True

    def remove_song(self, item_id: int) -> bool:
        """Remove a song from the queue."""
        removed = self.repository.remove(item_id)
        if removed:
            self._forget_failures(item_id)
        return removed

    def reorder_song(self, item_id: int, new_position: int) -> bool:
        """Move a song to a new position in the queue."""
        return self.repository.reorder(item_id, new_position)

    # =========================================================================
    # Relative moves
    #
    # These all clamp the target position into range before calling
    # reorder_song(), because the repository rejects a position outside
    # 1..max as invalid rather than clamping it itself (see
    # QueueRepository.reorder). Keeping them here puts that knowledge next to
    # the method whose contract requires it.
    # =========================================================================

    def _max_position(self, default: int = 0) -> int:
        """Highest position currently in the queue, or `default` if empty."""
        return max((item.position for item in self.repository.get_all()), default=default)

    def move_down(self, item_id: int) -> MoveResult:
        """
        Move a song down one position in the queue.

        Args:
            item_id: ID of the queue item to move down

        Returns:
            MoveResult: MOVED with both positions, ALREADY_AT_BOUNDARY with
            the position it stayed at, or NOT_FOUND / FAILED.
        """
        self.logger.info("Moving down song %s", item_id)

        item = self.get_item(item_id)
        if not item:
            self.logger.warning("Song %s not found", item_id)
            return MoveResult(MoveOutcome.NOT_FOUND)

        current_position = item.position

        # Calculate new position (down 1, but not past the end)
        new_position = min(current_position + 1, self._max_position())

        if new_position == current_position:
            # Already at the end
            self.logger.info("Song %s already at end of queue", item_id)
            return MoveResult(MoveOutcome.ALREADY_AT_BOUNDARY, old_position=current_position)

        if not self.reorder_song(item_id, new_position):
            self.logger.error("Failed to reorder song %s", item_id)
            return MoveResult(MoveOutcome.FAILED)

        return MoveResult(
            MoveOutcome.MOVED, old_position=current_position, new_position=new_position
        )

    def move_up(self, item_id: int) -> MoveResult:
        """
        Move a song up one position in the queue.

        Args:
            item_id: ID of the queue item to move up

        Returns:
            MoveResult: MOVED with both positions, ALREADY_AT_BOUNDARY with
            the position it stayed at, or NOT_FOUND / FAILED.
        """
        self.logger.info("Moving up song %s", item_id)

        item = self.get_item(item_id)
        if not item:
            self.logger.warning("Song %s not found", item_id)
            return MoveResult(MoveOutcome.NOT_FOUND)

        current_position = item.position

        # Get min position
        queue = self.get_queue()
        min_position = min((q.position for q in queue), default=1)

        # Calculate new position (up 1, but not past the start)
        new_position = max(current_position - 1, min_position)

        if new_position == current_position:
            # Already at the start
            self.logger.info("Song %s already at start of queue", item_id)
            return MoveResult(MoveOutcome.ALREADY_AT_BOUNDARY, old_position=current_position)

        if not self.reorder_song(item_id, new_position):
            self.logger.error("Failed to reorder song %s", item_id)
            return MoveResult(MoveOutcome.FAILED)

        return MoveResult(
            MoveOutcome.MOVED, old_position=current_position, new_position=new_position
        )

    def move_after(self, item_id: int, reference_song_id: Optional[int]) -> Optional[int]:
        """
        Move a song to the position just after a reference song.

        The write-side mirror of get_song_at_offset(): the caller supplies the
        reference song, so this needs to know nothing about playback. With no
        reference song, the song goes to the front of the queue.

        The reference song's position is queried fresh rather than taken from
        the caller, because any earlier reorder leaves a cached position
        stale.

        Args:
            item_id: ID of the queue item to move
            reference_song_id: ID of the song to move it after, or None to
                move it to the front

        Returns:
            The song's new position, or None if the move could not be made
        """
        self.logger.info("Moving song %s after song %s", item_id, reference_song_id)

        if reference_song_id:
            reference_song = self.get_item(reference_song_id)
            if not reference_song:
                self.logger.error("Reference song ID %s not found", reference_song_id)
                return None
            item = self.get_item(item_id)
            if item and item.position < reference_song.position:
                # Moving down: reorder() shifts the songs in between (the
                # reference among them) up one before inserting, so the
                # reference's own position is the slot right after it.
                target_position = reference_song.position
            else:
                target_position = reference_song.position + 1

            # If the reference song is last in the queue, "after" it is simply
            # the end of the queue - clamp so we don't request a position
            # past the max (which reorder() would reject).
            target_position = min(target_position, self._max_position(target_position))
        else:
            target_position = 1

        if not self.reorder_song(item_id, target_position):
            self.logger.error("Failed to move song %s after %s", item_id, reference_song_id)
            return None

        return target_position

    def move_before(self, item_id: int, reference_song_id: int) -> Optional[int]:
        """
        Move a song to the position just before a reference song.

        Both positions are queried fresh, like move_after().

        Args:
            item_id: ID of the queue item to move
            reference_song_id: ID of the song it should end up right before

        Returns:
            The song's new position, or None if the move could not be made
        """
        self.logger.info("Moving song %s before song %s", item_id, reference_song_id)
        item = self.get_item(item_id)
        reference_song = self.get_item(reference_song_id)
        if item is None or reference_song is None:
            self.logger.error("Song %s or %s not found", item_id, reference_song_id)
            return None
        if item.position < reference_song.position:
            # Moving down: reorder() shifts the reference up one first.
            target_position = reference_song.position - 1
        else:
            target_position = reference_song.position
        if target_position == item.position:
            return target_position
        if not self.reorder_song(item_id, target_position):
            self.logger.error("Failed to move song %s before %s", item_id, reference_song_id)
            return None
        return target_position

    def move_to_end(self, item_id: int) -> Optional[int]:
        """
        Move a song to the end of the queue.

        Args:
            item_id: ID of the queue item to move

        Returns:
            The song's new position, or None if the move could not be made
        """
        self.logger.info("Moving song %s to end of queue", item_id)

        queue = self.get_queue()
        if not queue:
            self.logger.warning("Cannot move to end - queue is empty")
            return None

        max_position = max((item.position for item in queue), default=0)

        if not self.reorder_song(item_id, max_position):
            self.logger.error("Failed to move song %s to end", item_id)
            return None

        return max_position

    def get_queue(self) -> List[QueueItem]:
        """Get the entire queue ordered by position."""
        return self.repository.get_all()

    def get_song_at_offset(self, from_song_id: Optional[int], offset: int) -> Optional[QueueItem]:
        """
        Get the song at an offset from a reference song, respecting queue order.

        Returns the song at the given offset by position, regardless of download
        status. This is for queue-order navigation — callers decide whether to
        play, wait, or skip based on the song's status.

        Args:
            from_song_id: Reference song ID (None = start from beginning/end)
            offset: +1 for next, -1 for previous, 0 for first

        Returns:
            The song at the offset, or None if not found
        """
        queue = self.repository.get_all()

        if not queue:
            return None

        if from_song_id is None:
            # No reference - return first (offset >= 0) or last (offset < 0)
            return queue[0] if offset >= 0 else queue[-1]

        # Find reference song in queue
        current_idx = next((i for i, s in enumerate(queue) if s.id == from_song_id), None)

        if current_idx is None:
            # Reference song gone entirely - fall back to first/last.
            # PlaybackController.remove_song() keeps playback's references
            # (current song, cursor) pointing at rows that exist, so this is
            # reached only for a stale reference, e.g. a cursor persisted by
            # an older version. Playing from the top beats getting stuck.
            return queue[0] if offset >= 0 else queue[-1]

        target_idx = current_idx + offset
        if 0 <= target_idx < len(queue):
            return queue[target_idx]

        return None

    def clear_queue(self) -> int:
        """Clear all items from the queue and rotate the party session.

        Clearing the queue is the ritual that bookends a party: the current
        session is marked ended and a fresh session is started (snapshotting
        the currently-configured party theme, if any).
        """
        if self.before_clear is not None:
            try:
                self.before_clear()
            except Exception:
                self.logger.exception("before_clear callback failed; clearing queue anyway")
        count = self.repository.clear()
        self._failed_attempts.clear()
        self._retry_not_before.clear()
        if self.alerts is not None:
            self.alerts.clear()
        if self.session_manager is not None:
            self.session_manager.end_and_rotate()
        return count

    def update_content_status(
        self,
        item_id: int,
        status: str,
        content_path: Optional[str] = None,
        error_message: Optional[str] = None,
        video_id: Optional[str] = None,
    ) -> bool:
        """
        Update content status for a queue item.

        With video_id, applies only while the item still holds that video
        (see QueueRepository.update_status).
        """
        return self.repository.update_status(
            item_id, status, content_path, error_message, video_id=video_id
        )

    def get_item(self, item_id: int) -> Optional[QueueItem]:
        """Get a specific queue item by ID."""
        return self.repository.get_item(item_id)

    def update_pitch(self, item_id: int, pitch_semitones: int) -> bool:
        """Update pitch adjustment for a queue item."""
        return self.repository.update_pitch(item_id, pitch_semitones)

    def update_extracted_metadata(
        self, item_id: int, artist: str, song_name: str, video_id: Optional[str] = None
    ) -> bool:
        """Update extracted artist/song metadata for a queue item."""
        return self.repository.update_extracted_metadata(
            item_id, artist, song_name, video_id=video_id
        )
