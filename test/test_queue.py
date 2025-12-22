"""
Unit tests for QueueManager.
"""

import pytest
import sqlite3
import tempfile
import os
from kbox.database import Database
from kbox.queue import QueueManager
from kbox.user import UserManager


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    db = Database(db_path=path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def user_manager(temp_db):
    """Create a UserManager instance for testing."""
    return UserManager(temp_db)


@pytest.fixture
def queue_manager(temp_db):
    """Create a QueueManager instance for testing."""
    return QueueManager(temp_db)


# Test user IDs - used consistently across tests
ALICE_ID = 'alice-uuid-1234'
BOB_ID = 'bob-uuid-5678'
CHARLIE_ID = 'charlie-uuid-9012'


@pytest.fixture
def test_users(user_manager):
    """Create test users and return their IDs."""
    user_manager.get_or_create_user(ALICE_ID, 'Alice')
    user_manager.get_or_create_user(BOB_ID, 'Bob')
    user_manager.get_or_create_user(CHARLIE_ID, 'Charlie')
    return {'alice': ALICE_ID, 'bob': BOB_ID, 'charlie': CHARLIE_ID}


def test_add_song(queue_manager, test_users):
    """Test adding a song to the queue."""
    item_id = queue_manager.add_song(
        user_id=test_users['alice'],
        source='youtube',
        source_id='test123',
        title='Test Song',
        duration_seconds=180,
        thumbnail_url='http://example.com/thumb.jpg',
        pitch_semitones=2
    )
    
    assert item_id == 1
    
    queue = queue_manager.get_queue()
    assert len(queue) == 1
    assert queue[0]['user_id'] == test_users['alice']
    assert queue[0]['user_name'] == 'Alice'  # Display name from users table
    assert queue[0]['source'] == 'youtube'
    assert queue[0]['source_id'] == 'test123'
    assert queue[0]['title'] == 'Test Song'
    assert queue[0]['duration_seconds'] == 180
    assert queue[0]['pitch_semitones'] == 2
    assert queue[0]['download_status'] == QueueManager.STATUS_PENDING
    assert queue[0]['position'] == 1


def test_add_multiple_songs(queue_manager, test_users):
    """Test adding multiple songs maintains order."""
    queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song 1')
    queue_manager.add_song(test_users['bob'], 'youtube', 'vid2', 'Song 2')
    queue_manager.add_song(test_users['charlie'], 'youtube', 'vid3', 'Song 3')
    
    queue = queue_manager.get_queue()
    assert len(queue) == 3
    assert queue[0]['position'] == 1
    assert queue[1]['position'] == 2
    assert queue[2]['position'] == 3
    assert queue[0]['user_id'] == test_users['alice']
    assert queue[1]['user_id'] == test_users['bob']
    assert queue[2]['user_id'] == test_users['charlie']


def test_remove_song(queue_manager, test_users):
    """Test removing a song from the queue."""
    id1 = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song 1')
    id2 = queue_manager.add_song(test_users['bob'], 'youtube', 'vid2', 'Song 2')
    id3 = queue_manager.add_song(test_users['charlie'], 'youtube', 'vid3', 'Song 3')
    
    # Remove middle song
    result = queue_manager.remove_song(id2)
    assert result is True
    
    queue = queue_manager.get_queue()
    assert len(queue) == 2
    assert queue[0]['position'] == 1
    assert queue[1]['position'] == 2
    assert queue[0]['source_id'] == 'vid1'
    assert queue[1]['source_id'] == 'vid3'


def test_remove_nonexistent_song(queue_manager):
    """Test removing a non-existent song."""
    result = queue_manager.remove_song(999)
    assert result is False


def test_reorder_song(queue_manager, test_users):
    """Test reordering songs in the queue."""
    id1 = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song 1')
    id2 = queue_manager.add_song(test_users['bob'], 'youtube', 'vid2', 'Song 2')
    id3 = queue_manager.add_song(test_users['charlie'], 'youtube', 'vid3', 'Song 3')
    
    # Move last to first
    result = queue_manager.reorder_song(id3, 1)
    assert result is True
    
    queue = queue_manager.get_queue()
    assert queue[0]['source_id'] == 'vid3'
    assert queue[1]['source_id'] == 'vid1'
    assert queue[2]['source_id'] == 'vid2'
    assert queue[0]['position'] == 1
    assert queue[1]['position'] == 2
    assert queue[2]['position'] == 3


def test_reorder_invalid_position(queue_manager, test_users):
    """Test reordering with invalid position."""
    id1 = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song 1')
    
    # Try to move to position 0 (invalid)
    result = queue_manager.reorder_song(id1, 0)
    assert result is False
    
    # Try to move to position beyond queue length
    result = queue_manager.reorder_song(id1, 10)
    assert result is False


def test_get_next_song(queue_manager, test_users):
    """Test getting next ready song."""
    id1 = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song 1')
    id2 = queue_manager.add_song(test_users['bob'], 'youtube', 'vid2', 'Song 2')
    
    # No ready songs yet
    next_song = queue_manager.get_next_song()
    assert next_song is None
    
    # Mark first as ready
    queue_manager.update_download_status(id1, QueueManager.STATUS_READY, download_path='/path/to/vid1.mp4')
    
    next_song = queue_manager.get_next_song()
    assert next_song is not None
    assert next_song['id'] == id1
    assert next_song['download_status'] == QueueManager.STATUS_READY


def test_update_download_status(queue_manager, test_users):
    """Test updating download status."""
    item_id = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song 1')
    
    # Update to downloading
    result = queue_manager.update_download_status(item_id, QueueManager.STATUS_DOWNLOADING)
    assert result is True
    
    item = queue_manager.get_item(item_id)
    assert item['download_status'] == QueueManager.STATUS_DOWNLOADING
    
    # Update to ready with path
    result = queue_manager.update_download_status(
        item_id,
        QueueManager.STATUS_READY,
        download_path='/path/to/video.mp4'
    )
    assert result is True
    
    item = queue_manager.get_item(item_id)
    assert item['download_status'] == QueueManager.STATUS_READY
    assert item['download_path'] == '/path/to/video.mp4'


def test_update_download_status_error(queue_manager, test_users):
    """Test updating download status with error."""
    item_id = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song 1')
    
    result = queue_manager.update_download_status(
        item_id,
        QueueManager.STATUS_ERROR,
        error_message='Download failed'
    )
    assert result is True
    
    item = queue_manager.get_item(item_id)
    assert item['download_status'] == QueueManager.STATUS_ERROR
    assert item['error_message'] == 'Download failed'


def test_mark_played(queue_manager, test_users):
    """Test marking a song as played."""
    item_id = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song 1')
    
    result = queue_manager.mark_played(item_id)
    assert result is True
    
    item = queue_manager.get_item(item_id)
    assert item['played_at'] is not None


def test_update_pitch(queue_manager, test_users):
    """Test updating pitch for a queue item."""
    item_id = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song 1', pitch_semitones=0)
    
    result = queue_manager.update_pitch(item_id, 3)
    assert result is True
    
    item = queue_manager.get_item(item_id)
    assert item['pitch_semitones'] == 3


def test_clear_queue(queue_manager, test_users):
    """Test clearing the entire queue."""
    queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song 1')
    queue_manager.add_song(test_users['bob'], 'youtube', 'vid2', 'Song 2')
    queue_manager.add_song(test_users['charlie'], 'youtube', 'vid3', 'Song 3')
    
    count = queue_manager.clear_queue()
    assert count == 3
    
    queue = queue_manager.get_queue()
    assert len(queue) == 0


def test_queue_persistence(temp_db, user_manager):
    """Test that queue persists across QueueManager instances."""
    user_manager.get_or_create_user(ALICE_ID, 'Alice')
    
    qm1 = QueueManager(temp_db)
    item_id = qm1.add_song(ALICE_ID, 'youtube', 'vid1', 'Song 1')
    qm1.update_download_status(item_id, QueueManager.STATUS_READY, download_path='/path/to/video.mp4')
    
    # Create new QueueManager with same database
    qm2 = QueueManager(temp_db)
    queue = qm2.get_queue()
    assert len(queue) == 1
    assert queue[0]['user_id'] == ALICE_ID
    assert queue[0]['user_name'] == 'Alice'
    assert queue[0]['download_status'] == QueueManager.STATUS_READY


def test_record_history(queue_manager, test_users):
    """Test recording playback history."""
    # Add a song
    item_id = queue_manager.add_song(
        test_users['alice'], 'youtube', 'vid1', 'Test Song',
        duration_seconds=180, pitch_semitones=2
    )
    
    # Record history
    history_id = queue_manager.record_history(
        queue_item_id=item_id,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3
    )
    
    assert history_id > 0


def test_record_history_nonexistent_item(queue_manager):
    """Test recording history for nonexistent item."""
    history_id = queue_manager.record_history(
        queue_item_id=999,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3
    )
    
    # Should return 0 on error
    assert history_id == 0


def test_get_last_settings(queue_manager, test_users):
    """Test getting last settings from history."""
    # Add and record a song with specific pitch
    item_id = queue_manager.add_song(
        test_users['alice'], 'youtube', 'vid1', 'Test Song',
        pitch_semitones=-2
    )
    
    queue_manager.record_history(
        queue_item_id=item_id,
        played_duration_seconds=150,
        playback_end_position_seconds=150,
        completion_percentage=83.3
    )
    
    # Get settings back (using user_id now)
    settings = queue_manager.get_last_settings('youtube', 'vid1', test_users['alice'])
    assert settings == {'pitch_semitones': -2}


def test_get_last_settings_no_history(queue_manager, test_users):
    """Test getting settings when no history exists."""
    settings = queue_manager.get_last_settings('youtube', 'nonexistent', test_users['alice'])
    assert settings == {}


def test_get_last_settings_different_users(queue_manager, test_users):
    """Test that settings are user-specific."""
    # Alice sings with pitch -2
    item1 = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song', pitch_semitones=-2)
    queue_manager.record_history(item1, 150, 150, 83.3)
    
    # Bob sings same song with pitch +3
    item2 = queue_manager.add_song(test_users['bob'], 'youtube', 'vid1', 'Song', pitch_semitones=3)
    queue_manager.record_history(item2, 150, 150, 83.3)
    
    # Each user should get their own settings (queried by user_id)
    alice_settings = queue_manager.get_last_settings('youtube', 'vid1', test_users['alice'])
    bob_settings = queue_manager.get_last_settings('youtube', 'vid1', test_users['bob'])
    
    assert alice_settings == {'pitch_semitones': -2}
    assert bob_settings == {'pitch_semitones': 3}


def test_get_last_settings_most_recent(queue_manager, test_users):
    """Test that get_last_settings returns most recent performance."""
    # Alice sings with pitch -2
    item1 = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song', pitch_semitones=-2)
    queue_manager.record_history(item1, 150, 150, 83.3)
    
    # Alice sings again with pitch +1
    item2 = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song', pitch_semitones=1)
    queue_manager.record_history(item2, 150, 150, 83.3)
    
    # Should get the most recent (+1)
    settings = queue_manager.get_last_settings('youtube', 'vid1', test_users['alice'])
    assert settings == {'pitch_semitones': 1}


def test_get_user_history(queue_manager, test_users):
    """Test getting user's playback history."""
    # Add and record several songs for Alice
    item1 = queue_manager.add_song(test_users['alice'], 'youtube', 'vid1', 'Song 1', duration_seconds=180, pitch_semitones=-2)
    queue_manager.record_history(item1, 150, 150, 83.3)
    
    item2 = queue_manager.add_song(test_users['alice'], 'youtube', 'vid2', 'Song 2', duration_seconds=200, pitch_semitones=0)
    queue_manager.record_history(item2, 200, 200, 100.0)
    
    # Add one for Bob
    item3 = queue_manager.add_song(test_users['bob'], 'youtube', 'vid3', 'Song 3', duration_seconds=220, pitch_semitones=3)
    queue_manager.record_history(item3, 220, 220, 100.0)
    
    # Get Alice's history (using user_id)
    alice_history = queue_manager.get_user_history(test_users['alice'], limit=50)
    
    assert len(alice_history) == 2
    # Most recent first
    assert alice_history[0]['title'] == 'Song 2'
    assert alice_history[0]['pitch_semitones'] == 0
    assert alice_history[0]['completion_percentage'] == 100.0
    assert alice_history[1]['title'] == 'Song 1'
    assert alice_history[1]['pitch_semitones'] == -2
    assert alice_history[1]['completion_percentage'] == 83.3
    
    # Get Bob's history (using user_id)
    bob_history = queue_manager.get_user_history(test_users['bob'], limit=50)
    assert len(bob_history) == 1
    assert bob_history[0]['title'] == 'Song 3'


def test_get_user_history_limit(queue_manager, test_users):
    """Test that history respects the limit parameter."""
    # Add 5 songs for Alice
    for i in range(5):
        item = queue_manager.add_song(test_users['alice'], 'youtube', f'vid{i}', f'Song {i}', duration_seconds=180)
        queue_manager.record_history(item, 150, 150, 83.3)
    
    # Request only 3
    history = queue_manager.get_user_history(test_users['alice'], limit=3)
    assert len(history) == 3
    # Should be most recent 3 (vid4, vid3, vid2)
    assert history[0]['source_id'] == 'vid4'
    assert history[1]['source_id'] == 'vid3'
    assert history[2]['source_id'] == 'vid2'


def test_get_user_history_empty(queue_manager):
    """Test getting history for user with no history."""
    history = queue_manager.get_user_history('NonExistentUser', limit=50)
    assert history == []


