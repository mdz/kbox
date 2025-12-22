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
        
        # Queue items table - source-agnostic with JSON columns
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position INTEGER NOT NULL,
                download_status TEXT DEFAULT 'pending',
                played_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_name TEXT NOT NULL,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                song_metadata_json TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                download_json TEXT
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
        
        # Playback history table - permanent record of performances
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS playback_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                song_metadata_json TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                performance_json TEXT NOT NULL
            )
        ''')
        
        # Create indexes for common queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_history_user_song 
            ON playback_history(user_name, source, source_id, performed_at DESC)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_history_time 
            ON playback_history(performed_at DESC)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_history_user 
            ON playback_history(user_name, performed_at DESC)
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

