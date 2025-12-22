"""
User management for kbox.

Handles user identity with UUID-based identification.
"""

import logging
from typing import Optional, Dict, Any

from .database import Database


class UserManager:
    """Manages user identity and display names."""
    
    def __init__(self, database: Database):
        """
        Initialize UserManager.
        
        Args:
            database: Database instance for persistence
        """
        self.database = database
        self.logger = logging.getLogger(__name__)
    
    def get_or_create_user(self, user_id: str, display_name: str) -> Dict[str, Any]:
        """
        Get or create a user by ID.
        
        If the user exists, updates their display_name if it has changed.
        If not, creates a new user record.
        
        Args:
            user_id: UUID of the user
            display_name: Display name for the user
            
        Returns:
            User dict with id, display_name, created_at
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            # Check if user exists
            cursor.execute(
                'SELECT id, display_name, created_at FROM users WHERE id = ?',
                (user_id,)
            )
            row = cursor.fetchone()
            
            if row:
                # User exists - update display_name if changed
                if row['display_name'] != display_name:
                    cursor.execute(
                        'UPDATE users SET display_name = ? WHERE id = ?',
                        (display_name, user_id)
                    )
                    conn.commit()
                    self.logger.info('Updated display name for user %s: %s -> %s',
                                   user_id, row['display_name'], display_name)
                
                return {
                    'id': row['id'],
                    'display_name': display_name,  # Return the new name
                    'created_at': row['created_at']
                }
            else:
                # Create new user
                cursor.execute(
                    'INSERT INTO users (id, display_name) VALUES (?, ?)',
                    (user_id, display_name)
                )
                conn.commit()
                
                # Fetch the created record to get created_at
                cursor.execute(
                    'SELECT id, display_name, created_at FROM users WHERE id = ?',
                    (user_id,)
                )
                row = cursor.fetchone()
                
                self.logger.info('Created new user: %s (%s)', display_name, user_id)
                
                return {
                    'id': row['id'],
                    'display_name': row['display_name'],
                    'created_at': row['created_at']
                }
        finally:
            conn.close()
    
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a user by ID.
        
        Args:
            user_id: UUID of the user
            
        Returns:
            User dict with id, display_name, created_at, or None if not found
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute(
                'SELECT id, display_name, created_at FROM users WHERE id = ?',
                (user_id,)
            )
            row = cursor.fetchone()
            
            if row:
                return {
                    'id': row['id'],
                    'display_name': row['display_name'],
                    'created_at': row['created_at']
                }
            return None
        finally:
            conn.close()

