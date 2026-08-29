"""
User management for kbox.

Handles user identity with UUID-based identification.
"""

import logging
from typing import List, Optional

from .database import Database, UserRepository
from .identity import normalize_name
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

    def lookup_candidates(self, name: str) -> List[User]:
        """
        Find existing identities whose name matches a typed name.

        Normalizes `name` the same way stored identities are, so "Matt",
        "matt", and "Matt " all resolve to the same candidates. An empty
        result means this is a genuinely new name — the caller should let
        the guest through with no recognition step, per
        ldocs/GUEST_IDENTITY_CONTINUITY.md.

        Args:
            name: Raw name as typed by the guest

        Returns:
            Matching User objects, most recently seen first. Never raises on
            an unmatched name — just returns an empty list.
        """
        return self.repository.find_by_normalized_name(normalize_name(name))

    def touch_last_seen(self, user_id: str) -> None:
        """
        Record that a session just (re)bound to this user.

        Args:
            user_id: UUID of the user
        """
        self.repository.touch_last_seen(user_id)

    def merge_users(self, keep_id: str, merge_id: str) -> None:
        """
        Fold merge_id's history into keep_id and delete merge_id.

        For an operator confirming two identities belong to the same real
        person — never done automatically. See
        ldocs/GUEST_IDENTITY_CONTINUITY.md.

        Args:
            keep_id: UUID of the identity to keep
            merge_id: UUID of the identity to fold in and remove

        Raises:
            ValueError: if keep_id == merge_id, or either doesn't exist
        """
        self.repository.merge_users(keep_id, merge_id)
