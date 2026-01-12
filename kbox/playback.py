"""
Playback controller for kbox.

Orchestrates playback, manages state transitions, and handles error recovery.
"""

import logging
import threading
from enum import Enum
from typing import Any, Dict, Optional

from .models import QueueItem
from .queue import QueueManager


class PlaybackState(Enum):
    """Playback state enumeration."""

    STOPPED = "stopped"  # Operator stopped or initial state - no auto-start
    IDLE = "idle"  # Queue exhausted naturally - will auto-start when songs added
    PLAYING = "playing"
    PAUSED = "paused"
    TRANSITION = "transition"  # Between songs, showing interstitial
    ERROR = "error"


class PlaybackController:
    """Orchestrates playback and manages state."""

    def __init__(
        self,
        queue_manager: QueueManager,
        streaming_controller,  # StreamingController - avoid circular import
        config_manager,
        history_manager=None,  # HistoryManager - avoid circular import, optional for tests
    ):
        """
        Initialize PlaybackController.

        Args:
            queue_manager: QueueManager instance
            streaming_controller: StreamingController instance
            config_manager: ConfigManager instance
            history_manager: HistoryManager instance (optional, for tests)
        """
        self.queue_manager = queue_manager
        self.streaming_controller = streaming_controller
        self.config_manager = config_manager
        self.history_manager = history_manager
        self.logger = logging.getLogger(__name__)
        self.state = PlaybackState.STOPPED  # Start in STOPPED so operator must press Play
        self.current_song_id: Optional[int] = None
        self.lock = threading.Lock()

        # Transition/interstitial state
        self._transition_timer: Optional[threading.Timer] = None
        self._next_song_pending = None  # Song to play after transition

        # Interstitial generator (lazy-initialized)
        self._interstitial_generator = None

        # Background monitor thread (handles notifications, auto-start, etc.)
        self._monitor_thread = None
        self._monitoring = False
        self._up_next_shown = False  # Track if "up next" notification was shown for current song
        self._current_singer_shown = (
            False  # Track if "current singer" notification was shown for current song
        )

        # Overlay management - PlaybackController owns overlay state
        self._base_overlay_text = ""  # Persistent overlay (current singer, up next, etc.)
        self._notification_timer: Optional[threading.Timer] = None
        self._notification_lock = threading.Lock()

        self._start_monitor()

        # Set EOS callback
        self.streaming_controller.set_eos_callback(self.on_song_end)

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

    def _set_base_overlay(self, text: str):
        """
        Set the base (persistent) overlay text.

        This is the text that should be shown when no transient notification
        is active (e.g., "Now singing: Alice", "Up next: Bob").

        Args:
            text: The base overlay text, or empty string to clear
        """
        with self._notification_lock:
            self._base_overlay_text = text
            # Only update display if no notification is active
            if self._notification_timer is None:
                self.streaming_controller.set_overlay_text(text)

    def show_notification(self, text: str, duration_seconds: float = 5.0):
        """
        Show a transient notification that auto-hides and restores the base overlay.

        Args:
            text: Notification text to display
            duration_seconds: How long to show the notification (default 5s)
        """
        with self._notification_lock:
            # Cancel any pending timer
            if self._notification_timer:
                self._notification_timer.cancel()
                self._notification_timer = None

            # Show the notification
            self.streaming_controller.set_overlay_text(text)
            self.logger.info("Showing notification: %s", text)

            # Schedule restoration of base overlay
            def restore_base_overlay():
                with self._notification_lock:
                    self._notification_timer = None
                    self.streaming_controller.set_overlay_text(self._base_overlay_text)
                    self.logger.debug("Restored base overlay: %s", self._base_overlay_text)

            self._notification_timer = threading.Timer(duration_seconds, restore_base_overlay)
            self._notification_timer.daemon = True
            self._notification_timer.start()

    def _start_monitor(self):
        """Start background thread to monitor playback state and react accordingly."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        def monitor():
            """Periodically check playback state and handle notifications, auto-start, etc."""
            import time

            while self._monitoring:
                try:
                    if self.state == PlaybackState.PLAYING and self.current_song_id:
                        position = self.streaming_controller.get_position()
                        if position is not None:
                            # Check if we should show "current singer" notification at start
                            self._check_current_singer_notification(position)
                            # Check if we should show "up next" notification near end
                            self._check_up_next_notification(position)
                    elif self.state == PlaybackState.IDLE:
                        # Auto-start playback if we're idle and there are ready songs
                        # This handles the "that's all" screen -> someone adds a song case
                        self._check_auto_start_when_idle()
                    time.sleep(2)  # Check every 2 seconds
                except Exception as e:
                    self.logger.error("Error in monitor: %s", e, exc_info=True)
                    time.sleep(5)  # Wait longer on error

        self._monitoring = True
        self._monitor_thread = threading.Thread(target=monitor, daemon=True, name="PlaybackMonitor")
        self._monitor_thread.start()
        self.logger.info("Playback monitor started")

    def _check_auto_start_when_idle(self):
        """
        Check if we're idle but have ready songs, and auto-start playback.

        This handles the case where we're on the "that's all" screen and
        someone adds a song - once it finishes downloading, playback
        should start automatically without requiring operator intervention.
        """
        if self.queue_manager.get_ready_song_at_offset(None, 0):
            self.logger.info("Idle with ready songs, auto-starting playback")
            self.play()

    def _check_current_singer_notification(self, current_position: int):
        """
        Check if song just started and show "current singer" persistent overlay.

        Args:
            current_position: Current playback position in seconds
        """
        if self._current_singer_shown:
            return  # Already shown for this song

        if not self.current_song_id:
            return

        # Show persistent overlay at the very start of the song (within first 3 seconds)
        # This allows the singer to see their name even if they missed the interstitial
        # and lets the audience learn their name
        if current_position <= 3:
            current_song = self.queue_manager.get_item(self.current_song_id)
            if current_song:
                overlay_text = f"Now singing: {current_song.user_name}"
                self._set_base_overlay(overlay_text)
                self._current_singer_shown = True
                self.logger.debug("Set current singer overlay for: %s", current_song.user_name)

    def _check_up_next_notification(self, current_position: int):
        """
        Check if song is ending and update overlay to show "up next".

        Args:
            current_position: Current playback position in seconds
        """
        if self._up_next_shown:
            return  # Already shown for this song

        if not self.current_song_id:
            return

        # Get current song data to check duration
        current_song = self.queue_manager.get_item(self.current_song_id)
        if not current_song:
            return

        duration = current_song.metadata.duration_seconds
        if not duration or duration <= 0:
            return

        # Update overlay text when 15 seconds or less remain
        time_remaining = duration - current_position
        if time_remaining <= 15:
            # Get next song
            next_song = self.queue_manager.get_ready_song_at_offset(self.current_song_id, +1)
            if next_song:
                overlay_text = f"Up next: {next_song.user_name}"
                self._set_base_overlay(overlay_text)
                self._up_next_shown = True
                self.logger.debug("Updated overlay to up next for: %s", next_song.user_name)

    def play(self) -> bool:
        """
        Start or resume playback.

        Returns:
            True if playback started, False otherwise
        """
        with self.lock:
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

            # Start new song
            return self._load_and_play_next()

    def _load_and_play_next(self) -> bool:
        """Load first ready song in the queue and start playback."""
        next_song = self.queue_manager.get_ready_song_at_offset(None, 0)

        if not next_song:
            self._set_state(PlaybackState.IDLE, "no ready songs")
            return False

        return self._play_song(next_song)

    def _play_song(self, song: QueueItem) -> bool:
        """
        Load and play a specific song.

        Args:
            song: Queue item to play

        Returns:
            True if playback started, False on error
        """
        # Check if file exists
        download_path = song.download_path
        if not download_path:
            self.logger.warning("No download path for song %s", song.id)
            self._set_state(PlaybackState.IDLE, "no download path")
            return False

        # Reset notification flags for new song
        self._up_next_shown = False
        self._current_singer_shown = False

        try:
            self.logger.info("Loading song: %s by %s", song.metadata.title, song.user_name)

            # Set pitch for this song
            pitch = song.settings.pitch_semitones
            self.logger.debug(
                "[DEBUG] _play_song: before set_pitch_shift, song=%s pitch=%s", song.id, pitch
            )
            try:
                self.streaming_controller.set_pitch_shift(pitch)
            except Exception as e:
                self.logger.warning("Could not set pitch shift: %s", e)

            # Load file into streaming controller (always start from beginning)
            self.logger.debug("[DEBUG] _play_song: before load_file")
            try:
                self.streaming_controller.load_file(download_path)
            except Exception as e:
                self.logger.error("Failed to load file into streaming controller: %s", e)
                self._set_state(PlaybackState.ERROR, f"load failed: {e}")
                self._handle_error(song.id, f"Playback failed: {str(e)}")
                return False

            # Mark song ID as currently playing
            # NOTE: This is one of only 3 places current_song_id is mutated:
            #   1. _play_song() - sets it when starting playback
            #   2. _stop_internal() - clears it when stopping playback
            #   3. on_song_end() - clears it when song finishes naturally
            self.current_song_id = song.id
            self._set_state(PlaybackState.PLAYING, f"playing: {song.metadata.title}")
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
        with self.lock:
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
        Stop playback and clear current song.

        Helper method for stopping playback. Assumes lock is already held.
        This is the ONLY place (besides _play_song) where current_song_id should be mutated.
        """
        self.streaming_controller.stop_playback()
        self._set_base_overlay("")  # Clear the singer/up next overlay
        self.current_song_id = None
        self._set_state(PlaybackState.STOPPED, "stopped by operator")
        self.show_idle_screen()

    def stop_playback(self) -> bool:
        """
        Stop current playback and return to idle state.
        Unlike skip(), this does not try to load the next song.
        Shows the idle interstitial screen.

        Returns:
            True if stopped, False otherwise
        """
        with self.lock:
            # Cancel any pending transition
            if self._transition_timer:
                self._transition_timer.cancel()
                self._transition_timer = None
            self._next_song_pending = None

            if not self.current_song_id and self.state in (
                PlaybackState.IDLE,
                PlaybackState.STOPPED,
            ):
                self.logger.debug("Already stopped, nothing to stop")
                return False

            self.logger.info("Stopping playback")
            try:
                self._stop_internal()
                return True
            except Exception as e:
                self.logger.error("Error stopping playback: %s", e, exc_info=True)
                return False

    def _skip_internal(self) -> bool:
        """
        Skip to next song (internal version, assumes lock is held).

        Navigation-based: moves to next song in queue without marking current as played.
        The current song remains in the queue and can be navigated back to.

        Returns:
            True if skipped, False if no next song available
        """
        self.logger.info("Skipping current song")

        if not self.current_song_id:
            self.logger.info("No current song, trying to start playback")
            return self._load_and_play_next()

        # Get current song data
        current_song = self.queue_manager.get_item(self.current_song_id)
        if not current_song:
            self.logger.error("Current song ID %s not found", self.current_song_id)
            return False

        # Get next song after current
        next_song = self.queue_manager.get_ready_song_at_offset(self.current_song_id, +1)

        if not next_song:
            self.logger.info("No next song available to skip to")
            return False

        # Record history if threshold met
        current_position = self.streaming_controller.get_position() or 0
        if self.history_manager and self._should_record_history(
            current_song.metadata.duration_seconds, current_position
        ):
            completion_pct = self._calculate_completion_percentage(
                current_position, current_song.metadata.duration_seconds
            )
            self._record_performance_history(
                current_song, current_position, current_position, completion_pct
            )
        # Mark as played in queue for current event tracking
        self.queue_manager.mark_played(self.current_song_id)

        # Stop current playback (but do NOT mark as played)
        self.logger.debug(
            "[DEBUG] skip: before stop_playback, current=%s next=%s",
            self.current_song_id,
            next_song.id,
        )
        self.streaming_controller.stop_playback()
        self.logger.debug("[DEBUG] skip: after stop_playback")

        # Load and play the next song
        return self._play_song(next_song)

    def skip(self) -> bool:
        """
        Skip to next song.

        Navigation-based: moves to next song in queue without marking current as played.
        The current song remains in the queue and can be navigated back to.

        Returns:
            True if skipped, False if no next song available
        """
        with self.lock:
            return self._skip_internal()

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
        with self.lock:
            self.logger.info("Jumping to song ID %s", item_id)

            # Get the song from queue
            song = self.queue_manager.get_item(item_id)
            if not song:
                self.logger.warning("Song %s not found in queue", item_id)
                return False

            # Check if song is ready
            if song.download_status != QueueManager.STATUS_READY:
                self.logger.warning(
                    "Song %s is not ready (status: %s)", item_id, song.download_status
                )
                return False

            # Stop current playback if something is playing
            if self.current_song_id:
                self.streaming_controller.stop_playback()

            self.logger.info("Playing song %s at position %s", item_id, song.position)

            # Load and play the song (always from beginning)
            return self._play_song(song)

    def _switch_to_song(self, song: QueueItem) -> bool:
        """
        Stop current playback and switch to a different song.

        Helper method for navigation operations (previous, etc).
        Assumes lock is already held.

        Args:
            song: Queue item to play

        Returns:
            True if successful, False otherwise
        """
        # Stop current playback (but do NOT mark as played or record history)
        if self.current_song_id:
            self.streaming_controller.stop_playback()

        # Load and play the song (from beginning)
        return self._play_song(song)

    def previous(self) -> bool:
        """
        Go to previous song in the queue.

        Navigation-based: moves to previous song by position.

        Returns:
            True if moved to previous song, False if no previous song
        """
        with self.lock:
            self.logger.info("Going to previous song")

            if not self.current_song_id:
                self.logger.info("No current song, cannot go to previous")
                return False

            # Get previous song before current
            prev_song = self.queue_manager.get_ready_song_at_offset(self.current_song_id, -1)

            if not prev_song:
                self.logger.info("No previous song available")
                return False

            # Switch to the previous song
            return self._switch_to_song(prev_song)

    def move_down(self, item_id: int) -> Dict[str, Any]:
        """
        Move a song down one position in the queue.

        Does not affect the currently playing song.

        Args:
            item_id: ID of the queue item to move down

        Returns:
            Dictionary with status information:
            - status: 'moved_down' or 'already_at_end'
            - old_position: Original position
            - new_position: New position
        """
        with self.lock:
            self.logger.info("Moving down song %s", item_id)

            # Get current item
            item = self.queue_manager.get_item(item_id)
            if not item:
                self.logger.warning("Song %s not found", item_id)
                return {"status": "not_found"}

            current_position = item.position

            # Get max position
            queue = self.queue_manager.get_queue()
            max_position = max((q.position for q in queue), default=0)

            # Calculate new position (down 1, but not past the end)
            new_position = min(current_position + 1, max_position)

            if new_position == current_position:
                # Already at the end
                self.logger.info("Song %s already at end of queue", item_id)
                return {"status": "already_at_end", "position": current_position}

            # Reorder the song
            if not self.queue_manager.reorder_song(item_id, new_position):
                self.logger.error("Failed to reorder song %s", item_id)
                return {"status": "error"}

            return {
                "status": "moved_down",
                "old_position": current_position,
                "new_position": new_position,
            }

    def move_up(self, item_id: int) -> Dict[str, Any]:
        """
        Move a song up one position in the queue.

        Does not affect the currently playing song.

        Args:
            item_id: ID of the queue item to move up

        Returns:
            Dictionary with status information:
            - status: 'moved_up' or 'already_at_start'
            - old_position: Original position
            - new_position: New position
        """
        with self.lock:
            self.logger.info("Moving up song %s", item_id)

            # Get current item
            item = self.queue_manager.get_item(item_id)
            if not item:
                self.logger.warning("Song %s not found", item_id)
                return {"status": "not_found"}

            current_position = item.position

            # Get min position
            queue = self.queue_manager.get_queue()
            min_position = min((q.position for q in queue), default=1)

            # Calculate new position (up 1, but not past the start)
            new_position = max(current_position - 1, min_position)

            if new_position == current_position:
                # Already at the start
                self.logger.info("Song %s already at start of queue", item_id)
                return {"status": "already_at_start", "position": current_position}

            # Reorder the song
            if not self.queue_manager.reorder_song(item_id, new_position):
                self.logger.error("Failed to reorder song %s", item_id)
                return {"status": "error"}

            return {
                "status": "moved_up",
                "old_position": current_position,
                "new_position": new_position,
            }

    def move_to_next(self, item_id: int) -> bool:
        """
        Move a song to play next in the queue.

        If a song is currently playing, moves the song to position+1 after it.
        Otherwise, moves to position 1 (front of queue).

        If the currently playing song is moved, its position is updated.

        Args:
            item_id: ID of the queue item to move

        Returns:
            True if successful, False if item not found
        """
        with self.lock:
            self.logger.info("Moving song %s to play next", item_id)

            # Determine target position based on current playback
            if self.current_song_id:
                # Query fresh position from database
                current_song = self.queue_manager.get_item(self.current_song_id)
                if not current_song:
                    self.logger.error("Current song ID %s not found", self.current_song_id)
                    return False
                target_position = current_song.position + 1
            else:
                target_position = 1

            # Check if this is the currently playing song
            is_currently_playing = (
                self.current_song_id is not None and self.current_song_id == item_id
            )

            # Reorder the song
            if not self.queue_manager.reorder_song(item_id, target_position):
                self.logger.error("Failed to move song %s to next", item_id)
                return False

            # Note: No need to update current_song_id - the ID doesn't change,
            # only the position in the database changes
            if is_currently_playing:
                self.logger.info("Currently playing song moved to position %s", target_position)

            return True

    def move_to_end(self, item_id: int) -> bool:
        """
        Move a song to the end of the queue.

        If the song is currently playing, playback continues but the
        current_song position is updated to reflect its new location.

        Args:
            item_id: ID of the queue item to move

        Returns:
            True if successful, False if item not found
        """
        with self.lock:
            self.logger.info("Moving song %s to end of queue", item_id)

            # Get max position
            queue = self.queue_manager.get_queue()
            if not queue:
                self.logger.warning("Cannot move to end - queue is empty")
                return False

            max_position = max((item.position for item in queue), default=0)

            # Check if this is the currently playing song
            is_currently_playing = (
                self.current_song_id is not None and self.current_song_id == item_id
            )

            # Reorder the song
            if not self.queue_manager.reorder_song(item_id, max_position):
                self.logger.error("Failed to move song %s to end", item_id)
                return False

            # Note: No need to update current_song_id - the ID doesn't change,
            # only the position in the database changes
            if is_currently_playing:
                self.logger.info("Currently playing song moved to position %s", max_position)

            return True

    def _handle_error(self, item_id: int, error_message: str):
        """
        Handle playback error.

        Assumes lock is already held (called from _play_song which is called with lock held).

        Args:
            item_id: ID of the queue item that failed
            error_message: Error message
        """
        self.logger.error("Playback error for item %s: %s", item_id, error_message)

        # Stop playback so operator can investigate
        self._set_state(PlaybackState.STOPPED, f"error: {error_message}")
        self.current_song_id = None
        self.show_idle_screen()

    def on_song_end(self):
        """Called when current song ends (EOS)."""
        with self.lock:
            finished_song_id = None

            if self.current_song_id:
                finished_song_id = self.current_song_id

                # Get song data for history recording
                finished_song = self.queue_manager.get_item(finished_song_id)
                if finished_song:
                    self.logger.info("Song ended: %s", finished_song.metadata.title)

                    # Record history if threshold met
                    final_position = self.streaming_controller.get_position() or 0
                    if self.history_manager and self._should_record_history(
                        finished_song.metadata.duration_seconds, final_position
                    ):
                        completion_pct = self._calculate_completion_percentage(
                            final_position, finished_song.metadata.duration_seconds
                        )
                        self._record_performance_history(
                            finished_song, final_position, final_position, completion_pct
                        )

                    # Mark as played in queue for current event tracking
                    self.queue_manager.mark_played(finished_song_id)
                else:
                    self.logger.warning("Finished song ID %s not found", finished_song_id)
            else:
                self.logger.info("Song ended: unknown")

            # Reset pitch
            self.streaming_controller.set_pitch_shift(0)

            # Clear the singer/up next overlay
            self._set_base_overlay("")

            # Clear current song ID (natural end of song, transitioning to next or idle)
            # NOTE: This is one of only 3 places current_song_id is mutated:
            #   1. _play_song() - sets it when starting playback
            #   2. _stop_internal() - clears it when stopping playback
            #   3. on_song_end() - clears it when song finishes naturally
            self.current_song_id = None

            # Check for next song and show transition, using the ID of the song that just finished
            self._show_transition_or_end(finished_song_id=finished_song_id)

    def _show_transition_or_end(self, finished_song_id: Optional[int] = None):
        """
        Show transition screen for next song, or end-of-queue screen.

        Called after a song ends. Shows appropriate interstitial and schedules
        the next song to start after the transition duration.

        Args:
            finished_song_id: ID of the song that just finished (used to find next song by position)

        Note: Called with lock held.
        """
        # Get next ready song after the one that finished
        # If finished_song_id is None, we have no reference point, so show end screen
        if finished_song_id is None:
            self._set_state(PlaybackState.IDLE, "queue exhausted")
            self._show_end_of_queue_screen()
            return

        next_song = self.queue_manager.get_ready_song_at_offset(finished_song_id, +1)

        if not next_song:
            # No more songs - show end-of-queue screen
            self._set_state(PlaybackState.IDLE, "queue exhausted")
            self._show_end_of_queue_screen()
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
        self._show_transition_screen(
            singer_name=next_song.user_name, song_title=next_song.metadata.title
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
        with self.lock:
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

    def _get_interstitial_generator(self):
        """Get or create the interstitial generator (lazy initialization)."""
        if self._interstitial_generator is None:
            import os

            from .interstitials import InterstitialGenerator

            # Get cache directory from config or use default
            cache_dir = self.config_manager.get("cache_directory")
            if cache_dir:
                cache_dir = os.path.join(cache_dir, "interstitials")
            self._interstitial_generator = InterstitialGenerator(cache_dir=cache_dir)
        return self._interstitial_generator

    def _get_web_url(self) -> Optional[str]:
        """Get the web interface URL for QR codes."""
        # Access server through streaming controller
        server = getattr(self.streaming_controller, "server", None)
        if server:
            return getattr(server, "external_url", None)
        return None

    def show_idle_screen(self, message: str = "Add songs to get started!"):
        """
        Show the idle screen interstitial.

        Args:
            message: Message to display on the idle screen
        """
        self.logger.info("Showing idle screen: %s", message)

        generator = self._get_interstitial_generator()
        web_url = self._get_web_url()

        image_path = generator.generate_idle_screen(web_url=web_url, message=message)

        if image_path:
            self.streaming_controller.display_image(image_path)
        else:
            self.logger.warning("Could not generate idle screen")

    def _show_transition_screen(self, singer_name: str, song_title: Optional[str] = None):
        """
        Display the between-songs transition screen.

        Args:
            singer_name: Name of the next singer
            song_title: Optional song title (can be None for surprise)
        """
        self.logger.info("Showing transition screen for: %s", singer_name)

        generator = self._get_interstitial_generator()
        web_url = self._get_web_url()

        image_path = generator.generate_transition_screen(
            singer_name=singer_name, song_title=song_title, web_url=web_url
        )

        if image_path:
            self.streaming_controller.display_image(image_path)
        else:
            self.logger.warning("Could not generate transition screen")

    def _show_end_of_queue_screen(self, message: str = "That's all for now!"):
        """
        Display the end-of-queue interstitial screen.

        Args:
            message: Message to display
        """
        self.logger.info("Showing end-of-queue screen: %s", message)

        generator = self._get_interstitial_generator()
        web_url = self._get_web_url()

        image_path = generator.generate_end_of_queue_screen(web_url=web_url, message=message)

        if image_path:
            self.streaming_controller.display_image(image_path)
        else:
            self.logger.warning("Could not generate end-of-queue screen")

    def get_status(self) -> Dict[str, Any]:
        """
        Get current playback status.

        Returns:
            Dictionary with playback state and current song info
        """
        from dataclasses import asdict

        with self.lock:
            position = None
            if self.state in (PlaybackState.PLAYING, PlaybackState.PAUSED):
                position = self.streaming_controller.get_position()

            # Query current song data from database (always fresh)
            current_song = None
            if self.current_song_id:
                queue_item = self.queue_manager.get_item(self.current_song_id)
                if queue_item:
                    # Convert QueueItem to dict for JSON serialization
                    current_song = asdict(queue_item)
                    # Convert datetime objects to ISO format strings for JSON
                    if current_song.get("played_at"):
                        current_song["played_at"] = current_song["played_at"].isoformat()
                    if current_song.get("created_at"):
                        current_song["created_at"] = current_song["created_at"].isoformat()
                    # Flatten metadata and settings for easier frontend access
                    current_song["title"] = queue_item.metadata.title
                    current_song["duration_seconds"] = queue_item.metadata.duration_seconds
                    current_song["thumbnail_url"] = queue_item.metadata.thumbnail_url
                    current_song["channel"] = queue_item.metadata.channel
                    current_song["pitch_semitones"] = queue_item.settings.pitch_semitones

            status = {
                "state": self.state.value,
                "current_song": current_song,
                "position_seconds": position,
            }
            return status

    def set_pitch(self, semitones: int) -> bool:
        """
        Set pitch adjustment for current song.

        Args:
            semitones: Pitch adjustment in semitones

        Returns:
            True if set, False if no current song
        """
        with self.lock:
            if not self.current_song_id:
                self.logger.warning("No current song to adjust pitch")
                return False

            # Update in queue database
            self.queue_manager.update_pitch(self.current_song_id, semitones)

            # Apply to streaming controller
            self.streaming_controller.set_pitch_shift(semitones)

            self.logger.info("Set pitch to %s semitones for current song", semitones)
            return True

    def restart(self) -> bool:
        """
        Restart the current song from the beginning.

        Returns:
            True if successful, False if no current song or seek failed
        """
        with self.lock:
            if not self.current_song_id:
                self.logger.warning("No current song to restart")
                return False

            if self.state not in (PlaybackState.PLAYING, PlaybackState.PAUSED):
                self.logger.warning("Cannot restart: not playing or paused")
                return False

            self.logger.info("Restarting song from beginning")
            return self.streaming_controller.seek(0)

    def seek_relative(self, delta_seconds: int) -> bool:
        """
        Seek forward or backward by a relative amount.

        Args:
            delta_seconds: Seconds to seek (positive = forward, negative = backward)

        Returns:
            True if successful, False otherwise
        """
        with self.lock:
            if not self.current_song_id:
                self.logger.warning("No current song to seek")
                return False

            if self.state not in (PlaybackState.PLAYING, PlaybackState.PAUSED):
                self.logger.warning("Cannot seek: not playing or paused")
                return False

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
            return self.streaming_controller.seek(new_position)

    def _should_record_history(self, duration_seconds: Optional[int], played_seconds: int) -> bool:
        """
        Check if a performance should be recorded in history.

        Records if played duration meets threshold:
        - 70% of total duration (default), OR
        - 90+ seconds (default)

        Args:
            duration_seconds: Total song duration (None if unknown)
            played_seconds: How long the song was played

        Returns:
            True if performance should be recorded
        """
        # Get thresholds from config
        threshold_pct = self.config_manager.get("history_threshold_percentage")
        threshold_seconds = self.config_manager.get("history_threshold_seconds")

        # Use defaults if not configured
        if threshold_pct is None:
            threshold_pct = 70
        else:
            try:
                threshold_pct = int(threshold_pct)
            except (ValueError, TypeError):
                threshold_pct = 70

        if threshold_seconds is None:
            threshold_seconds = 90
        else:
            try:
                threshold_seconds = int(threshold_seconds)
            except (ValueError, TypeError):
                threshold_seconds = 90

        # Check time threshold
        if played_seconds >= threshold_seconds:
            self.logger.debug(
                "History threshold met: %s seconds >= %s seconds", played_seconds, threshold_seconds
            )
            return True

        # Check percentage threshold if duration known
        if duration_seconds and duration_seconds > 0:
            percentage = (played_seconds / duration_seconds) * 100
            if percentage >= threshold_pct:
                self.logger.debug(
                    "History threshold met: %.1f%% >= %s%%", percentage, threshold_pct
                )
                return True

        self.logger.debug(
            "History threshold NOT met: %s seconds (threshold: %s%% or %s seconds)",
            played_seconds,
            threshold_pct,
            threshold_seconds,
        )
        return False

    def _calculate_completion_percentage(
        self, played_seconds: int, duration_seconds: Optional[int]
    ) -> float:
        """
        Calculate completion percentage.

        Args:
            played_seconds: How long the song was played
            duration_seconds: Total song duration

        Returns:
            Completion percentage (0.0-100.0)
        """
        if not duration_seconds or duration_seconds <= 0:
            return 0.0

        return min(100.0, (played_seconds / duration_seconds) * 100.0)

    def _record_performance_history(
        self,
        queue_item: QueueItem,
        played_duration_seconds: int,
        playback_end_position_seconds: int,
        completion_percentage: float,
    ):
        """
        Record a performance in history.

        Args:
            queue_item: Queue item with song details
            played_duration_seconds: How long the song was played
            playback_end_position_seconds: Where playback ended
            completion_percentage: Percentage of song completed
        """
        self.history_manager.record_performance(
            user_id=queue_item.user_id,
            user_name=queue_item.user_name,
            video_id=queue_item.video_id,
            metadata=queue_item.metadata,
            settings=queue_item.settings,
            played_duration_seconds=played_duration_seconds,
            playback_end_position_seconds=playback_end_position_seconds,
            completion_percentage=completion_percentage,
        )

    def shutdown(self):
        """Shutdown the playback controller and cleanup all resources."""
        self.logger.info("Shutting down playback controller")
        self._monitoring = False

        # Cancel transition timer
        if self._transition_timer:
            self._transition_timer.cancel()
            self._transition_timer = None

        # Stop monitor thread
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)
            if self._monitor_thread.is_alive():
                self.logger.warning("Monitor thread did not stop within timeout")

        # Stop streaming
        if self.streaming_controller:
            self.streaming_controller.stop()

        self.logger.info("Playback controller shut down")
