"""
Database module for kbox.

Handles SQLite database initialization, schema creation, and connection management.
"""

import logging
import sqlite3
import os
from pathlib import Path
from typing import Optional

class Database:
    """Manages SQLite database connection and schema."""
    
    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize database connection.
        
        Args:
            db_path: Path to SQLite database file. If None, uses ~/.kbox/kbox.db
        """
        self.logger = logging.getLogger(__name__)
        
        if db_path is None:
            # Default to ~/.kbox/kbox.db
            home = Path.home()
            kbox_dir = home / '.kbox'
            kbox_dir.mkdir(exist_ok=True)
            db_path = str(kbox_dir / 'kbox.db')
        
        self.db_path = db_path
        self._ensure_schema()
        self.logger.info('Database initialized at %s', self.db_path)
    
    def _ensure_schema(self):
        """Ensure database schema exists (thread-safe)."""
        # Create a temporary connection just to create schema
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Queue items table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                youtube_video_id TEXT NOT NULL,
                title TEXT NOT NULL,
                duration_seconds INTEGER,
                thumbnail_url TEXT,
                pitch_semitones INTEGER DEFAULT 0,
                download_status TEXT DEFAULT 'pending',
                download_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                played_at TIMESTAMP,
                playback_position_seconds INTEGER DEFAULT 0,
                error_message TEXT
            )
        ''')
        
        # Configuration table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Playback history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS playback_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_item_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                youtube_video_id TEXT NOT NULL,
                title TEXT NOT NULL,
                duration_seconds INTEGER,
                pitch_semitones INTEGER DEFAULT 0,
                played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                playback_position_start INTEGER DEFAULT 0,
                playback_position_end INTEGER,
                FOREIGN KEY (queue_item_id) REFERENCES queue_items(id)
            )
        ''')
        
        # Create index on played_at for efficient queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_playback_history_played_at 
            ON playback_history(played_at)
        ''')
        
        # Create indexes
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_queue_position 
            ON queue_items(position)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_queue_status 
            ON queue_items(download_status)
        ''')
        
        conn.commit()
        conn.close()
        self.logger.debug('Database schema created/verified')
    
    
    def get_connection(self):
        """
        Get a new database connection (thread-safe).
        
        Each thread should get its own connection. Caller is responsible
        for closing the connection when done.
        """
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    
    def close(self):
        """Close database connection (no-op since we use per-thread connections)."""
        # No-op since we create connections per-thread now
        pass
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

