"""
Playback controller for kbox.

Orchestrates playback, manages state transitions, and handles error recovery.
"""

import logging
import threading
from enum import Enum
from typing import Optional, Dict, Any
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
        
        # Start download monitor thread
        self._download_monitor_thread = None
        self._monitoring = True
        self._start_download_monitor()
    
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
                    
                    # Sleep before next check
                    threading.Event().wait(2.0)  # Check every 2 seconds
                except Exception as e:
                    self.logger.error('Error in download monitor: %s', e, exc_info=True)
                    threading.Event().wait(5.0)  # Wait longer on error
        
        self._download_monitor_thread = threading.Thread(target=monitor, daemon=True)
        self._download_monitor_thread.start()
        self.logger.debug('Download monitor started')
    
    def _on_download_status(self, item_id: int, status: str, path: Optional[str], error: Optional[str]):
        """Callback for download status updates."""
        if status == 'ready' and path:
            self.queue_manager.update_download_status(
                item_id,
                QueueManager.STATUS_READY,
                download_path=path
            )
            self.logger.info('Download complete for queue item %s: %s', item_id, path)
            
            # If we're idle and this is the next song, auto-start playback
            if self.state == PlaybackState.IDLE:
                next_song = self.queue_manager.get_next_song()
                if next_song and next_song['id'] == item_id:
                    self.logger.info('Next song ready, auto-starting playback')
                    self.play()
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
                # TODO: Resume streaming controller
                self.state = PlaybackState.PLAYING
                return True
            
            # Start new song
            return self._load_and_play_next()
    
    def _load_and_play_next(self) -> bool:
        """Load next ready song and start playback."""
        next_song = self.queue_manager.get_next_song()
        
        if not next_song:
            self.logger.info('No ready songs in queue')
            self.state = PlaybackState.IDLE
            return False
        
        # Check if file exists
        download_path = next_song.get('download_path')
        if not download_path:
            self.logger.warning('No download path for song %s', next_song['id'])
            self.state = PlaybackState.IDLE
            return False
        
        try:
            self.logger.info('Loading song: %s by %s', next_song['title'], next_song['user_name'])
            
            # Set pitch for this song
            pitch = next_song.get('pitch_semitones', 0)
            self.streaming_controller.set_pitch_shift(pitch)
            
            # Load file into streaming controller
            # TODO: Implement load_file in StreamingController
            # self.streaming_controller.load_file(download_path)
            
            # Mark as current song
            self.current_song = next_song
            self.state = PlaybackState.PLAYING
            
            # Mark as played in queue
            self.queue_manager.mark_played(next_song['id'])
            
            self.logger.info('Playback started: %s', next_song['title'])
            return True
            
        except Exception as e:
            self.logger.error('Error loading song: %s', e, exc_info=True)
            self.state = PlaybackState.ERROR
            self._handle_error(next_song['id'], str(e))
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
            # TODO: Pause streaming controller
            self.state = PlaybackState.PAUSED
            return True
    
    def skip(self) -> bool:
        """
        Skip to next song.
        
        Returns:
            True if skipped, False otherwise
        """
        with self.lock:
            self.logger.info('Skipping current song')
            
            # Stop current playback
            # TODO: Stop streaming controller
            
            # Load next song
            self.current_song = None
            return self._load_and_play_next()
    
    def previous(self) -> bool:
        """
        Go to previous song.
        
        Note: This is a simplified implementation. A full implementation would
        need to track playback history.
        
        Returns:
            True if successful, False otherwise
        """
        with self.lock:
            self.logger.warning('Previous song not yet implemented')
            # TODO: Implement playback history tracking
            return False
    
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
            self.logger.info('Song ended: %s', self.current_song['title'] if self.current_song else 'unknown')
            
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
    
    def stop(self):
        """Stop playback and cleanup."""
        self.logger.info('Stopping playback controller')
        self._monitoring = False
        if self._download_monitor_thread:
            self._download_monitor_thread.join(timeout=2.0)

