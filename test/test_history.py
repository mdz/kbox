"""Tests for history management."""

import os
import tempfile

import pytest

from kbox.database import Database
from kbox.history import HistoryManager
from kbox.models import SongMetadata, SongSettings
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
def history_manager(database):
    """Create a HistoryManager with a test database."""
    return HistoryManager(database)


@pytest.fixture
def test_users(user_manager):
    """Create test users and return their IDs and names."""
    alice = user_manager.get_or_create_user("alice-id", "Alice")
    bob = user_manager.get_or_create_user("bob-id", "Bob")
    return {
        "alice": {"id": alice.id, "name": alice.display_name},
        "bob": {"id": bob.id, "name": bob.display_name},
    }


def test_record_performance(history_manager, test_users):
    """Test recording a performance."""
    user = test_users["alice"]
    metadata = SongMetadata(
        title="Test Song", duration_seconds=180, thumbnail_url="http://example.com/thumb.jpg"
    )
    settings = SongSettings(pitch_semitones=-2)

    history_id = history_manager.record_performance(
        user_id=user["id"],
        user_name=user["name"],
        video_id="youtube:vid1",
        metadata=metadata,
        settings=settings,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3,
    )

    assert history_id > 0


def test_get_last_settings(history_manager, test_users):
    """Test getting last settings from history."""
    user = test_users["alice"]
    metadata = SongMetadata(title="Test Song", duration_seconds=180)
    settings = SongSettings(pitch_semitones=-2)

    history_manager.record_performance(
        user_id=user["id"],
        user_name=user["name"],
        video_id="youtube:vid1",
        metadata=metadata,
        settings=settings,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3,
    )

    # Get settings back
    retrieved_settings = history_manager.get_last_settings("youtube:vid1", user["id"])
    assert retrieved_settings is not None
    assert retrieved_settings.pitch_semitones == -2


def test_get_last_settings_no_history(history_manager, test_users):
    """Test getting settings when no history exists."""
    user = test_users["alice"]
    settings = history_manager.get_last_settings("youtube:nonexistent", user["id"])
    assert settings is None


def test_get_last_settings_different_users(history_manager, test_users):
    """Test that settings are user-specific."""
    alice = test_users["alice"]
    bob = test_users["bob"]

    # Alice sings with pitch -2
    history_manager.record_performance(
        user_id=alice["id"],
        user_name=alice["name"],
        video_id="youtube:vid1",
        metadata=SongMetadata(title="Song", duration_seconds=180),
        settings=SongSettings(pitch_semitones=-2),
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3,
    )

    # Bob sings same song with pitch +3
    history_manager.record_performance(
        user_id=bob["id"],
        user_name=bob["name"],
        video_id="youtube:vid1",
        metadata=SongMetadata(title="Song", duration_seconds=180),
        settings=SongSettings(pitch_semitones=3),
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3,
    )

    # Each user should get their own settings
    alice_settings = history_manager.get_last_settings("youtube:vid1", alice["id"])
    bob_settings = history_manager.get_last_settings("youtube:vid1", bob["id"])

    assert alice_settings is not None
    assert alice_settings.pitch_semitones == -2
    assert bob_settings is not None
    assert bob_settings.pitch_semitones == 3


def test_get_last_settings_most_recent(history_manager, test_users):
    """Test that get_last_settings returns most recent performance."""
    user = test_users["alice"]

    # Alice sings with pitch -2
    history_manager.record_performance(
        user_id=user["id"],
        user_name=user["name"],
        video_id="youtube:vid1",
        metadata=SongMetadata(title="Song", duration_seconds=180),
        settings=SongSettings(pitch_semitones=-2),
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3,
    )

    # Alice sings again with pitch +1
    history_manager.record_performance(
        user_id=user["id"],
        user_name=user["name"],
        video_id="youtube:vid1",
        metadata=SongMetadata(title="Song", duration_seconds=180),
        settings=SongSettings(pitch_semitones=1),
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3,
    )

    # Should get the most recent (+1)
    settings = history_manager.get_last_settings("youtube:vid1", user["id"])
    assert settings is not None
    assert settings.pitch_semitones == 1


def test_get_user_history(history_manager, test_users):
    """Test getting user's playback history."""
    alice = test_users["alice"]
    bob = test_users["bob"]

    # Add and record several songs for Alice
    history_manager.record_performance(
        user_id=alice["id"],
        user_name=alice["name"],
        video_id="youtube:vid1",
        metadata=SongMetadata(title="Song 1", duration_seconds=180),
        settings=SongSettings(pitch_semitones=-2),
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3,
    )

    history_manager.record_performance(
        user_id=alice["id"],
        user_name=alice["name"],
        video_id="youtube:vid2",
        metadata=SongMetadata(title="Song 2", duration_seconds=200),
        settings=SongSettings(pitch_semitones=0),
        played_duration_seconds=200,
        playback_end_position_seconds=200,
        completion_percentage=100.0,
    )

    # Add one for Bob
    history_manager.record_performance(
        user_id=bob["id"],
        user_name=bob["name"],
        video_id="youtube:vid3",
        metadata=SongMetadata(title="Song 3", duration_seconds=220),
        settings=SongSettings(pitch_semitones=3),
        played_duration_seconds=220,
        playback_end_position_seconds=220,
        completion_percentage=100.0,
    )

    # Get Alice's history
    alice_history = history_manager.get_user_history(alice["id"], limit=50)

    assert len(alice_history) == 2
    # Most recent first
    assert alice_history[0].metadata.title == "Song 2"
    assert alice_history[0].settings.pitch_semitones == 0
    assert alice_history[0].performance["completion_percentage"] == 100.0
    assert alice_history[1].metadata.title == "Song 1"
    assert alice_history[1].settings.pitch_semitones == -2
    assert alice_history[1].performance["completion_percentage"] == 83.3

    # Get Bob's history
    bob_history = history_manager.get_user_history(bob["id"], limit=50)
    assert len(bob_history) == 1
    assert bob_history[0].metadata.title == "Song 3"


def test_get_user_history_limit(history_manager, test_users):
    """Test that history respects the limit parameter."""
    user = test_users["alice"]

    # Add 5 songs for Alice
    for i in range(5):
        history_manager.record_performance(
            user_id=user["id"],
            user_name=user["name"],
            video_id=f"youtube:vid{i}",
            metadata=SongMetadata(title=f"Song {i}", duration_seconds=180),
            settings=SongSettings(pitch_semitones=0),
            played_duration_seconds=150,
            playback_end_position_seconds=150,
            completion_percentage=83.3,
        )

    # Request only 3
    history = history_manager.get_user_history(user["id"], limit=3)
    assert len(history) == 3
    # Should be most recent 3 (vid4, vid3, vid2)
    assert history[0].video_id == "youtube:vid4"
    assert history[1].video_id == "youtube:vid3"
    assert history[2].video_id == "youtube:vid2"


def test_get_user_history_empty(history_manager):
    """Test getting history for user with no history."""
    history = history_manager.get_user_history("nonexistent-user-id", limit=50)
    assert history == []
