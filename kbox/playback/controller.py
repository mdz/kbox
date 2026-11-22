"""
The playback state machine.

PlaybackController orchestrates playback, manages state transitions, and
handles error recovery. It is the only place self.state and
self.current_song_id are touched; everything that does not need them lives in
a sibling module (see the package docstring).
"""

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, List, Optional

from ..database import ConfigRepository
from ..interstitials import QueueSummary
from ..models import QueueItem
from ..periodic import PeriodicThread
from ..queue import MoveResult, QueueManager
from ..user_events import PHASE_PLAYING
from . import history_policy, song_tuning
from .cursor import QueueCursor
from .overlay import OverlayCoordinator
from .screens import ScreenPresenter
from .upcoming import UpcomingItem, upcoming_songs

# How often the background monitor ticks.
MONITOR_INTERVAL_SECONDS = 2
# Wait longer after a tick raised, so a persistent failure doesn't spin.
MONITOR_ERROR_BACKOFF_SECONDS = 5


class PlaybackState(Enum):
    """Playback state enumeration."""

    STOPPED = "stopped"  # Operator stopped or initial state - no auto-start
    IDLE = "idle"  # Queue exhausted naturally - will auto-start when songs added
    PLAYING = "playing"
    PAUSED = "paused"
    TRANSITION = "transition"  # Between songs, showing interstitial
    ERROR = "error"


@dataclass(frozen=True)
class PlaybackStatus:
    """
    A snapshot of what playback is doing.

    Domain objects, not a response body: current_song is the QueueItem
    itself, so a caller that only wants one field off it (the web layer's
    queue endpoint wants the duration) reads it directly instead of through
    a flattened dict. kbox/web/serializers.py turns it into JSON.
    """

    state: PlaybackState
    current_song: Optional[QueueItem]
    position_seconds: Optional[int]


class SeekOutcome(Enum):
    """How a seek (or a restart, which is a seek to zero) ended."""

    SEEKED = "seeked"
    NOTHING_PLAYING = "nothing_playing"  # Not an error: controls are always visible
    FAILED = "failed"  # GStreamer refused the seek


@dataclass(frozen=True)
class SeekResult:
    """
    The outcome of a seek, plus where playback ended up.

    Deliberately not a response body. "restarted" vs "seeked" is the HTTP
    vocabulary for the same outcome seen from two endpoints, and the
    frontend relies on "nothing_playing" to tell an expected no-op from a
    failure -- so that wording belongs in the routes, which know which
    endpoint they are.
    """

    outcome: SeekOutcome
    position: Optional[int] = None


