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
        # Root logger stays at INFO (DEBUG globally is too spammy from
        # third-party libs like httpx/litellm); opt this logger in on its
        # own so the new-identity debug logging above is actually emitted.
        self.logger.setLevel(logging.DEBUG)

    def get_or_create_user(
        self,
        user_id: str,
        display_name: str,
        user_agent: Optional[str] = None,
        session_already_bound: Optional[bool] = None,
    ) -> User:
        """
        Get or create a user by ID.

        If the user exists, updates their display_name if it has changed.
        If not, creates a new user record.

        Args:
            user_id: UUID of the user
            display_name: Display name for the user
            user_agent: User-Agent header of the request, for debug logging only
            session_already_bound: whether the request's session already had a
                user_id bound before this call, for debug logging only

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
            # Create new user. Log enough to tell apart the two root causes of
            # identity churn: the client never had a UUID to send (real
            # client-side storage loss) vs. the client sent a UUID that we
            # simply didn't recognize (a lookup/session bug).
            self.logger.debug(
                "[DEBUG] Creating new user identity: user_id=%r client_sent_uuid=%s "
                "session_already_bound=%s display_name=%r user_agent=%r",
                user_id,
                bool(user_id),
                session_already_bound,
                display_name,
                user_agent,
            )
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
