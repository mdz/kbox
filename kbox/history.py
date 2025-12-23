"""
Playback history management.

Maintains a permanent record of performances, including settings used
and playback metrics.
"""

import logging
import json
from typing import Dict, Any, Optional

from .database import Database


class HistoryManager:
    """
    Manages playback history.
    
    Records performances, retrieves past settings, and provides history queries.
    Operates independently from the queue - history is permanent, queue is ephemeral.
    """
    
    def __init__(self, database: Database):
        """
        Initialize history manager.
        
        Args:
            database: Database instance for persistence
        """
        self.database = database
        self.logger = logging.getLogger(__name__)
    
    # JSON encoding/decoding utilities
    
    def _encode_settings(self, settings: Dict[str, Any]) -> str:
        """Encode settings dict to JSON string."""
        return json.dumps(settings)
    
    def _decode_settings(self, settings_json: str) -> Dict[str, Any]:
        """Decode settings from JSON string."""
        if not settings_json:
            return {}
        try:
            return json.loads(settings_json)
        except json.JSONDecodeError:
            self.logger.warning('Failed to decode settings JSON: %s', settings_json)
            return {}
    
    def _encode_metadata(self, metadata: Dict[str, Any]) -> str:
        """Encode song metadata dict to JSON string."""
        return json.dumps(metadata)
    
    def _decode_metadata(self, metadata_json: str) -> Dict[str, Any]:
        """Decode song metadata from JSON string."""
        if not metadata_json:
            return {}
        try:
            return json.loads(metadata_json)
        except json.JSONDecodeError:
            self.logger.warning('Failed to decode metadata JSON: %s', metadata_json)
            return {}
    
    def _encode_performance(self, performance: Dict[str, Any]) -> str:
        """Encode performance metrics dict to JSON string."""
        return json.dumps(performance)
    
    def _decode_performance(self, performance_json: str) -> Dict[str, Any]:
        """Decode performance metrics from JSON string."""
        if not performance_json:
            return {}
        try:
            return json.loads(performance_json)
        except json.JSONDecodeError:
            self.logger.warning('Failed to decode performance JSON: %s', performance_json)
            return {}
    
    # History recording
    
    def record_performance(
        self,
        queue_item: Dict[str, Any],
        played_duration_seconds: int,
        playback_end_position_seconds: int,
        completion_percentage: float
    ) -> int:
        """
        Record a performance in history.
        
        Args:
            queue_item: Queue item dict with song details
            played_duration_seconds: How long the song played
            playback_end_position_seconds: Position where playback ended
            completion_percentage: Percentage of song completed
        
        Returns:
            ID of the created history record
        """
        # Extract metadata for JSON
        song_metadata = {
            'title': queue_item.get('title', 'Unknown'),
            'duration_seconds': queue_item.get('duration_seconds'),
            'thumbnail_url': queue_item.get('thumbnail_url'),
        }
        
        # Extract settings
        settings = {
            'pitch_semitones': queue_item.get('pitch_semitones', 0)
        }
        
        # Build performance metrics
        performance = {
            'played_duration_seconds': played_duration_seconds,
            'playback_end_position_seconds': playback_end_position_seconds,
            'completion_percentage': completion_percentage
        }
        
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO playback_history (
                    user_id,
                    user_name,
                    source,
                    source_id,
                    song_metadata_json,
                    settings_json,
                    performance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                queue_item['user_id'],
                queue_item['user_name'],
                queue_item['source'],
                queue_item['source_id'],
                self._encode_metadata(song_metadata),
                self._encode_settings(settings),
                self._encode_performance(performance)
            ))
            conn.commit()
            history_id = cursor.lastrowid
            self.logger.info(
                'Recorded history: %s sang %s (source=%s, id=%s, %.1f%% complete)',
                queue_item['user_name'],
                song_metadata.get('title', 'Unknown'),
                queue_item['source'],
                queue_item['source_id'],
                completion_percentage
            )
            return history_id
        finally:
            conn.close()
    
    # History queries
    
    def get_last_settings(self, source: str, source_id: str, user_id: str) -> Dict[str, Any]:
        """
        Get the last used settings for a song from playback history for a specific user.
        
        Args:
            source: Source type (e.g., 'youtube')
            source_id: Source-specific identifier
            user_id: UUID of the user
        
        Returns:
            Settings dict (e.g., {'pitch_semitones': -2}), or empty dict if not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # Query most recent performance by this user for this song
            cursor.execute('''
                SELECT settings_json
                FROM playback_history
                WHERE source = ? AND source_id = ? AND user_id = ?
                ORDER BY performed_at DESC, id DESC
                LIMIT 1
            ''', (source, source_id, user_id))
            
            result = cursor.fetchone()
            if result:
                return self._decode_settings(result['settings_json'])
            return {}
        finally:
            conn.close()
    
    def get_user_history(self, user_id: str, limit: int = 50) -> list[Dict[str, Any]]:
        """
        Get playback history for a specific user.
        
        Args:
            user_id: UUID of the user
            limit: Maximum number of records to return (default 50)
        
        Returns:
            List of history records with song metadata, settings, and performance info
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT 
                    id,
                    source,
                    source_id,
                    performed_at,
                    song_metadata_json,
                    settings_json,
                    performance_json
                FROM playback_history
                WHERE user_id = ?
                ORDER BY performed_at DESC, id DESC
                LIMIT ?
            ''', (user_id, limit))
            
            results = []
            for row in cursor.fetchall():
                metadata = self._decode_metadata(row['song_metadata_json'])
                settings = self._decode_settings(row['settings_json'])
                performance = self._decode_performance(row['performance_json'])
                
                results.append({
                    'id': row['id'],
                    'source': row['source'],
                    'source_id': row['source_id'],
                    'performed_at': row['performed_at'],
                    'title': metadata.get('title', 'Unknown'),
                    'duration_seconds': metadata.get('duration_seconds'),
                    'thumbnail_url': metadata.get('thumbnail_url'),
                    'pitch_semitones': settings.get('pitch_semitones', 0),
                    'played_duration_seconds': performance.get('played_duration_seconds'),
                    'playback_end_position_seconds': performance.get('playback_end_position_seconds'),
                    'completion_percentage': performance.get('completion_percentage'),
                })
            
            return results
        finally:
            conn.close()