class PlaybackController:
    """Orchestrates playback and manages state."""

    def __init__(
        self,
        queue_manager: QueueManager,
        streaming_controller,  # StreamingController - avoid circular import
        config_manager,
        history_manager=None,  # HistoryManager - avoid circular import, optional for tests
        silence_analyzer=None,  # TrailingSilenceAnalyzer - avoid circular import, optional
        loudness_analyzer=None,  # LoudnessAnalyzer - avoid circular import, optional
        pitch_log=None,  # PitchLog - optional, for recording pitch choices
    ):
        """
        Initialize PlaybackController.

        Args:
            queue_manager: QueueManager instance
            streaming_controller: StreamingController instance
            config_manager: ConfigManager instance
            history_manager: HistoryManager instance (optional, for tests)
            silence_analyzer: TrailingSilenceAnalyzer instance (optional). When
                provided (and skip_trailing_silence_enabled is on), playback
                advances early once a song reaches its cached trailing-silence
                trim point instead of waiting for literal end-of-file.
            loudness_analyzer: LoudnessAnalyzer instance (optional). When
                provided (and loudness_normalization_enabled is on), a
                compensating gain is applied so songs from different
                sources play back at a consistent volume.
            pitch_log: PitchLog instance (optional). When provided, the pitch
                each song starts at and every change made during playback are
                logged (see kbox/pitch_log.py).
        """
        self.queue_manager = queue_manager
        self.streaming_controller = streaming_controller
        self.config_manager = config_manager
        self.history_manager = history_manager
        self.silence_analyzer = silence_analyzer
        self.loudness_analyzer = loudness_analyzer
        self.pitch_log = pitch_log
        self.logger = logging.getLogger(__name__)
        self.state = PlaybackState.STOPPED  # Start in STOPPED so operator must press Play
        self.current_song_id: Optional[int] = None

        # The one lock in the playback layer: the collaborators below
        # (cursor, overlay, screens) have none of their own and rely on it.
        #
        # This lock is deliberately NOT an RLock. Re-entering it is a bug, and
        # a plain Lock deadlocks loudly instead of silently absorbing it.
        # Methods that require it are marked with self._require_locked().
        self.lock = threading.Lock()

        # Thread ID of the current holder of self.lock, or None. Maintained by
        # _locked() and checked by _require_locked(). Only ever written while
        # self.lock is held, so no separate synchronization is needed.
        self._lock_owner: Optional[int] = None

        # Cached trailing-silence trim point (seconds) for the current song,
        # looked up once in _play_song(). None if unavailable/disabled.
        self._current_trim_point: Optional[int] = None

        # Pitch the current song started playing at, set in _play_song(), so
        # pitch_change events can tell the starting choice from corrections.
        self._start_pitch: Optional[int] = None

        # Queue cursor: the ID of the song currently playing (or last played).
        # Songs before the cursor's position are "played", songs after are "upcoming".
        # Persisted to the config table for restart resilience.
        self._cursor = QueueCursor(ConfigRepository(queue_manager.database))

        # Transition/interstitial state
        self._transition_timer: Optional[threading.Timer] = None
        self._next_song_pending = None  # Song to play after transition

        # Interstitial screens (idle, transition, end-of-queue, messages)
        self._screens = ScreenPresenter(config_manager, streaming_controller)

        # Background monitor thread (handles notifications, auto-start, etc.).
        # PeriodicThread owns the thread; the policy of what each tick does is
        # _on_monitor_tick() below, because it reads and writes the state
        # machine.
        self._monitor = PeriodicThread(
            self._on_monitor_tick,
            interval_seconds=MONITOR_INTERVAL_SECONDS,
            error_backoff_seconds=MONITOR_ERROR_BACKOFF_SECONDS,
            name="PlaybackMonitor",
        )
        self._up_next_shown = False  # Track if "up next" notification was shown for current song
        self._current_singer_shown = (
            False  # Track if "current singer" notification was shown for current song
        )

        # Set when replace_song() stops the currently-playing song to swap its
        # content; the monitor auto-resumes once that item's content is ready.
        self._awaiting_replace_item_id: Optional[int] = None

        # Overlay management - the coordinator owns the base text and the
        # active notification's deadline. Only ever called under self.lock;
        # the monitor tick expires notifications.
        self._overlay = OverlayCoordinator(streaming_controller)

        self._monitor.start()

        # Set EOS callback
        self.streaming_controller.set_eos_callback(self.on_song_end)

    # =========================================================================
    # Locking
    # =========================================================================

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """
        Acquire the playback lock, recording the owning thread.

        Use this instead of `with self.lock:` everywhere. Recording the owner
        is what makes _require_locked() able to check that *this* thread holds
        the lock, rather than merely that somebody does.

        self.lock is not reentrant, so nesting this (directly or via a method
        that uses it) deadlocks. That is intentional - see the note on
        self.lock in __init__.
        """
        with self.lock:
            self._lock_owner = threading.get_ident()
            try:
                yield
            finally:
                self._lock_owner = None

    def _require_locked(self) -> None:
        """
        Assert that the calling thread holds the playback lock.

        Called at the top of methods documented as "assumes lock is already
        held", turning that convention from a comment into a runtime check.

        Note this checks ownership, not merely that the lock is held:
        threading.Lock.locked() would also be True while a *different* thread
        held it, which is exactly the case worth catching.
        """
        if self._lock_owner != threading.get_ident():
            raise AssertionError(
                f"{type(self).__name__} method requires self.lock to be held by the "
                f"calling thread; use 'with self._locked():' at the entry point"
            )

    # =========================================================================
    # Queue Cursor
    # =========================================================================

    def set_cursor(self, song_id: int) -> None:
        """Set the queue cursor to the song now playing. See QueueCursor.set."""
        self._cursor.set(song_id)

    def get_cursor(self) -> Optional[int]:
        """Get the current cursor song ID."""
        return self._cursor.get()

    def get_upcoming(self) -> List[UpcomingItem]:
        """Songs after the cursor, with estimated start times (see upcoming_songs)."""
        with self._locked():
            state = self.state
            current_song_id = self.current_song_id

        position = None
        if state in (PlaybackState.PLAYING, PlaybackState.PAUSED):
            position = self.streaming_controller.get_position()

        return upcoming_songs(
            self.queue_manager.get_queue(), self.get_cursor(), current_song_id, position
        )

    def clear_cursor(self) -> None:
        """Clear the queue cursor (e.g., when queue is cleared)."""
        self._cursor.clear()

    def _set_state(self, new_state: PlaybackState, reason: str = ""):
        """
        Set playback state with logging.

        All state transitions should go through this method to ensure
        consistent logging and easier debugging.

        Args:
            new_state: The new state to transition to
            reason: Optional reason/context for the transition
        """
        old_state = self.state
        if old_state == new_state:
            return  # No change, skip logging

        self.state = new_state
        if reason:
            self.logger.info("State: %s -> %s (%s)", old_state.value, new_state.value, reason)
        else:
            self.logger.info("State: %s -> %s", old_state.value, new_state.value)

    # =========================================================================
    # Overlay Management
    # =========================================================================

    def show_notification(self, text: str, duration_seconds: float = 5.0):
        """
        Show a transient notification over the base overlay text.

        The notification is cleared by the monitor tick, so it stays up for
        duration_seconds rounded up to the next tick: up to
        MONITOR_INTERVAL_SECONDS longer than asked for.

        Args:
            text: Notification text to display
            duration_seconds: Minimum time to show the notification
        """
        with self._locked():
            self._overlay.show_notification(text, duration_seconds)

    def _on_monitor_tick(self):
        """
        One pass of the background monitor: handle notifications, auto-start, etc.

        Called from the monitor's PeriodicThread. This stays on the controller
        because every branch below reads self.state and self.current_song_id,
        and _check_auto_start_when_idle() / _check_auto_resume_after_replace()
        start playback.

        The whole tick runs under the playback lock, so the state a branch
        decided on is still the state when it acts: an operator's stop or
        skip lands either wholly before the tick or wholly after it. What
        that costs is the lock held across a position query and a few SQLite
        reads every couple of seconds, which is small next to _play_song()
        holding it across load_file().
        """
        with self._locked():
            self._monitor_tick_locked()

    def _monitor_tick_locked(self):
        """The body of _on_monitor_tick(). Assumes lock is already held."""
        self._require_locked()
        # In every state, not just PLAYING: a notification shown over an
        # interstitial screen must clear too.
        self._overlay.expire_notification()
        if self.state == PlaybackState.PLAYING and self.current_song_id:
            position = self.streaming_controller.get_position()
            if position is not None:
                # Check if we should show "current singer" notification at start
                self._check_current_singer_notification(position)
                # Check if we should show "up next" notification near end
                self._check_up_next_notification(position)
                # Check if we've reached trailing silence and can advance early
                self._check_trailing_silence_skip(position)
        elif self.state == PlaybackState.IDLE:
            # Auto-start playback if we're idle and there are ready songs
            # This handles the "that's all" screen -> someone adds a song case
            self._check_auto_start_when_idle()
        elif self.state == PlaybackState.STOPPED and self._awaiting_replace_item_id:
            # Auto-resume a song that was stopped to replace its content,
            # once the replacement has finished downloading
            self._check_auto_resume_after_replace()
        elif self.state == PlaybackState.STOPPED:
            # Before the operator presses Play, keep the idle screen's
            # "who's in line" current as guests add songs, so the room sees
            # the party filling up. Redraws only when the summary changes.
            self._screens.refresh_idle(self._idle_queue_summary())

    def _check_auto_start_when_idle(self):
        """
        Check if we're idle and the next song in queue order is ready, and auto-start.

        This handles the case where we're on the "that's all" screen and
        someone adds a song - once it finishes downloading, playback
        should start automatically without requiring operator intervention.

        Only looks forward from the cursor, never backward - so songs that
        were already played (before the cursor) won't be picked up.

        Respects queue order: if the next song isn't ready yet, we wait
        rather than skipping ahead to a later song that happened to
        download faster. A song whose download failed for good is the one
        exception (see _step_past_failed()).

        Assumes lock is already held.
        """
        self._require_locked()
        next_song = self._step_past_failed(self._song_after_cursor())
        if next_song and next_song.content_status == QueueManager.STATUS_READY:
            self.logger.info("Idle with ready songs, auto-starting playback")
            self._play_locked()

    def _check_auto_resume_after_replace(self):
        """
        Check if a song we stopped to replace has finished downloading, and
        resume it automatically without requiring operator intervention.

        Only acts while self._awaiting_replace_item_id is set (by
        replace_song()) and still matches the current song - any other
        control action (play/skip/previous/jump/manual stop) clears it,
        so this never overrides an operator's explicit choice.

        Assumes lock is already held.
        """
        self._require_locked()
        item_id = self._awaiting_replace_item_id
        if item_id is None or item_id != self.current_song_id:
            return

        song = self.queue_manager.get_item(item_id)
        if song and song.content_status == QueueManager.STATUS_READY:
            self.logger.info("Replacement ready for song %s, auto-resuming", item_id)
            self._awaiting_replace_item_id = None
            self._play_locked()

    def _check_current_singer_notification(self, current_position: int):
        """
        Check if song just started and show "current singer" persistent overlay.

        Assumes lock is already held.

        Args:
            current_position: Current playback position in seconds
        """
        self._require_locked()
        if self._current_singer_shown:
            return  # Already shown for this song

        if not self.current_song_id:
            return

        # Show persistent overlay at the very start of the song (within first 3 seconds)
        # This allows the singer to see their name even if they missed the interstitial
        # and lets the audience learn their name
        if current_position > 3:
            return

        current_song = self.queue_manager.get_item(self.current_song_id)
        if current_song:
            overlay_text = f"Now singing: {current_song.user_name}"
            # Add extracted song metadata if available
            song_info = self._format_song_info(current_song.metadata)
            if song_info:
                overlay_text = f"{overlay_text}\n{song_info}"
            self._overlay.set_base(overlay_text)
            self._current_singer_shown = True
            self.logger.debug("Set current singer overlay for: %s", current_song.user_name)

    def _format_song_info(self, metadata) -> Optional[str]:
        """
        Format extracted song metadata for display.

        Args:
            metadata: SongMetadata with optional artist/song_name

        Returns:
            Formatted string like "Song Name by Artist" or None if no metadata
        """
        if metadata.song_name and metadata.artist:
            return f"{metadata.song_name} by {metadata.artist}"
        elif metadata.song_name:
            return metadata.song_name
        return None

    def _check_up_next_notification(self, current_position: int):
        """
        Check if song is ending and update overlay to show "up next".

        Assumes lock is already held.

        Args:
            current_position: Current playback position in seconds
        """
        self._require_locked()
        if self._up_next_shown:
            return  # Already shown for this song

        if not self.current_song_id:
            return

        song_id = self.current_song_id

        # Get current song data to check duration
        current_song = self.queue_manager.get_item(song_id)
        if not current_song:
            return

        duration = current_song.metadata.duration_seconds
        if not duration or duration <= 0:
            return

        # Update overlay text when 15 seconds or less remain
        time_remaining = duration - current_position
        if time_remaining > 15:
            return

        # Get next song by queue position (show who's up next even if their
        # song is still downloading, but never someone whose song can't play)
        next_song = self._step_past_failed(self.queue_manager.get_song_at_offset(song_id, +1))
        if next_song:
            overlay_text = f"Up next: {next_song.user_name}"
            self._overlay.set_base(overlay_text)
            self._up_next_shown = True
            self.logger.debug("Updated overlay to up next for: %s", next_song.user_name)

    def _check_trailing_silence_skip(self, current_position: int):
        """
        Check if playback has reached the cached trailing-silence trim point
        and, if so, advance to the next song without waiting for literal EOS.

        Assumes lock is already held.

        Args:
            current_position: Current playback position in seconds
        """
        self._require_locked()
        if self.state != PlaybackState.PLAYING or self._current_trim_point is None:
            return

        if current_position < self._current_trim_point:
            return

        self.logger.info(
            "Reached trailing silence at %ss (trim point %ss), advancing early",
            current_position,
            self._current_trim_point,
        )

        song = self.queue_manager.get_item(self.current_song_id) if self.current_song_id else None
        duration = song.metadata.duration_seconds if song else None
        # Record the full song duration (not the truncated stop position)
        # for history/completion stats, so trimmed songs still count as
        # fully played.
        final_position = duration if duration else self._current_trim_point

        self.streaming_controller.stop_playback()
        self._complete_current_song(final_position=final_position)

    def play(self) -> bool:
        """
        Start or resume playback.

        Returns:
            True if playback started, False otherwise
        """
        with self._locked():
            return self._play_locked()

    def _play_locked(self) -> bool:
        """The body of play(). Assumes lock is already held."""
        self._require_locked()
        if self.state == PlaybackState.PLAYING:
            self.logger.debug("Already playing")
            return True

        if self.state == PlaybackState.PAUSED:
            # Resume current song
            try:
                self.streaming_controller.resume()
                self._set_state(PlaybackState.PLAYING, "resumed")
                return True
            except Exception as e:
                self.logger.error("Error resuming playback: %s", e, exc_info=True)
                return False

        if self.state == PlaybackState.TRANSITION:
            # Already transitioning to next song, skip the wait
            self.logger.info("Skipping transition, starting song immediately")
            if self._transition_timer:
                self._transition_timer.cancel()
                self._transition_timer = None
            if self._next_song_pending:
                next_song = self._next_song_pending
                self._next_song_pending = None
                return self._play_song(next_song)

        if self.state == PlaybackState.STOPPED:
            if self.current_song_id:
                # Resume the stopped song
                song = self.queue_manager.get_item(self.current_song_id)
                if song and song.content_status == "ready":
                    return self._play_song(song)
                # Song was deleted or not ready - fall through to load next
            # No remembered song - fall through to load next

        # Start new song
        return self._load_and_play_next()

    def _load_and_play_next(self) -> bool:
        """Load next song in queue order and start playback if it's ready.

        Uses the cursor to find the next song. On fresh start (no cursor),
        falls back to the first song in the queue.

        Respects queue order: if the next song isn't ready yet, we go idle
        rather than skipping ahead to a later song. A song whose download
        failed for good is stepped past (see _take_turn_from_failed()).

        Assumes lock is already held.
        """
        self._require_locked()
        next_song = self._take_turn_from_failed(self._song_after_cursor())

        if not next_song:
            self._set_state(PlaybackState.IDLE, "no more songs")
            return False

        if next_song.content_status != QueueManager.STATUS_READY:
            self._set_state(PlaybackState.IDLE, "next song not ready yet")
            return False

        return self._play_song(next_song)

    def _song_after_cursor(self) -> Optional[QueueItem]:
        """The song after the cursor, or the first song with no cursor."""
        cursor = self.get_cursor()
        if cursor is not None:
            return self.queue_manager.get_song_at_offset(cursor, +1)
        return self.queue_manager.get_song_at_offset(None, 0)

    def _step_past_failed(self, candidate: Optional[QueueItem]) -> Optional[QueueItem]:
        """
        The song that will actually go next in candidate's place.

        That is candidate itself unless its download has failed for good
        (STATUS_ERROR, after every retry), in which case it is the first song
        after it that hasn't: waiting for a song that will never be ready
        would stall the party. A song that is still downloading is not
        stepped past -- the queue waits for it, as it always has.

        Moves nothing; see _take_turn_from_failed() for that.

        Returns:
            The song to start, or None if only failed songs are left
        """
        seen = set()
        song = candidate
        while song is not None and song.content_status == QueueManager.STATUS_ERROR:
            if song.id in seen:  # Defensive: never loop on a stale reference
                return None
            seen.add(song.id)
            song = self.queue_manager.get_song_at_offset(song.id, +1)
        return song

    def _take_turn_from_failed(self, candidate: Optional[QueueItem]) -> Optional[QueueItem]:
        """
        _step_past_failed(), and move the song that goes instead to the
        failed song's place in the queue.

        The failed song (and any failed songs right behind it) then stay
        next in line rather than falling behind the cursor: its singer keeps
        their turn, and if they fix the song -- replace it or retry it --
        they sing right after this one. Still broken by then, it steps aside
        again.

        For the paths that are about to start (or announce) the song this
        returns; anything that only displays the next song uses
        _step_past_failed() instead.
        """
        self._require_locked()
        chosen = self._step_past_failed(candidate)
        if candidate is not None and chosen is not None and chosen.id != candidate.id:
            self.logger.info(
                "Song %s failed to download; song %s goes ahead of it", candidate.id, chosen.id
            )
            self.queue_manager.move_before(chosen.id, candidate.id)
        return chosen

    def _play_song(self, song: QueueItem) -> bool:
        """
        Load and play a specific song.

        Assumes lock is already held.

        Args:
            song: Queue item to play

        Returns:
            True if playback started, False on error
        """
        self._require_locked()
        content_path = song.content_path
        if not content_path:
            self.logger.warning("No content path for song %s", song.id)
            self._set_state(PlaybackState.IDLE, "no content path")
            return False

        # Any pending "waiting for replacement" is moot once a song starts playing
        self._awaiting_replace_item_id = None

        # Reset notification flags for new song
        self._up_next_shown = False
        self._current_singer_shown = False

        try:
            self.logger.info("Loading song: %s by %s", song.metadata.title, song.user_name)

            # Set pitch for this song
            pitch = song.settings.pitch_semitones
            self.logger.debug(
                "_play_song: before set_pitch_shift, song=%s pitch=%s", song.id, pitch
            )
            try:
                self.streaming_controller.set_pitch_shift(pitch)
            except Exception as e:
                self.logger.warning("Could not set pitch shift: %s", e)

            # Apply loudness-normalization gain for this song, if measured
            try:
                self.streaming_controller.set_volume_gain_db(
                    song_tuning.lookup_volume_gain_db(
                        self.loudness_analyzer, self.config_manager, song.video_id
                    )
                )
            except Exception as e:
                self.logger.warning("Could not set volume gain: %s", e)

            # Load file into streaming controller (always start from beginning)
            self.logger.debug("_play_song: before load_file")
            try:
                self.streaming_controller.load_file(content_path)
            except Exception as e:
                self.logger.error("Failed to load file into streaming controller: %s", e)
                self._set_state(PlaybackState.ERROR, f"load failed: {e}")
                self._handle_error(song.id, f"Playback failed: {str(e)}")
                return False

            # Mark song ID as currently playing
            # NOTE: This is one of only 2 places current_song_id is mutated:
            #   1. _play_song() - sets it when starting playback
            #   2. on_song_end() - clears it when song finishes naturally
            # _stop_internal() does NOT clear it - the song is remembered for resume
            self.current_song_id = song.id
            self._start_pitch = pitch
            if self.pitch_log is not None:
                self.pitch_log.song_started(song)

            # Look up trailing-silence trim point for the monitor loop to act on
            self._current_trim_point = song_tuning.lookup_trim_point(
                self.silence_analyzer, self.config_manager, song.video_id
            )

            # Advance the queue cursor - this is the single place the cursor moves.
            # All navigation (skip, previous, jump_to_song) goes through _play_song(),
            # so the cursor always tracks what's playing.
            self.set_cursor(song.id)

            self._set_state(PlaybackState.PLAYING, f"playing: {song.metadata.title}")
            self._screens.leave_idle()
            return True

        except Exception as e:
            self.logger.error("Error loading song: %s", e, exc_info=True)
            self._set_state(PlaybackState.ERROR, f"error: {e}")
            self._handle_error(song.id, str(e))
            return False

    def pause(self) -> bool:
        """
        Pause playback.

        Returns:
            True if paused, False otherwise
        """
        with self._locked():
            if self.state != PlaybackState.PLAYING:
                self.logger.debug("Not playing, cannot pause")
                return False

            try:
                self.streaming_controller.pause()
                self._set_state(PlaybackState.PAUSED, "paused")
                return True
            except Exception as e:
                self.logger.error("Error pausing playback: %s", e, exc_info=True)
                return False

    def _stop_internal(self):
        """
        Stop playback but remember current song.

        Helper method for stopping playback. Assumes lock is already held.
        The current_song_id is preserved so that play() can resume from the same song.
        """
        self._require_locked()
        self.streaming_controller.stop_playback()
        self._overlay.set_base("")  # Clear the singer/up next overlay
        # Note: current_song_id is NOT cleared - we remember the song for resume
        self._set_state(PlaybackState.STOPPED, "stopped by operator")
        self._show_idle_screen_locked()

    def stop_playback(self) -> bool:
        """
        Stop current playback and return to idle state.
        Unlike skip(), this does not try to load the next song.
        Shows the idle interstitial screen.

        Returns:
            True if stopped, False otherwise
        """
        with self._locked():
            return self._stop_playback_locked()

    def _stop_playback_locked(self) -> bool:
        """stop_playback() for a caller that already holds the lock."""
        self._require_locked()

        # Cancel any pending transition
        if self._transition_timer:
            self._transition_timer.cancel()
            self._transition_timer = None
        self._next_song_pending = None

        if self.state in (PlaybackState.IDLE, PlaybackState.STOPPED):
            self.logger.debug("Already stopped, nothing to stop")
            return False

        self.logger.info("Stopping playback")
        try:
            # An explicit operator stop overrides any pending auto-resume
            self._awaiting_replace_item_id = None
            self._stop_internal()
            return True
        except Exception as e:
            self.logger.error("Error stopping playback: %s", e, exc_info=True)
            return False

    def apply_streaming_config(self) -> None:
        """
        Stop playback, rebuild the pipeline with fresh config, show idle.

        Called when an operator changes a setting the GStreamer pipeline
        only reads at build time (audio device, QR geometry, ...).

        All three steps run under ONE hold of self.lock. Done as three
        separate calls, another request thread could slip in between them:
        an operator Play landing after the stop would start a song that the
        rebuild then silently kills, leaving this controller in PLAYING
        over an idle pipeline; and the idle screen would be drawn over
        whatever state that left behind.

        The cost is that get_status() polls, and every other playback
        operation, block for the length of the teardown and rebuild. That
        is acceptable for a rare, operator-initiated config change, and is
        the point: nothing else should touch playback while the pipeline
        it drives does not exist.

        Raises whatever reinitialize_pipeline() raises, so the caller can
        report that the setting was saved but not applied.
        """
        # Join the bus watcher threads BEFORE taking self.lock. A watcher
        # can be inside the EOS callback (on_song_end) waiting for self.lock;
        # joining it while holding the lock would stall for the join timeout
        # and strand that thread. reinitialize_pipeline() joins them too,
        # but by then there are none left, and it starts fresh ones. See the
        # lock-ordering note in StreamingController.__init__.
        self.streaming_controller.stop_bus_polling()

        with self._locked():
            self._stop_playback_locked()
            self.streaming_controller.reinitialize_pipeline()
            self._show_idle_screen_locked()

    def _navigate(self, offset: int, record_history: bool) -> bool:
        """
        Move to the song `offset` positions away from the current one.

        Shared by skip() (offset=+1) and previous() (offset=-1) so both
        directions behave the same way:

        - While a song is actively playing/paused, this shows the "up next"
          transition interstitial before starting the target song - same as
          a natural end-of-song - so the next singer gets a heads up
          regardless of which direction the operator navigated.
        - While already in PlaybackState.TRANSITION (the interstitial from a
          previous skip/previous, or from a natural end-of-song, is still
          showing), navigates relative to the pending "up next" song,
          cancels the in-flight transition timer, and re-shows the
          interstitial for the new target - so the operator isn't locked
          out of navigation during the wait.
        - Otherwise (e.g. STOPPED while waiting on a replace_song()
          download) there's no active performance to protect continuity
          for, so this switches immediately with no interstitial delay.

        Args:
            offset: +1 to move forward (skip), -1 to move backward (previous)
            record_history: whether to record performance history for the
                song being navigated away from (skip does this, previous
                does not)

        Returns:
            True if navigation happened, False if there's nowhere to go

        Note: Assumes lock is already held.
        """
        self._require_locked()
        if self.state == PlaybackState.TRANSITION:
            if not self._next_song_pending:
                self.logger.warning("In transition but no pending song, cannot navigate")
                return False

            # The pending "up next" song is what the operator sees on screen,
            # so navigation is relative to it, not to the cursor (which still
            # points at the song that actually finished/was skipped).
            reference_song_id = self._next_song_pending.id
            target_song = self.queue_manager.get_song_at_offset(reference_song_id, offset)
            if offset > 0:
                target_song = self._take_turn_from_failed(target_song)
            if not target_song:
                self.logger.info("No song available to navigate to")
                return False

            self.logger.info(
                "Navigating during transition: %s -> %s", reference_song_id, target_song.id
            )

            # Cancel the in-flight transition timer - we're re-targeting it,
            # otherwise the old pending song would start on top of this one.
            if self._transition_timer:
                self._transition_timer.cancel()
                self._transition_timer = None
            self._next_song_pending = None

            # Re-show the interstitial for the newly-chosen song.
            self._show_transition_or_end(
                finished_song_id=self._cursor.get(),
                next_song=target_song,
                step_past_failed=False,
            )
            return True

        if not self.current_song_id:
            if offset > 0:
                self.logger.info("No current song, trying to start playback")
                return self._load_and_play_next()
            self.logger.info("No current song, cannot navigate")
            return False

        # Only skip needs the current song's data (for history recording);
        # previous doesn't touch it.
        current_song = None
        if record_history:
            current_song = self.queue_manager.get_item(self.current_song_id)
            if not current_song:
                self.logger.error("Current song ID %s not found", self.current_song_id)
                return False

        target_song = self.queue_manager.get_song_at_offset(self.current_song_id, offset)
        if offset > 0:
            target_song = self._take_turn_from_failed(target_song)
        if not target_song:
            self.logger.info("No song available to navigate to")
            return False

        if current_song:
            current_position = self.streaming_controller.get_position() or 0
            self._record_history_if_eligible(current_song, current_position)

        self.logger.debug(
            "_navigate: before stop_playback, current=%s target=%s offset=%s",
            self.current_song_id,
            target_song.id,
            offset,
        )
        self.streaming_controller.stop_playback()

        if self.state in (PlaybackState.PLAYING, PlaybackState.PAUSED):
            # Active performance - show the interstitial, same flow as a
            # natural end-of-song, before starting the target song.
            previous_song_id = self.current_song_id
            self._show_transition_or_end(
                finished_song_id=previous_song_id,
                next_song=target_song,
                step_past_failed=False,
            )
        else:
            # Nothing actually playing (e.g. stopped mid replace-download) -
            # switch immediately, no interstitial delay needed.
            self._play_song(target_song)

        return True

    def _skip_internal(self) -> bool:
        """
        Skip to next song (internal version, assumes lock is held).

        Navigation-based: moves to next song in queue without marking current as played.
        The current song remains in the queue and can be navigated back to.

        Returns:
            True if skipped, False if no next song available
        """
        self._require_locked()
        self.logger.info("Skipping current song")
        return self._navigate(offset=+1, record_history=True)

    def skip(self) -> bool:
        """
        Skip to next song.

        Navigation-based: moves to next song in queue without marking current as played.
        The current song remains in the queue and can be navigated back to.

        Returns:
            True if skipped, False if no next song available
        """
        with self._locked():
            return self._skip_internal()

    def end_turn(self, item_id: int) -> bool:
        """
        End a singer's turn early: stop their song and move on to the next
        singer.

        For a singer who can't carry on with their own song (wrong song,
        wrong version, a bad karaoke track, not ready). It moves on the way a
        natural end does -- the "up next" interstitial, or the end-of-queue
        screen if nobody is next -- but the attempt is never written to
        performance history. The song stays in the queue, so the host can
        give the singer another go with Play Next.

        Only item_id's turn ends, and only while it is playing or paused, so a
        request that arrives after that song has already ended can never end
        the next singer's turn instead.

        Args:
            item_id: Queue item whose turn the caller means to end

        Returns:
            True if the turn ended, False if it is not the song playing now
        """
        with self._locked():
            if self.current_song_id != item_id or self.state not in (
                PlaybackState.PLAYING,
                PlaybackState.PAUSED,
            ):
                return False
            position = self.streaming_controller.get_position() or 0
            self.logger.info("Ending turn for song %s at %ss on request", item_id, position)
            self.streaming_controller.stop_playback()
            self._complete_current_song(final_position=position, record_history=False)
            return True

    def jump_to_song(self, item_id: int) -> bool:
        """
        Jump to and play a song immediately at its current queue position.

        Stops any currently playing song and starts playing the target song.
        Does not reorder the queue.

        Args:
            item_id: ID of the queue item to jump to

        Returns:
            True if successful, False otherwise
        """
        with self._locked():
            self.logger.info("Jumping to song ID %s", item_id)

            # Get the song from queue
            song = self.queue_manager.get_item(item_id)
            if not song:
                self.logger.warning("Song %s not found in queue", item_id)
                return False

            # Check if song is ready
            if song.content_status != QueueManager.STATUS_READY:
                self.logger.warning(
                    "Song %s is not ready (status: %s)", item_id, song.content_status
                )
                return False

            # Stop current playback if something is playing
            if self.current_song_id:
                self.streaming_controller.stop_playback()

            self.logger.info("Playing song %s at position %s", item_id, song.position)

            # Load and play the song (always from beginning)
            return self._play_song(song)

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
        Replace the video for a queue item in place (e.g. wrong version added).

        Position and attribution are untouched. If the item is currently
        playing, playback stops immediately (the wrong content shouldn't
        keep playing) and auto-resumes the same item once the replacement
        finishes downloading - the operator/singer doesn't have to babysit it.

        Args:
            item_id: ID of the queue item to replace
            video_id: Opaque video ID of the replacement video
            title: Replacement song title
            duration_seconds: Duration in seconds (optional)
            thumbnail_url: Thumbnail URL (optional)
            channel: Channel/artist name (optional)

        Returns:
            True if successful, False if the item wasn't found
        """
        with self._locked():
            item = self.queue_manager.get_item(item_id)
            if not item:
                self.logger.warning("Cannot replace: queue item %s not found", item_id)
                return False

            is_current = self.current_song_id == item_id

            if is_current:
                self.logger.info("Replacing currently playing song %s", item_id)
                self._overlay.set_base("")
                self._set_state(PlaybackState.STOPPED, "replacing current song")
                self._awaiting_replace_item_id = item_id
                self._screens.show_message(f"Loading new video for {item.user_name}...")

            return self.queue_manager.replace_song(
                item_id, video_id, title, duration_seconds, thumbnail_url, channel
            )

    def remove_song(self, item_id: int) -> bool:
        """
        Remove a song from the queue, keeping playback consistent with it.

        Playback refers to queue items by ID: the song playing now, the one
        the "up next" screen announces, and the cursor (the last song
        started). Deleting the row behind any of them without telling
        playback leaves it looking for a song that is gone, and the queue's
        fallback for a missing reference is the top of the queue -- a song
        sung hours ago. So:

        - The song playing (or paused) now stops and playback moves on to
          the next singer, as at a natural end, without recording a
          performance: the singer withdrew it.
        - A song the "up next" screen announces is replaced there by the
          song after it.
        - The cursor moves back to the song before, so songs already sung
          stay sung.

        Args:
            item_id: ID of the queue item to remove

        Returns:
            True if removed, False if the item wasn't found
        """
        with self._locked():
            if self.queue_manager.get_item(item_id) is None:
                return False

            if self.current_song_id == item_id:
                if self.state in (PlaybackState.PLAYING, PlaybackState.PAUSED):
                    self.logger.info("Removing song %s while it plays; moving on", item_id)
                    self.streaming_controller.stop_playback()
                    self._complete_current_song(record_history=False)
                else:
                    # Stopped (remembered for resume) or awaiting a
                    # replacement: there is nothing left to resume.
                    self.current_song_id = None
            if self._awaiting_replace_item_id == item_id:
                self._awaiting_replace_item_id = None

            announced = (
                self.state == PlaybackState.TRANSITION
                and self._next_song_pending is not None
                and self._next_song_pending.id == item_id
            )
            # Both lookups need the row, so they come before the delete.
            song_after = self.queue_manager.get_song_at_offset(item_id, +1) if announced else None
            if self._cursor.get() == item_id:
                song_before = self.queue_manager.get_song_at_offset(item_id, -1)
                if song_before is not None:
                    self.set_cursor(song_before.id)
                else:
                    self.clear_cursor()

            if not self.queue_manager.remove_song(item_id):
                return False

            if announced:
                if self._transition_timer:
                    self._transition_timer.cancel()
                    self._transition_timer = None
                self._next_song_pending = None
                if song_after is not None:
                    self._show_transition_or_end(next_song=song_after)
                else:
                    self._set_state(PlaybackState.IDLE, "queue exhausted")
                    self._screens.show_end_of_queue()
            return True

    def previous(self) -> bool:
        """
        Go to previous song in the queue.

        Navigation-based: moves to previous song by position. Mirrors skip()
        in the backward direction, including showing the "up next"
        transition interstitial and working while that interstitial is
        already showing.

        Returns:
            True if moved to previous song, False if no previous song
        """
        with self._locked():
            self.logger.info("Going to previous song")
            return self._navigate(offset=-1, record_history=False)

    def move_down(self, item_id: int) -> MoveResult:
        """Move a song down one position, under the playback lock.

        Does not affect the currently playing song. See QueueManager.move_down.
        """
        with self._locked():
            return self.queue_manager.move_down(item_id)

    def move_up(self, item_id: int) -> MoveResult:
        """Move a song up one position, under the playback lock.

        Does not affect the currently playing song. See QueueManager.move_up.
        """
        with self._locked():
            return self.queue_manager.move_up(item_id)

    def move_to_next(self, item_id: int) -> bool:
        """Move a song to play next, i.e. after the current song.

        Supplies the current song as the reference; with none (idle), the song
        goes to the front. See QueueManager.move_after.
        """
        with self._locked():
            self.logger.info("Moving song %s to play next", item_id)
            is_currently_playing = (
                self.current_song_id is not None and self.current_song_id == item_id
            )

            new_position = self.queue_manager.move_after(item_id, self.current_song_id)
            if new_position is None:
                return False

            # current_song_id needs no update: reordering changes positions, not IDs.
            if is_currently_playing:
                self.logger.info("Currently playing song moved to position %s", new_position)

            return True

    def move_to_end(self, item_id: int) -> bool:
        """Move a song to the end of the queue, under the playback lock.

        Playback continues if it is the current song. See QueueManager.move_to_end.
        """
        with self._locked():
            is_currently_playing = (
                self.current_song_id is not None and self.current_song_id == item_id
            )

            new_position = self.queue_manager.move_to_end(item_id)
            if new_position is None:
                return False

            # current_song_id needs no update: reordering changes positions, not IDs.
            if is_currently_playing:
                self.logger.info("Currently playing song moved to position %s", new_position)

            return True

    def _handle_error(self, item_id: int, error_message: str):
        """
        Handle playback error.

        Assumes lock is already held (called from _play_song which is called with lock held).

        Args:
            item_id: ID of the queue item that failed
            error_message: Error message
        """
        self._require_locked()
        self.logger.error("Playback error for item %s: %s", item_id, error_message)

        # Stop playback so operator can investigate
        self._set_state(PlaybackState.STOPPED, f"error: {error_message}")
        self.current_song_id = None
        self._show_idle_screen_locked()

    def on_song_end(self):
        """Called when current song ends (EOS)."""
        with self._locked():
            self._complete_current_song()

    def _complete_current_song(
        self, final_position: Optional[int] = None, record_history: bool = True
    ):
        """
        Finish the current song: record history, reset pitch/overlay, clear
        current_song_id, and transition to the next song or idle.

        Shared by the natural end-of-stream path (on_song_end) and the
        early trailing-silence skip (_check_trailing_silence_skip), which
        stops playback itself before calling this. Assumes lock is already
        held.

        Args:
            final_position: Position to record for history purposes. If
                None, queries the streaming controller's current position
                (the natural-EOS case, where GStreamer is already stopped
                at the real end of the file).
            record_history: False for a song that was withdrawn rather than
                sung (remove_song()), which is not a performance whatever
                its length.
        """
        self._require_locked()
        finished_song_id = None

        if self.current_song_id:
            finished_song_id = self.current_song_id

            # Get song data for history recording
            finished_song = self.queue_manager.get_item(finished_song_id)
            if finished_song:
                self.logger.info("Song ended: %s", finished_song.metadata.title)

                record_position = (
                    final_position
                    if final_position is not None
                    else (self.streaming_controller.get_position() or 0)
                )

                # Record history if threshold met
                if record_history:
                    self._record_history_if_eligible(finished_song, record_position)
            else:
                self.logger.warning("Finished song ID %s not found", finished_song_id)
        else:
            self.logger.info("Song ended: unknown")

        # Reset pitch and volume gain
        self.streaming_controller.set_pitch_shift(0)
        self.streaming_controller.set_volume_gain_db(0.0)

        # Clear current song ID (natural end of song, transitioning to next or idle)
        # NOTE: This is one of only 2 places current_song_id is mutated:
        #   1. _play_song() - sets it when starting playback
        #   2. _complete_current_song() - clears it when song finishes
        # _stop_internal() does NOT clear it - the song is remembered for resume
        # The queue cursor remains set to this song so _show_transition_or_end
        # can find the next song relative to it.
        self.current_song_id = None
        self._current_trim_point = None

        # Check for next song and show transition.
        # Use the cursor (which still points to the song that just finished)
        # to find the next song by position.
        self._show_transition_or_end(finished_song_id=finished_song_id)

    def _show_transition_or_end(
        self,
        finished_song_id: Optional[int] = None,
        next_song: Optional[QueueItem] = None,
        step_past_failed: bool = True,
    ):
        """
        Show transition screen for next song, or end-of-queue screen.

        Called after a song ends. Shows appropriate interstitial and schedules
        the next song to start after the transition duration.

        Respects queue order: by default only considers the immediate next
        song by position. If that song isn't ready, goes idle (auto-start
        will pick it up when ready).

        Args:
            finished_song_id: ID of the song that just finished (used to find
                the next song by position when next_song is not given).
            next_song: explicit song to transition to, overriding the default
                "next song by position after finished_song_id" derivation.
                Used by backward navigation (previous()) and by navigation
                performed while the interstitial is already showing (see
                _navigate()), where the target isn't simply
                finished_song_id + 1.
            step_past_failed: whether a next song whose download failed for
                good gives way to the song after it (see
                _take_turn_from_failed()). False for a caller that has
                already done that itself.

        Note: Called with lock held.
        """
        self._require_locked()
        # Whoever was singing is no longer singing. The overlay says who is on
        # *now*, so it must not survive into the transition screen or the start
        # of the next song; _play_song brings it back a few seconds in.
        self._overlay.set_base("")

        if next_song is None:
            # Get next song by position after the one that finished.
            # If finished_song_id is None, we have no reference point, so show end screen
            if finished_song_id is None:
                self._set_state(PlaybackState.IDLE, "queue exhausted")
                self._screens.show_end_of_queue()
                return

            next_song = self.queue_manager.get_song_at_offset(finished_song_id, +1)

        if step_past_failed:
            next_song = self._take_turn_from_failed(next_song)

        if not next_song:
            # No more songs - show end-of-queue screen
            self._set_state(PlaybackState.IDLE, "queue exhausted")
            self._screens.show_end_of_queue()
            return

        if next_song.content_status != QueueManager.STATUS_READY:
            # Next song exists but isn't ready yet - go idle, auto-start will handle it
            self._set_state(PlaybackState.IDLE, f"next song not ready ({next_song.content_status})")
            self._screens.show_end_of_queue()
            return

        # Store next song for transition
        self._next_song_pending = next_song

        # Get transition duration from config (default 5 seconds)
        transition_duration = self.config_manager.get("transition_duration_seconds")
        if transition_duration is None:
            transition_duration = 5
        else:
            try:
                transition_duration = int(transition_duration)
            except (ValueError, TypeError):
                transition_duration = 5

        # Show transition screen
        self._set_state(
            PlaybackState.TRANSITION, f"next: {next_song.user_name} ({transition_duration}s)"
        )
        # Use extracted metadata if available, otherwise fall back to original title
        song_title = next_song.metadata.song_name or next_song.metadata.title
        artist = next_song.metadata.artist
        self._screens.show_transition(
            singer_name=next_song.user_name, song_title=song_title, artist=artist
        )

        # Schedule the next song to start after transition
        if self._transition_timer:
            self._transition_timer.cancel()

        def start_next_song():
            self._on_transition_complete()

        self._transition_timer = threading.Timer(transition_duration, start_next_song)
        self._transition_timer.daemon = True
        self._transition_timer.start()

    def _on_transition_complete(self):
        """Called when transition timer fires to start the next song."""
        with self._locked():
            if self.state != PlaybackState.TRANSITION:
                self.logger.debug("Transition complete but state changed, ignoring")
                return

            if not self._next_song_pending:
                self.logger.warning("Transition complete but no pending song")
                self._set_state(PlaybackState.IDLE, "no pending song after transition")
                return

            next_song = self._next_song_pending
            self._next_song_pending = None

            self.logger.info("Transition complete, starting: %s", next_song.metadata.title)
            self._play_song(next_song)

    # =========================================================================
    # Interstitial Display
    # =========================================================================

    def show_idle_screen(self):
        """Show the idle screen interstitial with QR code instructions."""
        with self._locked():
            self._show_idle_screen_locked()

    def _show_idle_screen_locked(self):
        """show_idle_screen() for a caller that already holds the lock."""
        self._require_locked()
        self._screens.show_idle(self._idle_queue_summary())

    def _idle_queue_summary(self) -> QueueSummary:
        """
        Summarize the songs Play would work through from here.

        That is the stopped song, if one is remembered (Play resumes it),
        then everything after it; with none, everything after the cursor.

        Assumes lock is already held.
        """
        self._require_locked()
        queue = self.queue_manager.get_queue()
        ids = [item.id for item in queue]
        if self.current_song_id in ids:
            start = ids.index(self.current_song_id)
        else:
            cursor = self.get_cursor()
            start = ids.index(cursor) + 1 if cursor in ids else 0
        return QueueSummary.from_items(queue[start:])

    def update_qr_overlay(self, image_path: str):
        """
        Point the display's QR overlay at a new image.

        Goes through here, under self.lock, rather than straight to the
        streaming controller, so it cannot interleave with
        apply_streaming_config(). A rebuild replaces the OverlayController,
        copying the old one's QR path into the new one; a QR update landing
        mid-rebuild could record its path on the old, discarded controller
        after that copy, leaving the previous QR -- for a token that has
        just been revoked -- on screen.
        """
        with self._locked():
            self.streaming_controller.update_qr_overlay(image_path)

    def get_status(self) -> PlaybackStatus:
        """
        Get current playback status.

        Only briefly holds self.lock to snapshot (state, current_song_id) for
        a consistent view; the position query (GStreamer) and queue lookup
        (SQLite) below are blocking I/O and run outside the lock so status
        polls don't contend with in-progress song transitions (e.g.
        _play_song() holding the lock across load_file()).

        Because the snapshot is taken before the I/O, the song can end or a
        new one can start while this method is running. That's acceptable
        for a status read (a UI poll, not a control decision), but it means
        current_song_id may no longer match the live queue by the time
        get_item() runs -- handled below the same way a missing item is
        always handled.

        Returns:
            PlaybackStatus with the state, the current QueueItem (or None)
            and the position in seconds
        """
        with self._locked():
            state = self.state
            current_song_id = self.current_song_id

        position = None
        if state in (PlaybackState.PLAYING, PlaybackState.PAUSED):
            position = self.streaming_controller.get_position()

        # Query current song data from database (always fresh)
        current_song = None
        if current_song_id:
            current_song = self.queue_manager.get_item(current_song_id)

        return PlaybackStatus(state=state, current_song=current_song, position_seconds=position)

    def set_pitch(self, semitones: int, changed_by: Optional[str] = None) -> bool:
        """
        Set pitch adjustment for current song.

        Args:
            semitones: Pitch adjustment in semitones
            changed_by: User id of whoever made the change, if known; recorded
                with the pitch_change event

        Returns:
            True if set, False if no current song
        """
        with self._locked():
            if not self.current_song_id:
                self.logger.warning("No current song to adjust pitch")
                return False

            if self.pitch_log is not None:
                self._log_pitch_change(semitones, changed_by)

            # Update in queue database
            self.queue_manager.update_pitch(self.current_song_id, semitones)

            # Apply to streaming controller
            self.streaming_controller.set_pitch_shift(semitones)

            self.logger.info("Set pitch to %s semitones for current song", semitones)
            return True

    def _log_pitch_change(self, semitones: int, changed_by: Optional[str]) -> None:
        """Log a mid-song pitch change. Assumes lock is held; never raises."""
        self._require_locked()
        try:
            song = self.queue_manager.get_item(self.current_song_id)
            if song is None:
                return
            position = self.streaming_controller.get_position()
        except Exception:
            self.logger.warning("Could not read state to record pitch change", exc_info=True)
            return
        self.pitch_log.pitch_changed(
            song,
            semitones,
            phase=PHASE_PLAYING,
            changed_by=changed_by,
            start_semitones=self._start_pitch,
            position_seconds=position,
        )

    def restart(self) -> SeekResult:
        """
        Restart the current song from the beginning.

        Returns:
            SeekResult: SEEKED at position 0, NOTHING_PLAYING, or FAILED
        """
        with self._locked():
            if not self.current_song_id or self.state not in (
                PlaybackState.PLAYING,
                PlaybackState.PAUSED,
            ):
                self.logger.debug("Nothing playing, nothing to restart")
                return SeekResult(SeekOutcome.NOTHING_PLAYING)

            self.logger.info("Restarting song from beginning")
            if not self.streaming_controller.seek(0):
                return SeekResult(SeekOutcome.FAILED)
            return SeekResult(SeekOutcome.SEEKED, position=0)

    def seek_relative(self, delta_seconds: int) -> SeekResult:
        """
        Seek forward or backward by a relative amount.

        Args:
            delta_seconds: Seconds to seek (positive = forward, negative = backward)

        Returns:
            SeekResult: SEEKED with the new position, NOTHING_PLAYING, or FAILED
        """
        with self._locked():
            if not self.current_song_id or self.state not in (
                PlaybackState.PLAYING,
                PlaybackState.PAUSED,
            ):
                self.logger.debug("Nothing playing, nothing to seek")
                return SeekResult(SeekOutcome.NOTHING_PLAYING)

            current_position = self.streaming_controller.get_position()
            if current_position is None:
                current_position = 0

            new_position = max(0, current_position + delta_seconds)

            # Clamp to song duration if available
            current_song = self.queue_manager.get_item(self.current_song_id)
            if current_song:
                duration = current_song.metadata.duration_seconds
                if duration and new_position > duration:
                    new_position = max(0, duration - 1)  # Seek to near the end

            self.logger.info(
                "Seeking from %ss to %ss (delta: %+ds)",
                current_position,
                new_position,
                delta_seconds,
            )
            if not self.streaming_controller.seek(new_position):
                return SeekResult(SeekOutcome.FAILED)
            return SeekResult(SeekOutcome.SEEKED, position=new_position)

    def _record_history_if_eligible(self, song: QueueItem, position: int) -> None:
        """
        Record a performance in history if it played long enough to count.

        Shared by the skip path (_navigate) and the end-of-song path
        (_complete_current_song), which previously ran identical copies of
        this. The thresholds are read from config here and handed to
        history_policy, which decides on numbers alone.

        Assumes lock is already held.

        Args:
            song: Queue item being recorded
            position: Playback position to record as the played duration
        """
        self._require_locked()
        if not self.history_manager:
            return

        threshold_pct, threshold_seconds = history_policy.history_thresholds(self.config_manager)
        if not history_policy.should_record_history(
            song.metadata.duration_seconds, position, threshold_pct, threshold_seconds
        ):
            return

        completion_pct = history_policy.calculate_completion_percentage(
            position, song.metadata.duration_seconds
        )
        history_policy.record_performance_history(
            self.history_manager,
            song,
            position,
            position,
            completion_pct,
            self.config_manager.get("suggestion_theme") or None,
        )

    def shutdown(self):
        """Shutdown the playback controller and cleanup all resources."""
        self.logger.info("Shutting down playback controller")
        # Take the monitor out of play before anything else, but cancel the
        # transition timer before waiting for its thread -- otherwise the
        # timer could fire and start a song during the join.
        self._monitor.request_stop()

        # Cancel transition timer
        if self._transition_timer:
            self._transition_timer.cancel()
            self._transition_timer = None

        # Stop monitor thread
        self._monitor.join()

        # Stop streaming
        if self.streaming_controller:
            self.streaming_controller.stop()

        self.logger.info("Playback controller shut down")
