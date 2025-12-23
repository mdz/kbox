"""
Unit tests for UserManager.
"""

import pytest
import tempfile
import os

from kbox.database import Database
from kbox.user import UserManager


@pytest.fixture
def temp_db():
    """Create a temporary database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path=path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def user_manager(temp_db):
    """Create a UserManager instance."""
    return UserManager(temp_db)


class TestUserManager:
    """Tests for UserManager."""

    def test_create_new_user(self, user_manager):
        """get_or_create_user creates a new user when not exists."""
        user = user_manager.get_or_create_user("user-123", "Alice")
        
        assert user.id == "user-123"
        assert user.display_name == "Alice"
        assert user.created_at is not None

    def test_get_existing_user(self, user_manager):
        """get_or_create_user returns existing user."""
        user1 = user_manager.get_or_create_user("user-123", "Alice")
        user2 = user_manager.get_or_create_user("user-123", "Alice")
        
        assert user1.id == user2.id
        assert user1.display_name == user2.display_name

    def test_update_display_name(self, user_manager):
        """get_or_create_user updates display_name when it changes."""
        user1 = user_manager.get_or_create_user("user-123", "Alice")
        assert user1.display_name == "Alice"
        
        user2 = user_manager.get_or_create_user("user-123", "Alice Smith")
        assert user2.display_name == "Alice Smith"
        assert user2.id == user1.id

    def test_get_user_exists(self, user_manager):
        """get_user returns existing user."""
        user_manager.get_or_create_user("user-123", "Alice")
        
        user = user_manager.get_user("user-123")
        assert user is not None
        assert user.id == "user-123"
        assert user.display_name == "Alice"

    def test_get_user_not_exists(self, user_manager):
        """get_user returns None for non-existent user."""
        user = user_manager.get_user("nonexistent-user")
        assert user is None

    def test_multiple_users(self, user_manager):
        """Multiple users can be created and retrieved."""
        alice = user_manager.get_or_create_user("alice-id", "Alice")
        bob = user_manager.get_or_create_user("bob-id", "Bob")
        charlie = user_manager.get_or_create_user("charlie-id", "Charlie")
        
        assert alice.display_name == "Alice"
        assert bob.display_name == "Bob"
        assert charlie.display_name == "Charlie"
        
        # Verify all can be retrieved
        assert user_manager.get_user("alice-id").display_name == "Alice"
        assert user_manager.get_user("bob-id").display_name == "Bob"
        assert user_manager.get_user("charlie-id").display_name == "Charlie"

    def test_user_persistence(self, temp_db):
        """Users persist across UserManager instances."""
        # Create user with first manager
        manager1 = UserManager(temp_db)
        manager1.get_or_create_user("user-123", "Alice")
        
        # Retrieve with new manager instance
        manager2 = UserManager(temp_db)
        user = manager2.get_user("user-123")
        
        assert user is not None
        assert user.display_name == "Alice"

