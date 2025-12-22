"""
Playback controller for kbox.

Orchestrates playback, manages state transitions, and handles error recovery.
"""

import logging
import threading
from enum import Enum
from typing import Optional, Dict, Any
from .queue import QueueManager

class PlaybackState(Enum):
    """Playback state enumeration."""
    IDLE = 'idle'
    PLAYING = 'playing'
    PAUSED = 'paused'
    TRANSITION = 'transition'  # Between songs, showing interstitial
    ERROR = 'error'

class PlaybackController:
    """Orchestrates playback and manages state."""
    
    def __init__(
        self,
        queue_manager: QueueManager,
        streaming_controller,  # StreamingController - avoid circular import
        config_manager
    ):
        """
        Initialize PlaybackController.
        
        Args:
            queue_manager: QueueManager instance
            streaming_controller: StreamingController instance
            config_manager: ConfigManager instance
        """
        self.queue_manager = queue_manager
        self.streaming_controller = streaming_controller
        self.config_manager = config_manager
        
        self.logger = logging.getLogger(__name__)
        self.state = PlaybackState.IDLE
        self.current_song_id: Optional[int] = None
        self.lock = threading.Lock()
        
        # Transition/interstitial state
        self._transition_timer = None
        self._next_song_pending = None  # Song to play after transition
        
        # Interstitial generator (lazy-initialized)
        self._interstitial_generator = None
        
        # Start "up next" notification monitoring thread
        self._notification_thread = None
        self._monitoring_notifications = False
        self._up_next_shown = False  # Track if "up next" notification was shown for current song
        self._start_notification_monitor()
        
        # Set EOS callback
        self.streaming_controller.set_eos_callback(self.on_song_end)
    
    def _start_notification_monitor(self):
        """Start background thread to monitor playback and show 'up next' notifications."""
        if self._notification_thread and self._notification_thread.is_alive():
            return
        
        def monitor_notifications():
            """Periodically check if we should show 'up next' notification."""
            import time
            while self._monitoring_notifications:
                try:
                    if self.state == PlaybackState.PLAYING and self.current_song_id:
                        position = self.streaming_controller.get_position()
                        if position is not None:
                            # Check if we should show "up next" notification
                            self._check_up_next_notification(position)
                    time.sleep(2)  # Check every 2 seconds
                except Exception as e:
                    self.logger.error('Error in notification monitor: %s', e, exc_info=True)
                    time.sleep(5)  # Wait longer on error
        
        self._monitoring_notifications = True
        self._notification_thread = threading.Thread(target=monitor_notifications, daemon=True, name='NotificationMonitor')
        self._notification_thread.start()
        self.logger.info('Notification monitor started')
    
    def _check_up_next_notification(self, current_position: int):
        """
        Check if song is ending and show "up next" notification.
        
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
        
        duration = current_song.get('duration_seconds')
        if not duration or duration <= 0:
            return
        
        # Show notification when 15 seconds or less remain
        time_remaining = duration - current_position
        if time_remaining <= 15:
            # Get next song
            next_song = self.queue_manager.get_next_song_after(self.current_song_id)
            if next_song:
                notification_text = f"Up next: {next_song['user_name']}"
                self.streaming_controller.show_notification(notification_text, duration_seconds=10.0)
                self._up_next_shown = True
                self.logger.debug('Showed up next notification for: %s', next_song['user_name'])
    
    def play(self) -> bool:
        """
        Start or resume playback.
        
        Returns:
            True if playback started, False otherwise
        """
        with self.lock:
            if self.state == PlaybackState.PLAYING:
                self.logger.debug('Already playing')
                return True
            
            if self.state == PlaybackState.PAUSED:
                # Resume current song
                self.logger.info('Resuming playback')
                try:
                    self.streaming_controller.resume()
                    self.state = PlaybackState.PLAYING
                    return True
                except Exception as e:
                    self.logger.error('Error resuming playback: %s', e, exc_info=True)
                    return False
            
            if self.state == PlaybackState.TRANSITION:
                # Already transitioning to next song, skip the wait
                self.logger.info('Skipping transition, starting song immediately')
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
        """Load next ready song that hasn't been played yet and start playback."""
        # Get unplayed songs only
        queue = self.queue_manager.get_queue(include_played=False)
        ready_songs = [item for item in queue 
                      if item['download_status'] == QueueManager.STATUS_READY]
        
        if not ready_songs:
            self.logger.info('No ready songs in queue')
            self.state = PlaybackState.IDLE
            return False
        
        # Get the first ready song
        next_song = ready_songs[0]
        return self._play_song(next_song)
    
    def _play_song(self, song: Dict[str, Any]) -> bool:
        """
        Load and play a specific song.
        
        Args:
            song: Queue item dictionary to play
        
        Returns:
            True if playback started, False on error
        """
        # Check if file exists
        download_path = song.get('download_path')
        if not download_path:
            self.logger.warning('No download path for song %s', song['id'])
            self.state = PlaybackState.IDLE
            return False
        
        # Reset "up next" notification flag for new song
        self._up_next_shown = False
        
        try:
            self.logger.info('Loading song: %s by %s', song['title'], song['user_name'])
            
            # Set pitch for this song
            pitch = song.get('pitch_semitones', 0)
            self.logger.debug('[DEBUG] _play_song: before set_pitch_shift, song=%s pitch=%s', song['id'], pitch)
            try:
                self.streaming_controller.set_pitch_shift(pitch)
            except Exception as e:
                self.logger.warning('Could not set pitch shift: %s', e)
            
            # Load file into streaming controller (always start from beginning)
            self.logger.debug('[DEBUG] _play_song: before load_file')
            try:
                self.streaming_controller.load_file(download_path)
            except Exception as e:
                self.logger.error('Failed to load file into streaming controller: %s', e)
                self.state = PlaybackState.ERROR
                self._handle_error(song['id'], f'Playback failed: {str(e)}')
                return False
            
            # Mark song ID as currently playing
            # NOTE: This is one of only 3 places current_song_id is mutated:
            #   1. _play_song() - sets it when starting playback
            #   2. _stop_internal() - clears it when stopping playback
            #   3. on_song_end() - clears it when song finishes naturally
            self.current_song_id = song['id']
            self.state = PlaybackState.PLAYING
            
            self.logger.info('Playback started: %s', song['title'])
            return True
            
        except Exception as e:
            self.logger.error('Error loading song: %s', e, exc_info=True)
            self.state = PlaybackState.ERROR
            self._handle_error(song['id'], str(e))
            return False
    
    def pause(self) -> bool:
        """
        Pause playback.
        
        Returns:
            True if paused, False otherwise
        """
        with self.lock:
            if self.state != PlaybackState.PLAYING:
                self.logger.debug('Not playing, cannot pause')
                return False
            
            self.logger.info('Pausing playback')
            try:
                self.streaming_controller.pause()
                self.state = PlaybackState.PAUSED
                return True
            except Exception as e:
                self.logger.error('Error pausing playback: %s', e, exc_info=True)
                return False
    
    def _stop_internal(self):
        """
        Stop playback and clear current song.
        
        Helper method for stopping playback. Assumes lock is already held.
        This is the ONLY place (besides _play_song) where current_song_id should be mutated.
        """
        self.streaming_controller.stop_playback()
        self.current_song_id = None
        self.state = PlaybackState.IDLE
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
            
            if not self.current_song_id and self.state == PlaybackState.IDLE:
                self.logger.debug('Already idle, nothing to stop')
                return False
            
            self.logger.info('Stopping playback')
            try:
                self._stop_internal()
                return True
            except Exception as e:
                self.logger.error('Error stopping playback: %s', e, exc_info=True)
                return False
    
    def skip(self) -> bool:
        """
        Skip to next song.
        
        Navigation-based: moves to next song in queue without marking current as played.
        The current song remains in the queue and can be navigated back to.
        
        Returns:
            True if skipped, False if no next song available
        """
        with self.lock:
            self.logger.info('Skipping current song')
            
            if not self.current_song_id:
                self.logger.info('No current song, trying to start playback')
                return self._load_and_play_next()
            
            # Get current song data for history
            current_song = self.queue_manager.get_item(self.current_song_id)
            if not current_song:
                self.logger.error('Current song ID %s not found', self.current_song_id)
                return False
            
            # Get next song after current
            next_song = self.queue_manager.get_next_song_after(self.current_song_id)
            
            if not next_song:
                self.logger.info('No next song available to skip to')
                return False
            
            # Phase 2: Playback history recording disabled for now
            # current_position = self.streaming_controller.get_position() or 0
            # self.queue_manager.record_playback_history(...)
            pass
            
            # Stop current playback (but do NOT mark as played)
            self.logger.debug('[DEBUG] skip: before stop_playback, current=%s next=%s', self.current_song_id, next_song['id'])
            self.streaming_controller.stop_playback()
            self.logger.debug('[DEBUG] skip: after stop_playback')
            
            # Load and play the next song
            return self._play_song(next_song)
    
    def jump_to_song(self, item_id: int) -> bool:
        """
        Jump to a specific song in the queue.
        
        Args:
            item_id: ID of the queue item to jump to
        
        Returns:
            True if successful, False otherwise
        """
        with self.lock:
            self.logger.info('Jumping to song ID: %s', item_id)
            
            # Get the song from queue
            song = self.queue_manager.get_item(item_id)
            if not song:
                self.logger.warning('Song %s not found in queue', item_id)
                return False
            
            # Check if song is ready
            if song['download_status'] != QueueManager.STATUS_READY:
                self.logger.warning('Song %s is not ready (status: %s)', item_id, song['download_status'])
                return False
            
            # Stop current playback
            if self.current_song_id:
                self.streaming_controller.stop_playback()
            
            # Load and play the target song (always from beginning)
            return self._play_song(song)
    
    def play_now(self, item_id: int) -> bool:
        """
        Play a song immediately.
        
        If a song is currently playing, moves this song ahead of it and plays immediately.
        If playback is stopped, just plays the song at its current position.
        
        Args:
            item_id: ID of the queue item to play now
        
        Returns:
            True if successful, False otherwise
        """
        with self.lock:
            self.logger.info('Playing song ID %s now', item_id)
            
            # Get the song from queue
            song = self.queue_manager.get_item(item_id)
            if not song:
                self.logger.warning('Song %s not found in queue', item_id)
                return False
            
            # Check if song is ready
            if song['download_status'] != QueueManager.STATUS_READY:
                self.logger.warning('Song %s is not ready (status: %s)', item_id, song['download_status'])
                return False
            
            # If something is currently playing, move this song ahead of it
            if self.current_song_id:
                # Query fresh position from database
                current_song = self.queue_manager.get_item(self.current_song_id)
                if not current_song:
                    self.logger.error('Current song ID %s not found', self.current_song_id)
                    return False
                
                target_position = current_song.get('position', 1)
                
                self.logger.info('Moving song %s to position %s (ahead of current)', item_id, target_position)
                
                # Move the song to the current position
                if not self.queue_manager.reorder_song(item_id, target_position):
                    self.logger.error('Failed to reorder song %s to position %s', item_id, target_position)
                    return False
                
                # Stop current playback
                self.streaming_controller.stop_playback()
                
                # Refresh song data after reordering (position has changed)
                song = self.queue_manager.get_item(item_id)
                if not song:
                    self.logger.error('Song %s not found after reordering', item_id)
                    return False
            else:
                # Nothing playing - just play the song at its current position
                self.logger.info('No song playing, playing song %s at position %s', item_id, song.get('position'))
            
            # Load and play the song (always from beginning)
            return self._play_song(song)
    
    def _switch_to_song(self, song: Dict[str, Any]) -> bool:
        """
        Stop current playback and switch to a different song.
        
        Helper method for navigation operations (previous, bump_down, etc).
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
            self.logger.info('Going to previous song')
            
            if not self.current_song_id:
                self.logger.info('No current song, cannot go to previous')
                return False
            
            # Get previous song before current
            prev_song = self.queue_manager.get_previous_song_before(self.current_song_id)
            
            if not prev_song:
                self.logger.info('No previous song available')
                return False
            
            # Switch to the previous song
            return self._switch_to_song(prev_song)
    
    def bump_down(self, item_id: int) -> Dict[str, Any]:
        """
        Bump a song down one position in the queue.
        
        If the song is currently playing, skip to the next song.
        When that song completes, the bumped song will play.
        
        Args:
            item_id: ID of the queue item to bump down
        
        Returns:
            Dictionary with status information:
            - status: 'bumped_down', 'bumped_down_and_skipped', or 'already_at_end'
            - old_position: Original position
            - new_position: New position
        """
        with self.lock:
            self.logger.info('Bumping down song %s', item_id)
            
            # Get current item
            item = self.queue_manager.get_item(item_id)
            if not item:
                self.logger.warning('Song %s not found', item_id)
                return {'status': 'not_found'}
            
            current_position = item.get('position', 0)
            
            # Get max position
            queue = self.queue_manager.get_queue()
            max_position = max((q.get('position', 0) for q in queue), default=0)
            
            # Calculate new position (down 1, but not past the end)
            new_position = min(current_position + 1, max_position)
            
            if new_position == current_position:
                # Already at the end
                self.logger.info('Song %s already at end of queue', item_id)
                return {
                    'status': 'already_at_end',
                    'position': current_position
                }
            
            # Check if this is the currently playing song
            is_currently_playing = (
                self.current_song_id is not None and 
                self.current_song_id == item_id
            )
            
            # Reorder the song
            if not self.queue_manager.reorder_song(item_id, new_position):
                self.logger.error('Failed to reorder song %s', item_id)
                return {'status': 'error'}
            
            # If currently playing, play the song that moved up to take its place
            if is_currently_playing:
                self.logger.info('Bumped song was playing, playing song that moved up')
                
                # Find the song now at the old position (the one that moved up)
                # After reordering, the song that was at position+1 is now at position
                song_at_old_position = None
                updated_queue = self.queue_manager.get_queue()
                for song in updated_queue:
                    if song.get('position') == current_position:
                        song_at_old_position = song
                        break
                
                if song_at_old_position and song_at_old_position.get('download_status') == QueueManager.STATUS_READY:
                    # Play the song that moved up
                    self._switch_to_song(song_at_old_position)
                    return {
                        'status': 'bumped_down_and_skipped',
                        'old_position': current_position,
                        'new_position': new_position
                    }
                else:
                    # No ready song at that position, stop playback
                    self.logger.info('No ready song to play after bump down, going idle')
                    self._stop_internal()
                    return {
                        'status': 'bumped_down_and_stopped',
                        'old_position': current_position,
                        'new_position': new_position
                    }
            
            return {
                'status': 'bumped_down',
                'old_position': current_position,
                'new_position': new_position
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
            self.logger.info('Moving song %s to play next', item_id)
            
            # Determine target position based on current playback
            if self.current_song_id:
                # Query fresh position from database
                current_song = self.queue_manager.get_item(self.current_song_id)
                if not current_song:
                    self.logger.error('Current song ID %s not found', self.current_song_id)
                    return False
                target_position = current_song.get('position', 1) + 1
            else:
                target_position = 1
            
            # Check if this is the currently playing song
            is_currently_playing = (
                self.current_song_id is not None and 
                self.current_song_id == item_id
            )
            
            # Reorder the song
            if not self.queue_manager.reorder_song(item_id, target_position):
                self.logger.error('Failed to move song %s to next', item_id)
                return False
            
            # Note: No need to update current_song_id - the ID doesn't change,
            # only the position in the database changes
            if is_currently_playing:
                self.logger.info('Currently playing song moved to position %s', target_position)
            
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
            self.logger.info('Moving song %s to end of queue', item_id)
            
            # Get max position
            queue = self.queue_manager.get_queue()
            if not queue:
                self.logger.warning('Cannot move to end - queue is empty')
                return False
            
            max_position = max((item.get('position', 0) for item in queue), default=0)
            
            # Check if this is the currently playing song
            is_currently_playing = (
                self.current_song_id is not None and 
                self.current_song_id == item_id
            )
            
            # Reorder the song
            if not self.queue_manager.reorder_song(item_id, max_position):
                self.logger.error('Failed to move song %s to end', item_id)
                return False
            
            # Note: No need to update current_song_id - the ID doesn't change,
            # only the position in the database changes
            if is_currently_playing:
                self.logger.info('Currently playing song moved to position %s', max_position)
            
            return True
    
    def _handle_error(self, item_id: int, error_message: str):
        """
        Handle playback error.
        
        Args:
            item_id: ID of the queue item that failed
            error_message: Error message
        """
        self.logger.error('Playback error for item %s: %s', item_id, error_message)
        
        # Try to skip to next song
        if self.skip():
            self.logger.info('Skipped to next song after error')
        else:
            self.logger.warning('No next song available after error')
            self.state = PlaybackState.IDLE
    
    def on_song_end(self):
        """Called when current song ends (EOS)."""
        with self.lock:
            finished_song_id = None
            
            if self.current_song_id:
                finished_song_id = self.current_song_id
                
                # Get song data for history recording
                finished_song = self.queue_manager.get_item(finished_song_id)
                if finished_song:
                    self.logger.info('Song ended: %s', finished_song['title'])
                    
                    # Phase 2: Playback history recording disabled for now
                    # final_position = self.streaming_controller.get_position() or 0
                    # self.queue_manager.record_playback_history(...)
                    pass
                    
                    # Mark as played
                    self.queue_manager.mark_played(finished_song_id)
                else:
                    self.logger.warning('Finished song ID %s not found', finished_song_id)
            else:
                self.logger.info('Song ended: unknown')
            
            # Reset pitch
            self.streaming_controller.set_pitch_shift(0)
            
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
        if finished_song_id:
            # Find the next song after the one that finished, by queue position
            next_song = self.queue_manager.get_next_song_after(finished_song_id)
        else:
            # No finished song (e.g., starting fresh), get first ready song
            next_song = self.queue_manager.get_next_song()
        
        if not next_song:
            # No more songs - show end-of-queue screen
            self.logger.info('No more songs, showing end-of-queue screen')
            self.state = PlaybackState.IDLE
            self._show_end_of_queue_screen()
            return
        
        # Store next song for transition
        self._next_song_pending = next_song
        
        # Get transition duration from config (default 5 seconds)
        transition_duration = self.config_manager.get('transition_duration_seconds')
        if transition_duration is None:
            transition_duration = 5
        else:
            try:
                transition_duration = int(transition_duration)
            except (ValueError, TypeError):
                transition_duration = 5
        
        # Show transition screen
        self.logger.info('Showing transition for: %s (duration: %ss)', 
                        next_song['user_name'], transition_duration)
        self.state = PlaybackState.TRANSITION
        self._show_transition_screen(
            singer_name=next_song['user_name'],
            song_title=next_song.get('title')
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
                self.logger.debug('Transition complete but state changed, ignoring')
                return
            
            if not self._next_song_pending:
                self.logger.warning('Transition complete but no pending song')
                self.state = PlaybackState.IDLE
                return
            
            next_song = self._next_song_pending
            self._next_song_pending = None
            
            self.logger.info('Transition complete, starting: %s', next_song['title'])
            self._play_song(next_song)
    
    # =========================================================================
    # Interstitial Display
    # =========================================================================
    
    def _get_interstitial_generator(self):
        """Get or create the interstitial generator (lazy initialization)."""
        if self._interstitial_generator is None:
            from .interstitials import InterstitialGenerator
            import os
            # Get cache directory from config or use default
            cache_dir = self.config_manager.get('cache_directory')
            if cache_dir:
                cache_dir = os.path.join(cache_dir, 'interstitials')
            self._interstitial_generator = InterstitialGenerator(cache_dir=cache_dir)
        return self._interstitial_generator
    
    def _get_web_url(self) -> Optional[str]:
        """Get the web interface URL for QR codes."""
        # Access server through streaming controller
        server = getattr(self.streaming_controller, 'server', None)
        if server:
            return getattr(server, 'external_url', None)
        return None
    
    def show_idle_screen(self, message: str = "Add songs to get started!"):
        """
        Show the idle screen interstitial.
        
        Args:
            message: Message to display on the idle screen
        """
        self.logger.info('Showing idle screen: %s', message)
        
        generator = self._get_interstitial_generator()
        web_url = self._get_web_url()
        
        image_path = generator.generate_idle_screen(
            web_url=web_url,
            message=message
        )
        
        if image_path:
            self.streaming_controller.display_image(image_path)
        else:
            self.logger.warning('Could not generate idle screen')
    
    def _show_transition_screen(self, singer_name: str, song_title: Optional[str] = None):
        """
        Display the between-songs transition screen.
        
        Args:
            singer_name: Name of the next singer
            song_title: Optional song title (can be None for surprise)
        """
        self.logger.info('Showing transition screen for: %s', singer_name)
        
        generator = self._get_interstitial_generator()
        web_url = self._get_web_url()
        
        image_path = generator.generate_transition_screen(
            singer_name=singer_name,
            song_title=song_title,
            web_url=web_url
        )
        
        if image_path:
            self.streaming_controller.display_image(image_path)
        else:
            self.logger.warning('Could not generate transition screen')
    
    def _show_end_of_queue_screen(self, message: str = "That's all for now!"):
        """
        Display the end-of-queue interstitial screen.
        
        Args:
            message: Message to display
        """
        self.logger.info('Showing end-of-queue screen: %s', message)
        
        generator = self._get_interstitial_generator()
        web_url = self._get_web_url()
        
        image_path = generator.generate_end_of_queue_screen(
            web_url=web_url,
            message=message
        )
        
        if image_path:
            self.streaming_controller.display_image(image_path)
        else:
            self.logger.warning('Could not generate end-of-queue screen')
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current playback status.
        
        Returns:
            Dictionary with playback state and current song info
        """
        with self.lock:
            position = None
            if self.state in (PlaybackState.PLAYING, PlaybackState.PAUSED):
                position = self.streaming_controller.get_position()
            
            # Query current song data from database (always fresh)
            current_song = None
            if self.current_song_id:
                current_song = self.queue_manager.get_item(self.current_song_id)
            
            status = {
                'state': self.state.value,
                'current_song': current_song,
                'position_seconds': position,
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
                self.logger.warning('No current song to adjust pitch')
                return False
            
            # Update in queue database
            self.queue_manager.update_pitch(self.current_song_id, semitones)
            
            # Apply to streaming controller
            self.streaming_controller.set_pitch_shift(semitones)
            
            self.logger.info('Set pitch to %s semitones for current song', semitones)
            return True
    
    def restart(self) -> bool:
        """
        Restart the current song from the beginning.
        
        Returns:
            True if successful, False if no current song or seek failed
        """
        with self.lock:
            if not self.current_song_id:
                self.logger.warning('No current song to restart')
                return False
            
            if self.state not in (PlaybackState.PLAYING, PlaybackState.PAUSED):
                self.logger.warning('Cannot restart: not playing or paused')
                return False
            
            self.logger.info('Restarting song from beginning')
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
                self.logger.warning('No current song to seek')
                return False
            
            if self.state not in (PlaybackState.PLAYING, PlaybackState.PAUSED):
                self.logger.warning('Cannot seek: not playing or paused')
                return False
            
            current_position = self.streaming_controller.get_position()
            if current_position is None:
                current_position = 0
            
            new_position = max(0, current_position + delta_seconds)
            
            # Clamp to song duration if available
            current_song = self.queue_manager.get_item(self.current_song_id)
            if current_song:
                duration = current_song.get('duration_seconds')
                if duration and new_position > duration:
                    new_position = max(0, duration - 1)  # Seek to near the end
            
            self.logger.info('Seeking from %ss to %ss (delta: %+ds)', 
                           current_position, new_position, delta_seconds)
            return self.streaming_controller.seek(new_position)
    
    def shutdown(self):
        """Shutdown the playback controller and cleanup all resources."""
        self.logger.info('Shutting down playback controller')
        self._monitoring_notifications = False
        
        # Cancel transition timer
        if self._transition_timer:
            self._transition_timer.cancel()
            self._transition_timer = None
        
        # Stop notification monitor thread
        if self._notification_thread and self._notification_thread.is_alive():
            self._notification_thread.join(timeout=2.0)
            if self._notification_thread.is_alive():
                self.logger.warning('Notification monitor thread did not stop within timeout')
        
        # Stop streaming
        if self.streaming_controller:
            self.streaming_controller.stop()
        
        self.logger.info('Playback controller shut down')

