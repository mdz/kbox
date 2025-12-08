"""
Unit tests for QueueManager.
"""

import pytest
import sqlite3
import tempfile
import os
from kbox.database import Database
from kbox.queue import QueueManager


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
def queue_manager(temp_db):
    """Create a QueueManager instance for testing."""
    return QueueManager(temp_db)


def test_add_song(queue_manager):
    """Test adding a song to the queue."""
    item_id = queue_manager.add_song(
        user_name='Alice',
        youtube_video_id='test123',
        title='Test Song',
        duration_seconds=180,
        thumbnail_url='http://example.com/thumb.jpg',
        pitch_semitones=2
    )
    
    assert item_id == 1
    
    queue = queue_manager.get_queue()
    assert len(queue) == 1
    assert queue[0]['user_name'] == 'Alice'
    assert queue[0]['youtube_video_id'] == 'test123'
    assert queue[0]['title'] == 'Test Song'
    assert queue[0]['duration_seconds'] == 180
    assert queue[0]['pitch_semitones'] == 2
    assert queue[0]['download_status'] == QueueManager.STATUS_PENDING
    assert queue[0]['position'] == 1


def test_add_multiple_songs(queue_manager):
    """Test adding multiple songs maintains order."""
    queue_manager.add_song('Alice', 'vid1', 'Song 1')
    queue_manager.add_song('Bob', 'vid2', 'Song 2')
    queue_manager.add_song('Charlie', 'vid3', 'Song 3')
    
    queue = queue_manager.get_queue()
    assert len(queue) == 3
    assert queue[0]['position'] == 1
    assert queue[1]['position'] == 2
    assert queue[2]['position'] == 3
    assert queue[0]['user_name'] == 'Alice'
    assert queue[1]['user_name'] == 'Bob'
    assert queue[2]['user_name'] == 'Charlie'


def test_remove_song(queue_manager):
    """Test removing a song from the queue."""
    id1 = queue_manager.add_song('Alice', 'vid1', 'Song 1')
    id2 = queue_manager.add_song('Bob', 'vid2', 'Song 2')
    id3 = queue_manager.add_song('Charlie', 'vid3', 'Song 3')
    
    # Remove middle song
    result = queue_manager.remove_song(id2)
    assert result is True
    
    queue = queue_manager.get_queue()
    assert len(queue) == 2
    assert queue[0]['position'] == 1
    assert queue[1]['position'] == 2
    assert queue[0]['youtube_video_id'] == 'vid1'
    assert queue[1]['youtube_video_id'] == 'vid3'


def test_remove_nonexistent_song(queue_manager):
    """Test removing a non-existent song."""
    result = queue_manager.remove_song(999)
    assert result is False


def test_reorder_song(queue_manager):
    """Test reordering songs in the queue."""
    id1 = queue_manager.add_song('Alice', 'vid1', 'Song 1')
    id2 = queue_manager.add_song('Bob', 'vid2', 'Song 2')
    id3 = queue_manager.add_song('Charlie', 'vid3', 'Song 3')
    
    # Move last to first
    result = queue_manager.reorder_song(id3, 1)
    assert result is True
    
    queue = queue_manager.get_queue()
    assert queue[0]['youtube_video_id'] == 'vid3'
    assert queue[1]['youtube_video_id'] == 'vid1'
    assert queue[2]['youtube_video_id'] == 'vid2'
    assert queue[0]['position'] == 1
    assert queue[1]['position'] == 2
    assert queue[2]['position'] == 3


def test_reorder_invalid_position(queue_manager):
    """Test reordering with invalid position."""
    id1 = queue_manager.add_song('Alice', 'vid1', 'Song 1')
    
    # Try to move to position 0 (invalid)
    result = queue_manager.reorder_song(id1, 0)
    assert result is False
    
    # Try to move to position beyond queue length
    result = queue_manager.reorder_song(id1, 10)
    assert result is False


def test_get_next_song(queue_manager):
    """Test getting next ready song."""
    id1 = queue_manager.add_song('Alice', 'vid1', 'Song 1')
    id2 = queue_manager.add_song('Bob', 'vid2', 'Song 2')
    
    # No ready songs yet
    next_song = queue_manager.get_next_song()
    assert next_song is None
    
    # Mark first as ready
    queue_manager.update_download_status(id1, QueueManager.STATUS_READY, download_path='/path/to/vid1.mp4')
    
    next_song = queue_manager.get_next_song()
    assert next_song is not None
    assert next_song['id'] == id1
    assert next_song['download_status'] == QueueManager.STATUS_READY


def test_update_download_status(queue_manager):
    """Test updating download status."""
    item_id = queue_manager.add_song('Alice', 'vid1', 'Song 1')
    
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


def test_update_download_status_error(queue_manager):
    """Test updating download status with error."""
    item_id = queue_manager.add_song('Alice', 'vid1', 'Song 1')
    
    result = queue_manager.update_download_status(
        item_id,
        QueueManager.STATUS_ERROR,
        error_message='Download failed'
    )
    assert result is True
    
    item = queue_manager.get_item(item_id)
    assert item['download_status'] == QueueManager.STATUS_ERROR
    assert item['error_message'] == 'Download failed'


def test_mark_played(queue_manager):
    """Test marking a song as played."""
    item_id = queue_manager.add_song('Alice', 'vid1', 'Song 1')
    
    result = queue_manager.mark_played(item_id)
    assert result is True
    
    item = queue_manager.get_item(item_id)
    assert item['played_at'] is not None


def test_update_pitch(queue_manager):
    """Test updating pitch for a queue item."""
    item_id = queue_manager.add_song('Alice', 'vid1', 'Song 1', pitch_semitones=0)
    
    result = queue_manager.update_pitch(item_id, 3)
    assert result is True
    
    item = queue_manager.get_item(item_id)
    assert item['pitch_semitones'] == 3


def test_clear_queue(queue_manager):
    """Test clearing the entire queue."""
    queue_manager.add_song('Alice', 'vid1', 'Song 1')
    queue_manager.add_song('Bob', 'vid2', 'Song 2')
    queue_manager.add_song('Charlie', 'vid3', 'Song 3')
    
    count = queue_manager.clear_queue()
    assert count == 3
    
    queue = queue_manager.get_queue()
    assert len(queue) == 0


def test_queue_persistence(temp_db):
    """Test that queue persists across QueueManager instances."""
    qm1 = QueueManager(temp_db)
    item_id = qm1.add_song('Alice', 'vid1', 'Song 1')
    qm1.update_download_status(item_id, QueueManager.STATUS_READY, download_path='/path/to/video.mp4')
    
    # Create new QueueManager with same database
    qm2 = QueueManager(temp_db)
    queue = qm2.get_queue()
    assert len(queue) == 1
    assert queue[0]['user_name'] == 'Alice'
    assert queue[0]['download_status'] == QueueManager.STATUS_READY


