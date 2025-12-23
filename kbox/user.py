"""
User management for kbox.

Handles user identity with UUID-based identification.
"""

import logging
from typing import Optional

from .database import Database, UserRepository
from .models import User


class UserManager:
    """Manages user identity and display names."""
    
    def __init__(self, database: Database):
        """
        Initialize UserManager.
        
        Args:
            database: Database instance for persistence
        """
        self.database = database
        self.repository = UserRepository(database)
        self.logger = logging.getLogger(__name__)
    
    def get_or_create_user(self, user_id: str, display_name: str) -> User:
        """
        Get or create a user by ID.
        
        If the user exists, updates their display_name if it has changed.
        If not, creates a new user record.
        
        Args:
            user_id: UUID of the user
            display_name: Display name for the user
            
        Returns:
            User object
        """
        user = self.repository.get_by_id(user_id)
        
        if user:
            # User exists - update display_name if changed
            if user.display_name != display_name:
                self.repository.update_display_name(user_id, display_name)
                user = self.repository.get_by_id(user_id)  # Refresh to get updated name
            return user
        else:
            # Create new user
            return self.repository.create(user_id, display_name)
    
    def get_user(self, user_id: str) -> Optional[User]:
        """
        Get a user by ID.
        
        Args:
            user_id: UUID of the user
            
        Returns:
            User object, or None if not found
        """
        return self.repository.get_by_id(user_id)

