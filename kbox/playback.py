"""
Playback controller for kbox.

Orchestrates playback, manages state transitions, and handles error recovery.
"""

import logging
import threading
from enum import Enum
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from .queue import QueueManager
from .youtube import YouTubeClient

class PlaybackState(Enum):
    """Playback state enumeration."""
    IDLE = 'idle'
    PLAYING = 'playing'
    PAUSED = 'paused'
    ERROR = 'error'

class PlaybackController:
    """Orchestrates playback and manages state."""
    
    def __init__(
        self,
        queue_manager: QueueManager,
        youtube_client: YouTubeClient,
        streaming_controller,  # StreamingController - avoid circular import
        config_manager
    ):
        """
        Initialize PlaybackController.
        
        Args:
            queue_manager: QueueManager instance
            youtube_client: YouTubeClient instance
            streaming_controller: StreamingController instance
            config_manager: ConfigManager instance
        """
        self.queue_manager = queue_manager
        self.youtube_client = youtube_client
        self.streaming_controller = streaming_controller
        self.config_manager = config_manager
        
        self.logger = logging.getLogger(__name__)
        self.state = PlaybackState.IDLE
        self.current_song: Optional[Dict[str, Any]] = None
        self.lock = threading.Lock()
        
        # Download timeout (10 minutes) - reset stuck downloads after this time
        self._download_timeout = timedelta(minutes=10)
        
        # Start download monitor thread
        self._download_monitor_thread = None
        self._monitoring = True
        self._start_download_monitor()
        
        # Start position tracking thread
        self._position_tracking_thread = None
        self._tracking_position = False
        self._start_position_tracking()
        
        # Check for songs to resume on startup
        self._resume_interrupted_playback()
    
    def _start_download_monitor(self):
        """Start background thread to monitor queue and trigger downloads."""
        def monitor():
            while self._monitoring:
                try:
                    # Check for pending downloads
                    queue = self.queue_manager.get_queue()
                    for item in queue:
                        if item['download_status'] == QueueManager.STATUS_PENDING:
                            # Start download
                            self.logger.info('Starting download for %s (ID: %s)', 
                                           item['title'], item['id'])
                            self.youtube_client.download_video(
                                item['youtube_video_id'],
                                item['id'],
                                status_callback=lambda status, path, error: self._on_download_status(
                                    item['id'], status, path, error
                                )
                            )
                            # Update status to downloading
                            self.queue_manager.update_download_status(
                                item['id'],
                                QueueManager.STATUS_DOWNLOADING
                            )
                        elif item['download_status'] == QueueManager.STATUS_DOWNLOADING:
                            # Check if download is stuck
                            # First, check if file exists (download completed but callback failed)
                            download_path = self.youtube_client.get_download_path(item['youtube_video_id'])
                            if download_path and download_path.exists():
                                self.logger.info('Found completed download for %s (ID: %s), updating status', 
                                               item['title'], item['id'])
                                self.queue_manager.update_download_status(
                                    item['id'],
                                    QueueManager.STATUS_READY,
                                    download_path=str(download_path)
                                )
                                # Note: Playback will start when operator presses play button
                            else:
                                # Check if download has been stuck for too long
                                # Parse created_at timestamp
                                try:
                                    created_at_str = item.get('created_at')
                                    if created_at_str:
                                        if isinstance(created_at_str, str):
                                            created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                                        else:
                                            created_at = created_at_str
                                        
                                        # Check if it's been more than timeout since creation
                                        # (assuming download started shortly after creation)
                                        if datetime.now(created_at.tzinfo) - created_at > self._download_timeout:
                                            self.logger.warning('Download stuck for %s (ID: %s) for more than %s, resetting to pending', 
                                                              item['title'], item['id'], self._download_timeout)
                                            self.queue_manager.update_download_status(
                                                item['id'],
                                                QueueManager.STATUS_PENDING
                                            )
                                except (ValueError, TypeError) as e:
                                    # If we can't parse the timestamp, just log and continue
                                    self.logger.debug('Could not parse created_at for item %s: %s', item['id'], e)
                    
                    # Sleep before next check
                    threading.Event().wait(2.0)  # Check every 2 seconds
                except Exception as e:
                    self.logger.error('Error in download monitor: %s', e, exc_info=True)
                    threading.Event().wait(5.0)  # Wait longer on error
        
        self._download_monitor_thread = threading.Thread(target=monitor, daemon=True)
        self._download_monitor_thread.start()
        self.logger.debug('Download monitor started')
        
        # Set EOS callback
        self.streaming_controller.set_eos_callback(self.on_song_end)
    
    def _start_position_tracking(self):
        """Start background thread to track playback position."""
        if self._position_tracking_thread and self._position_tracking_thread.is_alive():
            return
        
        def track_position():
            """Periodically update playback position in database."""
            import time
            while self._tracking_position:
                try:
                    if self.state == PlaybackState.PLAYING and self.current_song:
                        position = self.streaming_controller.get_position()
                        if position is not None:
                            self.queue_manager.update_playback_position(
                                self.current_song['id'],
                                position
                            )
                    time.sleep(2)  # Update every 2 seconds
                except Exception as e:
                    self.logger.error('Error tracking position: %s', e, exc_info=True)
                    time.sleep(5)  # Wait longer on error
        
        self._tracking_position = True
        self._position_tracking_thread = threading.Thread(target=track_position, daemon=True, name='PositionTracker')
        self._position_tracking_thread.start()
        self.logger.info('Position tracking started')
    
    def _resume_interrupted_playback(self):
        """Check for songs with playback position and resume if needed."""
        try:
            conn = self.queue_manager.database.get_connection()
            cursor = conn.cursor()
            
            # Find songs with playback position but not marked as played
            cursor.execute('''
                SELECT id, position, user_name, youtube_video_id, title,
                       duration_seconds, thumbnail_url, pitch_semitones,
                       download_status, download_path, created_at, played_at,
                       playback_position_seconds, error_message
                FROM queue_items
                WHERE playback_position_seconds > 0 
                  AND played_at IS NULL
                  AND download_status = 'ready'
                ORDER BY position
                LIMIT 1
            ''')
            
            result = cursor.fetchone()
            conn.close()
            
            if result:
                song = dict(result)
                self.logger.info('Found interrupted playback: %s at position %s seconds', 
                               song['title'], song['playback_position_seconds'])
                # Resume playback
                self.jump_to_song(song['id'], resume_position=song['playback_position_seconds'])
        except Exception as e:
            self.logger.error('Error resuming interrupted playback: %s', e, exc_info=True)
    
    def _on_download_status(self, item_id: int, status: str, path: Optional[str], error: Optional[str]):
        """Callback for download status updates."""
        if status == 'ready' and path:
            self.queue_manager.update_download_status(
                item_id,
                QueueManager.STATUS_READY,
                download_path=path
            )
            self.logger.info('Download complete for queue item %s: %s', item_id, path)
            # Note: Playback will start when operator presses play button
        elif status == 'error' and error:
            self.queue_manager.update_download_status(
                item_id,
                QueueManager.STATUS_ERROR,
                error_message=error
            )
            self.logger.error('Download failed for queue item %s: %s', item_id, error)
    
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
        
        try:
            self.logger.info('Loading song: %s by %s', song['title'], song['user_name'])
            
            # Set pitch for this song
            pitch = song.get('pitch_semitones', 0)
            self.logger.debug('[DEBUG] _play_song: before set_pitch_shift, song=%s pitch=%s', song['id'], pitch)
            try:
                self.streaming_controller.set_pitch_shift(pitch)
            except Exception as e:
                self.logger.warning('Could not set pitch shift: %s', e)
            
            # Load file into streaming controller
            # Pass start position so seek happens before audio plays
            saved_position = song.get('playback_position_seconds', 0) or 0
            self.logger.debug('[DEBUG] _play_song: before load_file, start_position=%s', saved_position)
            try:
                self.streaming_controller.load_file(download_path, start_position_seconds=saved_position)
            except Exception as e:
                self.logger.error('Failed to load file into streaming controller: %s', e)
                self.state = PlaybackState.ERROR
                self._handle_error(song['id'], f'Playback failed: {str(e)}')
                return False
            
            # Mark as current song
            self.current_song = song
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
    
    def stop_playback(self) -> bool:
        """
        Stop current playback and return to idle state.
        Unlike skip(), this does not try to load the next song.
        
        Returns:
            True if stopped, False otherwise
        """
        with self.lock:
            if not self.current_song and self.state == PlaybackState.IDLE:
                self.logger.debug('Already idle, nothing to stop')
                return False
            
            self.logger.info('Stopping playback')
            try:
                if self.current_song:
                    self.streaming_controller.stop_playback()
                self.current_song = None
                self.state = PlaybackState.IDLE
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
            
            if not self.current_song:
                self.logger.info('No current song, trying to start playback')
                return self._load_and_play_next()
            
            # Get next song after current
            next_song = self.queue_manager.get_next_song_after(self.current_song['id'])
            
            if not next_song:
                self.logger.info('No next song available to skip to')
                return False
            
            # Record playback history for the song we're leaving (partial play)
            current_position = self.streaming_controller.get_position()
            if current_position is None:
                current_position = self.current_song.get('playback_position_seconds', 0)
            
            start_position = self.current_song.get('playback_position_seconds', 0)
            
            self.queue_manager.record_playback_history(
                queue_item_id=self.current_song['id'],
                user_name=self.current_song['user_name'],
                youtube_video_id=self.current_song['youtube_video_id'],
                title=self.current_song['title'],
                duration_seconds=self.current_song.get('duration_seconds'),
                pitch_semitones=self.current_song.get('pitch_semitones', 0),
                playback_position_start=start_position,
                playback_position_end=current_position
            )
            
            # Save playback position so user can resume later if they navigate back
            self.queue_manager.update_playback_position(self.current_song['id'], int(current_position) if current_position else 0)
            
            # Stop current playback (but do NOT mark as played)
            self.logger.debug('[DEBUG] skip: before stop_playback, current=%s next=%s', self.current_song['id'], next_song['id'])
            self.streaming_controller.stop_playback()
            self.logger.debug('[DEBUG] skip: after stop_playback')
            
            # Load and play the next song
            return self._play_song(next_song)
    
    def jump_to_song(self, item_id: int, resume_position: Optional[int] = None) -> bool:
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
            
            # Save current playback position if there's a current song
            if self.current_song:
                current_position = self.streaming_controller.get_position()
                if current_position is not None:
                    self.queue_manager.update_playback_position(self.current_song['id'], int(current_position))
                self.streaming_controller.stop_playback()
            
            # Override resume position if provided
            if resume_position and resume_position > 0:
                song = dict(song)  # Copy to avoid modifying cached item
                song['playback_position_seconds'] = resume_position
            
            # Load and play the target song (navigation-based, no marking as played)
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
            
            if not self.current_song:
                self.logger.info('No current song, cannot go to previous')
                return False
            
            # Get previous song before current
            prev_song = self.queue_manager.get_previous_song_before(self.current_song['id'])
            
            if not prev_song:
                self.logger.info('No previous song available')
                return False
            
            # Save current playback position
            current_position = self.streaming_controller.get_position()
            if current_position is not None:
                self.queue_manager.update_playback_position(self.current_song['id'], int(current_position))
            
            # Stop current playback (but do NOT mark as played or record history)
            self.streaming_controller.stop_playback()
            
            # Load and play the previous song
            return self._play_song(prev_song)
    
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
            if self.current_song:
                self.logger.info('Song ended: %s', self.current_song['title'])
                
                # Get final playback position
                final_position = self.streaming_controller.get_position()
                if final_position is None:
                    final_position = self.current_song.get('playback_position_seconds', 0)
                
                # Get start position (for resume cases)
                start_position = self.current_song.get('playback_position_seconds', 0)
                
                # Record in playback history
                self.queue_manager.record_playback_history(
                    queue_item_id=self.current_song['id'],
                    user_name=self.current_song['user_name'],
                    youtube_video_id=self.current_song['youtube_video_id'],
                    title=self.current_song['title'],
                    duration_seconds=self.current_song.get('duration_seconds'),
                    pitch_semitones=self.current_song.get('pitch_semitones', 0),
                    playback_position_start=start_position,
                    playback_position_end=final_position
                )
                
                # Mark as played and clear playback position
                self.queue_manager.mark_played(self.current_song['id'])
                self.queue_manager.update_playback_position(self.current_song['id'], 0)
            else:
                self.logger.info('Song ended: unknown')
            
            # Reset pitch
            self.streaming_controller.set_pitch_shift(0)
            
            # Load next song
            self.current_song = None
            if not self._load_and_play_next():
                self.logger.info('No more songs, entering idle state')
                self.state = PlaybackState.IDLE
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current playback status.
        
        Returns:
            Dictionary with playback state and current song info
        """
        with self.lock:
            status = {
                'state': self.state.value,
                'current_song': self.current_song,
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
            if not self.current_song:
                self.logger.warning('No current song to adjust pitch')
                return False
            
            # Update in queue
            self.queue_manager.update_pitch(self.current_song['id'], semitones)
            
            # Update current song dict
            self.current_song['pitch_semitones'] = semitones
            
            # Apply to streaming controller
            self.streaming_controller.set_pitch_shift(semitones)
            
            self.logger.info('Set pitch to %s semitones for current song', semitones)
            return True
    
    def shutdown(self):
        """Shutdown the playback controller and cleanup all resources."""
        self.logger.info('Shutting down playback controller')
        self._monitoring = False
        self._tracking_position = False
        
        # Stop download monitor thread
        if self._download_monitor_thread and self._download_monitor_thread.is_alive():
            self._download_monitor_thread.join(timeout=2.0)
            if self._download_monitor_thread.is_alive():
                self.logger.warning('Download monitor thread did not stop within timeout')
        
        # Stop position tracking thread
        if self._position_tracking_thread and self._position_tracking_thread.is_alive():
            self._position_tracking_thread.join(timeout=2.0)
            if self._position_tracking_thread.is_alive():
                self.logger.warning('Position tracking thread did not stop within timeout')
        
        # Stop streaming
        if self.streaming_controller:
            self.streaming_controller.stop()
        
        self.logger.info('Playback controller shut down')

