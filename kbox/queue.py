"""
Queue management for kbox.

Handles song queue operations with persistence and download management.
"""

import json
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, TYPE_CHECKING

from .database import Database

if TYPE_CHECKING:
    from .youtube import YouTubeClient

class QueueManager:
    """Manages the song queue with persistence and downloads."""
    
    # Download status constants
    STATUS_PENDING = 'pending'
    STATUS_DOWNLOADING = 'downloading'
    STATUS_READY = 'ready'
    STATUS_ERROR = 'error'
    
    # JSON utility methods
    @staticmethod
    def _encode_settings(settings: Dict[str, Any]) -> str:
        """Encode settings dict to JSON string."""
        return json.dumps(settings)
    
    @staticmethod
    def _decode_settings(settings_json: str) -> Dict[str, Any]:
        """Decode settings JSON string to dict."""
        if not settings_json:
            return {}
        try:
            return json.loads(settings_json)
        except (json.JSONDecodeError, TypeError):
            return {}
    
    @staticmethod
    def _encode_metadata(metadata: Dict[str, Any]) -> str:
        """Encode song metadata dict to JSON string."""
        return json.dumps(metadata)
    
    @staticmethod
    def _decode_metadata(metadata_json: str) -> Dict[str, Any]:
        """Decode metadata JSON string to dict."""
        if not metadata_json:
            return {}
        try:
            return json.loads(metadata_json)
        except (json.JSONDecodeError, TypeError):
            return {}
    
    @staticmethod
    def _encode_download_info(download_info: Dict[str, Any]) -> str:
        """Encode download info dict to JSON string."""
        return json.dumps(download_info)
    
    @staticmethod
    def _decode_download_info(download_json: Optional[str]) -> Dict[str, Any]:
        """Decode download info JSON string to dict."""
        if not download_json:
            return {}
        try:
            return json.loads(download_json)
        except (json.JSONDecodeError, TypeError):
            return {}
    
    def __init__(self, database: Database, youtube_client: Optional['YouTubeClient'] = None):
        """
        Initialize QueueManager.
        
        Args:
            database: Database instance for persistence
            youtube_client: YouTubeClient for downloading videos (optional)
        """
        self.database = database
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
                    self.logger.error('Error in download monitor: %s', e, exc_info=True)
                    threading.Event().wait(5.0)  # Wait longer on error
        
        self._download_monitor_thread = threading.Thread(
            target=monitor, daemon=True, name='DownloadMonitor'
        )
        self._download_monitor_thread.start()
        self.logger.info('Download monitor started')
    
    def _process_download_queue(self):
        """Process pending and stuck downloads."""
        queue = self.get_queue()
        
        for item in queue:
            if item['download_status'] == self.STATUS_PENDING:
                self._start_download(item)
            elif item['download_status'] == self.STATUS_DOWNLOADING:
                self._check_stuck_download(item)
    
    def _start_download(self, item: Dict[str, Any]):
        """Start downloading a queue item."""
        self.logger.info('Starting download for %s (ID: %s)', 
                        item['title'], item['id'])
        
        item_id = item['id']
        
        def on_status(status: str, path: Optional[str], error: Optional[str]):
            self._on_download_status(item_id, status, path, error)
        
        # For YouTube source, use source_id (video ID)
        if item['source'] == 'youtube':
            self.youtube_client.download_video(
                item['source_id'],
                item['id'],
                status_callback=on_status
            )
        else:
            self.logger.error('Unsupported source type: %s', item['source'])
            self.update_download_status(item['id'], self.STATUS_ERROR, 
                                       error_message=f"Unsupported source: {item['source']}")
            return
        
        # Update status to downloading
        self.update_download_status(item['id'], self.STATUS_DOWNLOADING)
    
    def _check_stuck_download(self, item: Dict[str, Any]):
        """Check if a download is stuck and recover if possible."""
        # First, check if file exists (download completed but callback failed)
        # Only works for YouTube source currently
        if item['source'] == 'youtube':
            download_path = self.youtube_client.get_download_path(item['source_id'])
            if download_path and download_path.exists():
                self.logger.info('Found completed download for %s (ID: %s), updating status', 
                               item['title'], item['id'])
                self.update_download_status(
                    item['id'],
                    self.STATUS_READY,
                    download_path=str(download_path)
                )
                return
        
        # Check if download has been stuck for too long
        try:
            created_at_str = item.get('created_at')
            if created_at_str:
                if isinstance(created_at_str, str):
                    created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                else:
                    created_at = created_at_str
                
                # Check if it's been more than timeout since creation
                if datetime.now(created_at.tzinfo) - created_at > self._download_timeout:
                    self.logger.warning(
                        'Download stuck for %s (ID: %s) for more than %s, resetting to pending', 
                        item['title'], item['id'], self._download_timeout
                    )
                    self.update_download_status(item['id'], self.STATUS_PENDING)
        except (ValueError, TypeError) as e:
            self.logger.debug('Could not parse created_at for item %s: %s', item['id'], e)
    
    def _on_download_status(self, item_id: int, status: str, path: Optional[str], error: Optional[str]):
        """Callback for download status updates."""
        if status == 'ready' and path:
            self.update_download_status(
                item_id,
                self.STATUS_READY,
                download_path=path
            )
            self.logger.info('Download complete for queue item %s: %s', item_id, path)
        elif status == 'error' and error:
            self.update_download_status(
                item_id,
                self.STATUS_ERROR,
                error_message=error
            )
            self.logger.error('Download failed for queue item %s: %s', item_id, error)
    
    def stop_download_monitor(self):
        """Stop the download monitor thread."""
        if not self._monitoring:
            return
        
        self.logger.info('Stopping download monitor...')
        self._monitoring = False
        
        if self._download_monitor_thread and self._download_monitor_thread.is_alive():
            self._download_monitor_thread.join(timeout=2.0)
            if self._download_monitor_thread.is_alive():
                self.logger.warning('Download monitor thread did not stop within timeout')
        
        self.logger.info('Download monitor stopped')
    
    # =========================================================================
    # Queue Operations
    # =========================================================================
    
    def add_song(
        self,
        user_name: str,
        source: str,
        source_id: str,
        title: str,
        duration_seconds: Optional[int] = None,
        thumbnail_url: Optional[str] = None,
        channel: Optional[str] = None,
        pitch_semitones: int = 0
    ) -> int:
        """
        Add a song to the end of the queue.
        
        Args:
            user_name: Name of the user who requested the song
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
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # Get the highest position
            cursor.execute('SELECT MAX(position) as max_pos FROM queue_items')
            result = cursor.fetchone()
            next_position = (result['max_pos'] or 0) + 1
            
            # Build metadata JSON
            metadata = {
                'title': title,
                'duration_seconds': duration_seconds,
                'thumbnail_url': thumbnail_url,
            }
            if channel:
                metadata['channel'] = channel
            
            # Build settings JSON
            settings = {'pitch_semitones': pitch_semitones}
            
            # Insert new item
            cursor.execute('''
                INSERT INTO queue_items 
                (position, user_name, source, source_id, song_metadata_json,
                 settings_json, download_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                next_position,
                user_name,
                source,
                source_id,
                self._encode_metadata(metadata),
                self._encode_settings(settings),
                self.STATUS_PENDING
            ))
            
            item_id = cursor.lastrowid
            conn.commit()
            
            self.logger.info('Added song to queue: %s by %s (ID: %s, source: %s, pitch: %s)', 
                           title, user_name, item_id, source, pitch_semitones)
            return item_id
        finally:
            conn.close()
    
    def remove_song(self, item_id: int) -> bool:
        """
        Remove a song from the queue.
        
        Args:
            item_id: ID of the queue item to remove
        
        Returns:
            True if item was removed, False if not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # Get position of item to remove
            cursor.execute('SELECT position FROM queue_items WHERE id = ?', (item_id,))
            result = cursor.fetchone()
            
            if not result:
                self.logger.warning('Queue item %s not found', item_id)
                return False
            
            removed_position = result['position']
            
            # Delete the item
            cursor.execute('DELETE FROM queue_items WHERE id = ?', (item_id,))
            
            # Decrement positions of items after the removed one
            cursor.execute('''
                UPDATE queue_items 
                SET position = position - 1 
                WHERE position > ?
            ''', (removed_position,))
            
            conn.commit()
            
            self.logger.info('Removed queue item %s', item_id)
            return True
        finally:
            conn.close()
    
    def reorder_song(self, item_id: int, new_position: int) -> bool:
        """
        Move a song to a new position in the queue.
        
        Args:
            item_id: ID of the queue item to move
            new_position: New position (1-based)
        
        Returns:
            True if successful, False if item not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # Get current position
            cursor.execute('SELECT position FROM queue_items WHERE id = ?', (item_id,))
            result = cursor.fetchone()
            
            if not result:
                self.logger.warning('Queue item %s not found', item_id)
                return False
            
            old_position = result['position']
            
            if old_position == new_position:
                self.logger.debug('Item %s already at position %s', item_id, new_position)
                return True
            
            # Get max position
            cursor.execute('SELECT MAX(position) as max_pos FROM queue_items')
            max_pos = cursor.fetchone()['max_pos'] or 0
            
            if new_position < 1 or new_position > max_pos:
                self.logger.warning('Invalid position %s (max: %s)', new_position, max_pos)
                return False
            
            # Shift items to make room
            if new_position > old_position:
                # Moving down: shift items up
                cursor.execute('''
                    UPDATE queue_items 
                    SET position = position - 1 
                    WHERE position > ? AND position <= ?
                ''', (old_position, new_position))
            else:
                # Moving up: shift items down
                cursor.execute('''
                    UPDATE queue_items 
                    SET position = position + 1 
                    WHERE position >= ? AND position < ?
                ''', (new_position, old_position))
            
            # Update the item's position
            cursor.execute('UPDATE queue_items SET position = ? WHERE id = ?', (new_position, item_id))
            
            conn.commit()
            
            self.logger.info('Moved queue item %s from position %s to %s', item_id, old_position, new_position)
            return True
        finally:
            conn.close()
    
    def get_queue(self, include_played: bool = True) -> List[Dict[str, Any]]:
        """
        Get the entire queue ordered by position.
        
        Args:
            include_played: If True, include songs that have been played.
                           If False, only return unplayed songs.
        
        Returns:
            List of queue items as dictionaries with JSON fields decoded
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            if include_played:
                cursor.execute('''
                    SELECT id, position, user_name, source, source_id,
                           song_metadata_json, settings_json, download_json,
                           download_status, created_at, played_at
                    FROM queue_items
                    ORDER BY position
                ''')
            else:
                cursor.execute('''
                    SELECT id, position, user_name, source, source_id,
                           song_metadata_json, settings_json, download_json,
                           download_status, created_at, played_at
                    FROM queue_items
                    WHERE played_at IS NULL
                    ORDER BY position
                ''')
            
            items = []
            for row in cursor.fetchall():
                item = dict(row)
                # Decode JSON fields and merge into item dict
                metadata = self._decode_metadata(item.pop('song_metadata_json'))
                settings = self._decode_settings(item.pop('settings_json'))
                download_info = self._decode_download_info(item.pop('download_json'))
                
                # Merge all fields
                item.update(metadata)
                item.update(settings)
                item.update(download_info)
                
                items.append(item)
            
            return items
        finally:
            conn.close()
    
    def get_next_song(self) -> Optional[Dict[str, Any]]:
        """
        Get the next ready song in the queue.
        
        Returns:
            Queue item dictionary with JSON fields decoded, or None if not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, position, user_name, source, source_id,
                       song_metadata_json, settings_json, download_json,
                       download_status, created_at, played_at
                FROM queue_items
                WHERE download_status = ? AND played_at IS NULL
                ORDER BY position
                LIMIT 1
            ''', (self.STATUS_READY,))
            
            result = cursor.fetchone()
            if not result:
                return None
            
            item = dict(result)
            # Decode JSON fields and merge
            metadata = self._decode_metadata(item.pop('song_metadata_json'))
            settings = self._decode_settings(item.pop('settings_json'))
            download_info = self._decode_download_info(item.pop('download_json'))
            
            item.update(metadata)
            item.update(settings)
            item.update(download_info)
            
            return item
        finally:
            conn.close()
    
    def get_next_song_after(self, current_song_id: int) -> Optional[Dict[str, Any]]:
        """
        Get the next ready song in the queue after the specified song.
        
        Args:
            current_song_id: ID of the current song
        
        Returns:
            Queue item dictionary with JSON fields decoded, or None if not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # Get position of current song
            cursor.execute('SELECT position FROM queue_items WHERE id = ?', (current_song_id,))
            result = cursor.fetchone()
            if not result:
                return None
            
            current_position = result['position']
            
            # Get next ready song after current position
            cursor.execute('''
                SELECT id, position, user_name, source, source_id,
                       song_metadata_json, settings_json, download_json,
                       download_status, created_at, played_at
                FROM queue_items
                WHERE position > ? AND download_status = ?
                ORDER BY position
                LIMIT 1
            ''', (current_position, self.STATUS_READY))
            
            result = cursor.fetchone()
            if not result:
                return None
            
            item = dict(result)
            # Decode JSON fields and merge
            metadata = self._decode_metadata(item.pop('song_metadata_json'))
            settings = self._decode_settings(item.pop('settings_json'))
            download_info = self._decode_download_info(item.pop('download_json'))
            
            item.update(metadata)
            item.update(settings)
            item.update(download_info)
            
            return item
        finally:
            conn.close()
    
    def get_previous_song_before(self, current_song_id: int) -> Optional[Dict[str, Any]]:
        """
        Get the previous ready song in the queue before the specified song.
        
        Args:
            current_song_id: ID of the current song
        
        Returns:
            Queue item dictionary with JSON fields decoded, or None if not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # Get position of current song
            cursor.execute('SELECT position FROM queue_items WHERE id = ?', (current_song_id,))
            result = cursor.fetchone()
            if not result:
                return None
            
            current_position = result['position']
            
            # Get previous ready song before current position
            cursor.execute('''
                SELECT id, position, user_name, source, source_id,
                       song_metadata_json, settings_json, download_json,
                       download_status, created_at, played_at
                FROM queue_items
                WHERE position < ? AND download_status = ?
                ORDER BY position DESC
                LIMIT 1
            ''', (current_position, self.STATUS_READY))
            
            result = cursor.fetchone()
            if not result:
                return None
            
            item = dict(result)
            # Decode JSON fields and merge
            metadata = self._decode_metadata(item.pop('song_metadata_json'))
            settings = self._decode_settings(item.pop('settings_json'))
            download_info = self._decode_download_info(item.pop('download_json'))
            
            item.update(metadata)
            item.update(settings)
            item.update(download_info)
            
            return item
        finally:
            conn.close()
    
    def clear_queue(self) -> int:
        """
        Clear all items from the queue (both played and unplayed).
        
        Returns:
            Number of items removed
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) as count FROM queue_items')
            count = cursor.fetchone()['count']
            
            cursor.execute('DELETE FROM queue_items')
            conn.commit()
            
            self.logger.info('Cleared queue (%s items removed)', count)
            return count
        finally:
            conn.close()
    
    def update_download_status(
        self,
        item_id: int,
        status: str,
        download_path: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Update download status for a queue item.
        
        Args:
            item_id: ID of the queue item
            status: New status (STATUS_PENDING, STATUS_DOWNLOADING, STATUS_READY, STATUS_ERROR)
            download_path: Path to downloaded file (if status is READY)
            error_message: Error message (if status is ERROR)
        
        Returns:
            True if updated, False if item not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # Get current download_json to merge updates
            cursor.execute('SELECT download_json FROM queue_items WHERE id = ?', (item_id,))
            result = cursor.fetchone()
            if not result:
                self.logger.warning('Queue item %s not found for status update', item_id)
                return False
            
            download_info = self._decode_download_info(result['download_json'])
            
            # Update download info
            if download_path is not None:
                download_info['download_path'] = download_path
            if error_message is not None:
                download_info['error_message'] = error_message
            elif status != self.STATUS_ERROR:
                # Clear error message if status is not error
                download_info.pop('error_message', None)
            
            # Update database
            cursor.execute('''
                UPDATE queue_items 
                SET download_status = ?, download_json = ?
                WHERE id = ?
            ''', (status, self._encode_download_info(download_info), item_id))
            
            updated = cursor.rowcount > 0
            conn.commit()
            
            if updated:
                self.logger.debug('Updated download status for item %s: %s', item_id, status)
            else:
                self.logger.warning('Queue item %s not found for status update', item_id)
            
            return updated
        finally:
            conn.close()
    
    def mark_played(self, item_id: int) -> bool:
        """
        Mark a queue item as played.
        
        Args:
            item_id: ID of the queue item
        
        Returns:
            True if updated, False if item not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE queue_items 
                SET played_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (item_id,))
            
            updated = cursor.rowcount > 0
            conn.commit()
            
            if updated:
                self.logger.debug('Marked queue item %s as played', item_id)
            else:
                self.logger.warning('Queue item %s not found', item_id)
            
            return updated
        finally:
            conn.close()
    
    def record_playback_history(
        self,
        queue_item_id: int,
        user_name: str,
        youtube_video_id: str,
        title: str,
        duration_seconds: Optional[int] = None,
        pitch_semitones: int = 0,
        playback_position_start: int = 0,
        playback_position_end: Optional[int] = None
    ) -> int:
        """
        Record a song in playback history.
        
        PHASE 2: Playback history will be redesigned in Phase 2.
        This is stubbed out for now to avoid breaking existing code.
        
        Returns:
            Dummy history ID (0)
        """
        self.logger.debug('Playback history recording disabled (Phase 2 feature)')
        return 0
    
    def get_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a specific queue item by ID.
        
        Args:
            item_id: ID of the queue item
        
        Returns:
            Queue item dictionary with JSON fields decoded, or None if not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, position, user_name, source, source_id,
                       song_metadata_json, settings_json, download_json,
                       download_status, created_at, played_at
                FROM queue_items
                WHERE id = ?
            ''', (item_id,))
            
            result = cursor.fetchone()
            if not result:
                return None
            
            item = dict(result)
            # Decode JSON fields and merge into item dict
            metadata = self._decode_metadata(item.pop('song_metadata_json'))
            settings = self._decode_settings(item.pop('settings_json'))
            download_info = self._decode_download_info(item.pop('download_json'))
            
            # Merge all fields
            item.update(metadata)
            item.update(settings)
            item.update(download_info)
            
            return item
        finally:
            conn.close()
    
    def get_last_song_settings(self, youtube_video_id: str, user_name: str) -> Dict[str, Any]:
        """
        Get all last used settings for a song from playback history for a specific user.
        
        PHASE 2: Playback history will be redesigned in Phase 2.
        This is stubbed out for now to avoid breaking existing code.
        
        Returns:
            Empty dict (no history available in Phase 1)
        """
        self.logger.debug('Settings recall from history disabled (Phase 2 feature)')
        return {}
    
    def update_pitch(self, item_id: int, pitch_semitones: int) -> bool:
        """
        Update pitch adjustment for a queue item.
        
        Args:
            item_id: ID of the queue item
            pitch_semitones: New pitch adjustment in semitones
        
        Returns:
            True if updated, False if item not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # Get current settings to merge
            cursor.execute('SELECT settings_json FROM queue_items WHERE id = ?', (item_id,))
            result = cursor.fetchone()
            if not result:
                self.logger.warning('Queue item %s not found', item_id)
                return False
            
            settings = self._decode_settings(result['settings_json'])
            settings['pitch_semitones'] = pitch_semitones
            
            # Update settings in queue item
            cursor.execute('''
                UPDATE queue_items 
                SET settings_json = ?
                WHERE id = ?
            ''', (self._encode_settings(settings), item_id))
            
            updated = cursor.rowcount > 0
            conn.commit()
            
            if updated:
                self.logger.debug('Updated pitch for item %s: %s semitones', item_id, pitch_semitones)
            else:
                self.logger.warning('Queue item %s not found', item_id)
            
            return updated
        finally:
            conn.close()

