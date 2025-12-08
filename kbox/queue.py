"""
Queue management for kbox.

Handles song queue operations with persistence.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from .database import Database

class QueueManager:
    """Manages the song queue with persistence."""
    
    # Download status constants
    STATUS_PENDING = 'pending'
    STATUS_DOWNLOADING = 'downloading'
    STATUS_READY = 'ready'
    STATUS_ERROR = 'error'
    
    def __init__(self, database: Database):
        """
        Initialize QueueManager.
        
        Args:
            database: Database instance for persistence
        """
        self.database = database
        self.logger = logging.getLogger(__name__)
    
    def add_song(
        self,
        user_name: str,
        youtube_video_id: str,
        title: str,
        duration_seconds: Optional[int] = None,
        thumbnail_url: Optional[str] = None,
        pitch_semitones: Optional[int] = None
    ) -> int:
        """
        Add a song to the end of the queue.
        
        If pitch_semitones is not provided (None), will check for a saved pitch setting
        for this video and use it if available. Otherwise defaults to 0.
        
        Args:
            user_name: Name of the user who requested the song
            youtube_video_id: YouTube video ID
            title: Song title
            duration_seconds: Duration in seconds (optional)
            thumbnail_url: Thumbnail URL (optional)
            pitch_semitones: Pitch adjustment in semitones. If None, will use saved setting if available, otherwise 0.
        
        Returns:
            ID of the created queue item
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # If pitch not explicitly provided, check for saved setting
            if pitch_semitones is None:
                settings = self.get_last_song_settings(youtube_video_id)
                saved_pitch = settings.get('pitch_semitones')
                pitch_semitones = saved_pitch if saved_pitch is not None else 0
                if saved_pitch is not None:
                    self.logger.debug('Using saved pitch setting for %s: %s semitones', youtube_video_id, saved_pitch)
            
            # Get the highest position
            cursor.execute('SELECT MAX(position) as max_pos FROM queue_items')
            result = cursor.fetchone()
            next_position = (result['max_pos'] or 0) + 1
            
            # Insert new item
            cursor.execute('''
                INSERT INTO queue_items 
                (position, user_name, youtube_video_id, title, duration_seconds, 
                 thumbnail_url, pitch_semitones, download_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                next_position,
                user_name,
                youtube_video_id,
                title,
                duration_seconds,
                thumbnail_url,
                pitch_semitones,
                self.STATUS_PENDING
            ))
            
            item_id = cursor.lastrowid
            conn.commit()
            
            self.logger.info('Added song to queue: %s by %s (ID: %s, pitch: %s)', title, user_name, item_id, pitch_semitones)
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
    
    def get_queue(self) -> List[Dict[str, Any]]:
        """
        Get the entire queue ordered by position.
        
        Returns:
            List of queue items as dictionaries
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, position, user_name, youtube_video_id, title, 
                       duration_seconds, thumbnail_url, pitch_semitones,
                       download_status, download_path, created_at, played_at,
                       playback_position_seconds, error_message
                FROM queue_items
                WHERE played_at IS NULL
                ORDER BY position
            ''')
            
            items = []
            for row in cursor.fetchall():
                items.append(dict(row))
            
            return items
        finally:
            conn.close()
    
    def get_next_song(self) -> Optional[Dict[str, Any]]:
        """
        Get the next ready song in the queue.
        
        Returns:
            Queue item dictionary if found, None otherwise
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, position, user_name, youtube_video_id, title,
                       duration_seconds, thumbnail_url, pitch_semitones,
                       download_status, download_path, created_at, played_at,
                       playback_position_seconds, error_message
                FROM queue_items
                WHERE download_status = ? AND played_at IS NULL
                ORDER BY position
                LIMIT 1
            ''', (self.STATUS_READY,))
            
            result = cursor.fetchone()
            return dict(result) if result else None
        finally:
            conn.close()
    
    def clear_queue(self) -> int:
        """
        Clear all items from the queue.
        
        Returns:
            Number of items removed
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) as count FROM queue_items WHERE played_at IS NULL')
            count = cursor.fetchone()['count']
            
            cursor.execute('DELETE FROM queue_items WHERE played_at IS NULL')
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
            
            updates = ['download_status = ?']
            params = [status]
            
            if download_path is not None:
                updates.append('download_path = ?')
                params.append(download_path)
            
            if error_message is not None:
                updates.append('error_message = ?')
                params.append(error_message)
            elif status != self.STATUS_ERROR:
                # Clear error message if status is not error
                updates.append('error_message = NULL')
            
            params.append(item_id)
            
            cursor.execute(f'''
                UPDATE queue_items 
                SET {', '.join(updates)}
                WHERE id = ?
            ''', params)
            
            updated = cursor.rowcount > 0
            conn.commit()
            
            if updated:
                self.logger.debug('Updated download status for item %s: %s', item_id, status)
            else:
                self.logger.warning('Queue item %s not found for status update', item_id)
            
            return updated
        finally:
            conn.close()
    
    def update_playback_position(self, item_id: int, position_seconds: int) -> bool:
        """
        Update playback position for a queue item.
        
        Args:
            item_id: ID of the queue item
            position_seconds: Current playback position in seconds
            
        Returns:
            True if updated, False if item not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE queue_items 
                SET playback_position_seconds = ?
                WHERE id = ?
            ''', (position_seconds, item_id))
            
            updated = cursor.rowcount > 0
            conn.commit()
            
            if updated:
                self.logger.debug('Updated playback position for item %s: %s seconds', item_id, position_seconds)
            else:
                self.logger.warning('Queue item %s not found for position update', item_id)
            
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
        
        Args:
            queue_item_id: ID of the queue item
            user_name: Name of the user who requested the song
            youtube_video_id: YouTube video ID
            title: Song title
            duration_seconds: Duration in seconds (optional)
            pitch_semitones: Pitch adjustment used
            playback_position_start: Position where playback started (for resume)
            playback_position_end: Position where playback ended (None if completed)
        
        Returns:
            ID of the created history record
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO playback_history 
                (queue_item_id, user_name, youtube_video_id, title, duration_seconds,
                 pitch_semitones, playback_position_start, playback_position_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                queue_item_id,
                user_name,
                youtube_video_id,
                title,
                duration_seconds,
                pitch_semitones,
                playback_position_start,
                playback_position_end
            ))
            
            history_id = cursor.lastrowid
            conn.commit()
            
            # Calculate duration for logging
            playback_duration = None
            if playback_position_end is not None:
                playback_duration = max(0, playback_position_end - playback_position_start)
            
            self.logger.debug('Recorded playback history for item %s (history ID: %s, played: %s seconds)', 
                            queue_item_id, history_id, playback_duration)
            return history_id
        finally:
            conn.close()
    
    def get_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a specific queue item by ID.
        
        Args:
            item_id: ID of the queue item
        
        Returns:
            Queue item dictionary if found, None otherwise
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, position, user_name, youtube_video_id, title,
                       duration_seconds, thumbnail_url, pitch_semitones,
                       download_status, download_path, created_at, played_at,
                       playback_position_seconds, error_message
                FROM queue_items
                WHERE id = ?
            ''', (item_id,))
            
            result = cursor.fetchone()
            return dict(result) if result else None
        finally:
            conn.close()
    
    def get_last_song_settings(self, youtube_video_id: str) -> Dict[str, Any]:
        """
        Get all last used settings for a song from playback history.
        
        Args:
            youtube_video_id: YouTube video ID
        
        Returns:
            Dictionary of settings from the most recent playback, or empty dict if not found.
            Currently includes: pitch_semitones (and can be extended for other settings)
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # Query playback history for the most recent entry with this video ID
            # Get all settings-related columns
            cursor.execute('''
                SELECT pitch_semitones
                FROM playback_history
                WHERE youtube_video_id = ?
                ORDER BY played_at DESC
                LIMIT 1
            ''', (youtube_video_id,))
            
            result = cursor.fetchone()
            if result:
                settings = {
                    'pitch_semitones': result['pitch_semitones']
                }
                return settings
            return {}
        finally:
            conn.close()
    
    def update_pitch(self, item_id: int, pitch_semitones: int) -> bool:
        """
        Update pitch adjustment for a queue item.
        
        The pitch will be saved to playback history when the song ends.
        This ensures the last used pitch (even if changed during playback) is recorded.
        
        Args:
            item_id: ID of the queue item
            pitch_semitones: New pitch adjustment in semitones
        
        Returns:
            True if updated, False if item not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # Update pitch in queue item
            cursor.execute('''
                UPDATE queue_items 
                SET pitch_semitones = ?
                WHERE id = ?
            ''', (pitch_semitones, item_id))
            
            updated = cursor.rowcount > 0
            conn.commit()
            
            if updated:
                self.logger.debug('Updated pitch for item %s: %s semitones', item_id, pitch_semitones)
            else:
                self.logger.warning('Queue item %s not found', item_id)
            
            return updated
        finally:
            conn.close()

