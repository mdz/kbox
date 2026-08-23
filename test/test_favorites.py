"""Tests for favorites (starred songs) management."""

import os
import tempfile

import pytest

from kbox.database import Database
from kbox.favorites import FavoritesManager
from kbox.models import SongMetadata
from kbox.user import UserManager


@pytest.fixture
def database():
    """Create a test database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def user_manager(database):
    """Create a UserManager with a test database."""
    return UserManager(database)


@pytest.fixture
def favorites_manager(database):
    """Create a FavoritesManager with a test database."""
    return FavoritesManager(database)


@pytest.fixture
def test_users(user_manager):
    """Create test users and return their IDs and names."""
    alice = user_manager.get_or_create_user("alice-id", "Alice")
    bob = user_manager.get_or_create_user("bob-id", "Bob")
    return {
        "alice": {"id": alice.id, "name": alice.display_name},
        "bob": {"id": bob.id, "name": bob.display_name},
    }


def test_add_favorite(favorites_manager, test_users):
    """Starring a song makes it show up in the user's favorites."""
    user = test_users["alice"]
    metadata = SongMetadata(
        title="Test Song", duration_seconds=180, thumbnail_url="http://example.com/thumb.jpg"
    )

    favorites_manager.add_favorite(user["id"], "youtube:vid1", metadata)

    favorites = favorites_manager.get_user_favorites(user["id"])
    assert len(favorites) == 1
    assert favorites[0].video_id == "youtube:vid1"
    assert favorites[0].metadata.title == "Test Song"
    assert favorites[0].metadata.duration_seconds == 180
    assert favorites[0].created_at is not None


def test_add_favorite_is_idempotent(favorites_manager, test_users):
    """Starring the same song twice doesn't create a duplicate entry."""
    user = test_users["alice"]
    favorites_manager.add_favorite(user["id"], "youtube:vid1", SongMetadata(title="Original"))
    favorites_manager.add_favorite(user["id"], "youtube:vid1", SongMetadata(title="Original"))

    favorites = favorites_manager.get_user_favorites(user["id"])
    assert len(favorites) == 1


def test_add_favorite_refreshes_metadata(favorites_manager, test_users):
    """Re-favoriting a song refreshes its stored metadata."""
    user = test_users["alice"]
    favorites_manager.add_favorite(user["id"], "youtube:vid1", SongMetadata(title="Old Title"))
    favorites_manager.add_favorite(user["id"], "youtube:vid1", SongMetadata(title="New Title"))

    favorites = favorites_manager.get_user_favorites(user["id"])
    assert len(favorites) == 1
    assert favorites[0].metadata.title == "New Title"


def test_remove_favorite(favorites_manager, test_users):
    """Unstarring a song removes it from favorites."""
    user = test_users["alice"]
    favorites_manager.add_favorite(user["id"], "youtube:vid1", SongMetadata(title="Test Song"))

    removed = favorites_manager.remove_favorite(user["id"], "youtube:vid1")
    assert removed is True
    assert favorites_manager.get_user_favorites(user["id"]) == []


def test_remove_favorite_not_favorited(favorites_manager, test_users):
    """Unstarring a song that was never favorited returns False."""
    user = test_users["alice"]
    removed = favorites_manager.remove_favorite(user["id"], "youtube:nonexistent")
    assert removed is False


def test_favorites_are_per_user(favorites_manager, test_users):
    """Favorites are private and scoped per user."""
    alice = test_users["alice"]
    bob = test_users["bob"]

    favorites_manager.add_favorite(alice["id"], "youtube:vid1", SongMetadata(title="Alice's Song"))
    favorites_manager.add_favorite(bob["id"], "youtube:vid2", SongMetadata(title="Bob's Song"))

    alice_favorites = favorites_manager.get_user_favorites(alice["id"])
    bob_favorites = favorites_manager.get_user_favorites(bob["id"])

    assert len(alice_favorites) == 1
    assert alice_favorites[0].video_id == "youtube:vid1"
    assert len(bob_favorites) == 1
    assert bob_favorites[0].video_id == "youtube:vid2"


def test_remove_favorite_does_not_affect_other_users(favorites_manager, test_users):
    """Removing one user's favorite doesn't touch another user's favorite for the same song."""
    alice = test_users["alice"]
    bob = test_users["bob"]

    favorites_manager.add_favorite(alice["id"], "youtube:vid1", SongMetadata(title="Song"))
    favorites_manager.add_favorite(bob["id"], "youtube:vid1", SongMetadata(title="Song"))

    favorites_manager.remove_favorite(alice["id"], "youtube:vid1")

    assert favorites_manager.get_user_favorites(alice["id"]) == []
    assert len(favorites_manager.get_user_favorites(bob["id"])) == 1


def test_get_user_favorites_newest_first(favorites_manager, test_users):
    """Favorites are returned newest first."""
    user = test_users["alice"]
    favorites_manager.add_favorite(user["id"], "youtube:vid1", SongMetadata(title="First"))
    favorites_manager.add_favorite(user["id"], "youtube:vid2", SongMetadata(title="Second"))
    favorites_manager.add_favorite(user["id"], "youtube:vid3", SongMetadata(title="Third"))

    favorites = favorites_manager.get_user_favorites(user["id"])
    assert [fav.video_id for fav in favorites] == ["youtube:vid3", "youtube:vid2", "youtube:vid1"]


def test_get_user_favorites_empty(favorites_manager):
    """A user with no favorites gets an empty list."""
    assert favorites_manager.get_user_favorites("nonexistent-user-id") == []
