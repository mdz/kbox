"""Tests for history management."""

import pytest
import tempfile
import os
from kbox.database import Database
from kbox.history import HistoryManager


@pytest.fixture
def database():
    """Create a test database."""
    # Use a temporary file instead of :memory: to avoid connection issues
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    db = Database(path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def history_manager(database):
    """Create a HistoryManager with a test database."""
    return HistoryManager(database)


def test_record_performance(history_manager):
    """Test recording a performance."""
    queue_item = {
        'user_id': 'alice-id',
        'user_name': 'Alice',
        'source': 'youtube',
        'source_id': 'vid1',
        'title': 'Test Song',
        'duration_seconds': 180,
        'thumbnail_url': 'http://example.com/thumb.jpg',
        'pitch_semitones': -2
    }
    
    history_id = history_manager.record_performance(
        queue_item=queue_item,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3
    )
    
    assert history_id > 0


def test_get_last_settings(history_manager):
    """Test getting last settings from history."""
    queue_item = {
        'user_id': 'alice-id',
        'user_name': 'Alice',
        'source': 'youtube',
        'source_id': 'vid1',
        'title': 'Test Song',
        'duration_seconds': 180,
        'pitch_semitones': -2
    }
    
    history_manager.record_performance(
        queue_item=queue_item,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3
    )
    
    # Get settings back
    retrieved_settings = history_manager.get_last_settings('youtube', 'vid1', 'alice-id')
    assert retrieved_settings == {'pitch_semitones': -2}


def test_get_last_settings_no_history(history_manager):
    """Test getting settings when no history exists."""
    settings = history_manager.get_last_settings('youtube', 'nonexistent', 'alice-id')
    assert settings == {}


def test_get_last_settings_different_users(history_manager):
    """Test that settings are user-specific."""
    # Alice sings with pitch -2
    alice_item = {
        'user_id': 'alice-id',
        'user_name': 'Alice',
        'source': 'youtube',
        'source_id': 'vid1',
        'title': 'Song',
        'duration_seconds': 180,
        'pitch_semitones': -2
    }
    history_manager.record_performance(
        queue_item=alice_item,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3
    )
    
    # Bob sings same song with pitch +3
    bob_item = {
        'user_id': 'bob-id',
        'user_name': 'Bob',
        'source': 'youtube',
        'source_id': 'vid1',
        'title': 'Song',
        'duration_seconds': 180,
        'pitch_semitones': 3
    }
    history_manager.record_performance(
        queue_item=bob_item,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3
    )
    
    # Each user should get their own settings
    alice_settings = history_manager.get_last_settings('youtube', 'vid1', 'alice-id')
    bob_settings = history_manager.get_last_settings('youtube', 'vid1', 'bob-id')
    
    assert alice_settings == {'pitch_semitones': -2}
    assert bob_settings == {'pitch_semitones': 3}


def test_get_last_settings_most_recent(history_manager):
    """Test that get_last_settings returns most recent performance."""
    # Alice sings with pitch -2
    item1 = {
        'user_id': 'alice-id',
        'user_name': 'Alice',
        'source': 'youtube',
        'source_id': 'vid1',
        'title': 'Song',
        'duration_seconds': 180,
        'pitch_semitones': -2
    }
    history_manager.record_performance(
        queue_item=item1,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3
    )
    
    # Alice sings again with pitch +1
    item2 = {
        'user_id': 'alice-id',
        'user_name': 'Alice',
        'source': 'youtube',
        'source_id': 'vid1',
        'title': 'Song',
        'duration_seconds': 180,
        'pitch_semitones': 1
    }
    history_manager.record_performance(
        queue_item=item2,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3
    )
    
    # Should get the most recent (+1)
    settings = history_manager.get_last_settings('youtube', 'vid1', 'alice-id')
    assert settings == {'pitch_semitones': 1}


def test_get_user_history(history_manager):
    """Test getting user's playback history."""
    # Add and record several songs for Alice
    item1 = {
        'user_id': 'alice-id',
        'user_name': 'Alice',
        'source': 'youtube',
        'source_id': 'vid1',
        'title': 'Song 1',
        'duration_seconds': 180,
        'pitch_semitones': -2
    }
    history_manager.record_performance(
        queue_item=item1,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3
    )
    
    item2 = {
        'user_id': 'alice-id',
        'user_name': 'Alice',
        'source': 'youtube',
        'source_id': 'vid2',
        'title': 'Song 2',
        'duration_seconds': 200,
        'pitch_semitones': 0
    }
    history_manager.record_performance(
        queue_item=item2,
        played_duration_seconds=200,
        playback_end_position_seconds=200,
        completion_percentage=100.0
    )
    
    # Add one for Bob
    item3 = {
        'user_id': 'bob-id',
        'user_name': 'Bob',
        'source': 'youtube',
        'source_id': 'vid3',
        'title': 'Song 3',
        'duration_seconds': 220,
        'pitch_semitones': 3
    }
    history_manager.record_performance(
        queue_item=item3,
        played_duration_seconds=220,
        playback_end_position_seconds=220,
        completion_percentage=100.0
    )
    
    # Get Alice's history
    alice_history = history_manager.get_user_history('alice-id', limit=50)
    
    assert len(alice_history) == 2
    # Most recent first
    assert alice_history[0]['title'] == 'Song 2'
    assert alice_history[0]['pitch_semitones'] == 0
    assert alice_history[0]['completion_percentage'] == 100.0
    assert alice_history[1]['title'] == 'Song 1'
    assert alice_history[1]['pitch_semitones'] == -2
    assert alice_history[1]['completion_percentage'] == 83.3
    
    # Get Bob's history
    bob_history = history_manager.get_user_history('bob-id', limit=50)
    assert len(bob_history) == 1
    assert bob_history[0]['title'] == 'Song 3'


def test_get_user_history_limit(history_manager):
    """Test that history respects the limit parameter."""
    # Add 5 songs for Alice
    for i in range(5):
        queue_item = {
            'user_id': 'alice-id',
            'user_name': 'Alice',
            'source': 'youtube',
            'source_id': f'vid{i}',
            'title': f'Song {i}',
            'duration_seconds': 180,
            'pitch_semitones': 0
        }
        history_manager.record_performance(
            queue_item=queue_item,
            played_duration_seconds=150,
            playback_end_position_seconds=150,
            completion_percentage=83.3
        )
    
    # Request only 3
    history = history_manager.get_user_history('alice-id', limit=3)
    assert len(history) == 3
    # Should be most recent 3 (vid4, vid3, vid2)
    assert history[0]['source_id'] == 'vid4'
    assert history[1]['source_id'] == 'vid3'
    assert history[2]['source_id'] == 'vid2'


def test_get_user_history_empty(history_manager):
    """Test getting history for user with no history."""
    history = history_manager.get_user_history('nonexistent-user-id', limit=50)
    assert history == []
