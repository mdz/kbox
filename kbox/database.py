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
        
        # Create indexes for efficient queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_playback_history_played_at 
            ON playback_history(played_at)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_playback_history_video_id_played_at 
            ON playback_history(youtube_video_id, played_at DESC)
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
        
        # Run migrations
        self._run_migrations(conn)
        
        conn.close()
        self.logger.debug('Database schema created/verified')
    
    def _run_migrations(self, conn):
        """Run database migrations."""
        cursor = conn.cursor()
        
        # Migration: Remove playback_position_seconds column from queue_items
        # Check if column exists
        cursor.execute("PRAGMA table_info(queue_items)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'playback_position_seconds' in columns:
            self.logger.info('Migrating database: removing playback_position_seconds column')
            
            # SQLite doesn't support DROP COLUMN before 3.35.0, so we recreate the table
            cursor.execute('''
                CREATE TABLE queue_items_new (
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
                    error_message TEXT
                )
            ''')
            
            # Copy data (excluding playback_position_seconds)
            cursor.execute('''
                INSERT INTO queue_items_new 
                (id, position, user_name, youtube_video_id, title, duration_seconds, 
                 thumbnail_url, pitch_semitones, download_status, download_path, 
                 created_at, played_at, error_message)
                SELECT id, position, user_name, youtube_video_id, title, duration_seconds,
                       thumbnail_url, pitch_semitones, download_status, download_path,
                       created_at, played_at, error_message
                FROM queue_items
            ''')
            
            # Drop old table and rename new one
            cursor.execute('DROP TABLE queue_items')
            cursor.execute('ALTER TABLE queue_items_new RENAME TO queue_items')
            
            # Recreate indexes
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_queue_position 
                ON queue_items(position)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_queue_status 
                ON queue_items(download_status)
            ''')
            
            conn.commit()
            self.logger.info('Migration complete: playback_position_seconds column removed')
    
    
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

