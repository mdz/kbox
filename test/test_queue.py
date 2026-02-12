"""
Unit tests for QueueManager.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kbox.database import Database
from kbox.queue import QueueManager
from kbox.user import UserManager


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
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
def mock_video_library():
    """Create a mock VideoLibrary for testing."""
    mock = MagicMock()
    mock.request.return_value = None  # Async download
    mock.get_path.return_value = None
    mock.is_available.return_value = False
    mock.manage_storage.return_value = 0
    return mock


@pytest.fixture
def queue_manager(temp_db, mock_video_library):
    """Create a QueueManager instance for testing."""
    qm = QueueManager(temp_db, video_library=mock_video_library)
    yield qm
    qm.stop_download_monitor()


# Test user IDs - used consistently across tests
ALICE_ID = "alice-uuid-1234"
BOB_ID = "bob-uuid-5678"
CHARLIE_ID = "charlie-uuid-9012"


@pytest.fixture
def test_users(user_manager):
    """Create test users and return User objects."""
    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
    bob = user_manager.get_or_create_user(BOB_ID, "Bob")
    charlie = user_manager.get_or_create_user(CHARLIE_ID, "Charlie")
    return {"alice": alice, "bob": bob, "charlie": charlie}


def test_add_song(queue_manager, test_users):
    """Test adding a song to the queue."""
    item_id = queue_manager.add_song(
        user=test_users["alice"],
        video_id="youtube:test123",
        title="Test Song",
        duration_seconds=180,
        thumbnail_url="http://example.com/thumb.jpg",
        pitch_semitones=2,
    )

    assert item_id == 1

    queue = queue_manager.get_queue()
    assert len(queue) == 1
    assert queue[0].user_id == ALICE_ID
    assert queue[0].user_name == "Alice"
    assert queue[0].video_id == "youtube:test123"
    assert queue[0].metadata.title == "Test Song"
    assert queue[0].metadata.duration_seconds == 180
    assert queue[0].settings.pitch_semitones == 2
    assert queue[0].download_status == QueueManager.STATUS_PENDING
    assert queue[0].position == 1


def test_add_multiple_songs(queue_manager, test_users):
    """Test adding multiple songs maintains order."""
    queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")
    queue_manager.add_song(test_users["charlie"], "youtube:vid3", "Song 3")

    queue = queue_manager.get_queue()
    assert len(queue) == 3
    assert queue[0].position == 1
    assert queue[1].position == 2
    assert queue[2].position == 3
    assert queue[0].user_id == ALICE_ID
    assert queue[1].user_id == BOB_ID
    assert queue[2].user_id == CHARLIE_ID


def test_add_duplicate_song_rejected(queue_manager, test_users):
    """Test that adding the same song twice is rejected."""
    from kbox.queue import DuplicateSongError

    # Add a song
    queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")

    # Try to add the same song again (same video_id)
    with pytest.raises(DuplicateSongError):
        queue_manager.add_song(test_users["bob"], "youtube:vid1", "Song 1 Again")

    # Queue should still have only one song
    queue = queue_manager.get_queue()
    assert len(queue) == 1


def test_add_same_song_after_cursor_passed_allowed(queue_manager, test_users):
    """Test that a song can be re-added after the cursor has passed it."""

    # Add a song
    item_id = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")

    # Move cursor past this song (simulates it being played)
    queue_manager.set_cursor(item_id)

    # Now adding the same song again should work (it's behind the cursor)
    item_id2 = queue_manager.add_song(test_users["bob"], "youtube:vid1", "Song 1 Again")
    assert item_id2 is not None

    # Queue should have two items
    queue = queue_manager.get_queue()
    assert len(queue) == 2


def test_remove_song(queue_manager, test_users):
    """Test removing a song from the queue."""
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    id2 = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")
    id3 = queue_manager.add_song(test_users["charlie"], "youtube:vid3", "Song 3")

    # Remove middle song
    result = queue_manager.remove_song(id2)
    assert result is True

    queue = queue_manager.get_queue()
    assert len(queue) == 2
    assert queue[0].position == 1
    assert queue[1].position == 2
    assert queue[0].video_id == "youtube:vid1"
    assert queue[1].video_id == "youtube:vid3"


def test_remove_nonexistent_song(queue_manager):
    """Test removing a non-existent song."""
    result = queue_manager.remove_song(999)
    assert result is False


def test_reorder_song(queue_manager, test_users):
    """Test reordering songs in the queue."""
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    id2 = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")
    id3 = queue_manager.add_song(test_users["charlie"], "youtube:vid3", "Song 3")

    # Move last to first
    result = queue_manager.reorder_song(id3, 1)
    assert result is True

    queue = queue_manager.get_queue()
    assert queue[0].video_id == "youtube:vid3"
    assert queue[1].video_id == "youtube:vid1"
    assert queue[2].video_id == "youtube:vid2"
    assert queue[0].position == 1
    assert queue[1].position == 2
    assert queue[2].position == 3


def test_reorder_invalid_position(queue_manager, test_users):
    """Test reordering with invalid position."""
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")

    # Try to move to position 0 (invalid)
    result = queue_manager.reorder_song(id1, 0)
    assert result is False

    # Try to move to position beyond queue length
    result = queue_manager.reorder_song(id1, 10)
    assert result is False


def test_get_ready_song_at_offset(queue_manager, test_users):
    """Test getting ready song at offset."""
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    id2 = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")
    id3 = queue_manager.add_song(test_users["alice"], "youtube:vid3", "Song 3")

    # No ready songs yet
    next_song = queue_manager.get_ready_song_at_offset(None, 0)
    assert next_song is None

    # Mark first and second as ready
    queue_manager.update_download_status(
        id1, QueueManager.STATUS_READY, download_path="/path/to/vid1.mp4"
    )
    queue_manager.update_download_status(
        id2, QueueManager.STATUS_READY, download_path="/path/to/vid2.mp4"
    )

    # Get first ready song
    first_song = queue_manager.get_ready_song_at_offset(None, 0)
    assert first_song is not None
    assert first_song.id == id1
    assert first_song.download_status == QueueManager.STATUS_READY

    # Get next song after first
    next_song = queue_manager.get_ready_song_at_offset(id1, +1)
    assert next_song is not None
    assert next_song.id == id2

    # Get previous song before second
    prev_song = queue_manager.get_ready_song_at_offset(id2, -1)
    assert prev_song is not None
    assert prev_song.id == id1

    # No next song after second (third is not ready)
    no_next = queue_manager.get_ready_song_at_offset(id2, +1)
    assert no_next is None

    # No previous song before first
    no_prev = queue_manager.get_ready_song_at_offset(id1, -1)
    assert no_prev is None

    # Reference song not in queue at all - falls back to first ready song
    nonexistent = queue_manager.get_ready_song_at_offset(9999, +1)
    assert nonexistent is not None
    assert nonexistent.id == id1

    # Reference song exists but is not ready (id3) - finds next by position
    # id3 is at position 3, and there are no ready songs after position 3
    not_ready_next = queue_manager.get_ready_song_at_offset(id3, +1)
    assert not_ready_next is None


def test_update_download_status(queue_manager, test_users):
    """Test updating download status."""
    item_id = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")

    # Update to downloading
    result = queue_manager.update_download_status(item_id, QueueManager.STATUS_DOWNLOADING)
    assert result is True

    item = queue_manager.get_item(item_id)
    assert item.download_status == QueueManager.STATUS_DOWNLOADING

    # Update to ready with path
    result = queue_manager.update_download_status(
        item_id, QueueManager.STATUS_READY, download_path="/path/to/video.mp4"
    )
    assert result is True

    item = queue_manager.get_item(item_id)
    assert item.download_status == QueueManager.STATUS_READY
    assert item.download_path == "/path/to/video.mp4"


def test_update_download_status_error(queue_manager, test_users):
    """Test updating download status with error."""
    item_id = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")

    result = queue_manager.update_download_status(
        item_id, QueueManager.STATUS_ERROR, error_message="Download failed"
    )
    assert result is True

    item = queue_manager.get_item(item_id)
    assert item.download_status == QueueManager.STATUS_ERROR
    assert item.error_message == "Download failed"


def test_cursor_set_and_get(queue_manager, test_users):
    """Test setting and getting the queue cursor."""
    item_id = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")

    # Initially no cursor
    assert queue_manager.get_cursor() is None
    assert queue_manager.get_cursor_position() is None

    # Set cursor
    queue_manager.set_cursor(item_id)
    assert queue_manager.get_cursor() == item_id
    assert queue_manager.get_cursor_position() == 1

    # Clear cursor
    queue_manager.clear_cursor()
    assert queue_manager.get_cursor() is None
    assert queue_manager.get_cursor_position() is None


def test_update_pitch(queue_manager, test_users):
    """Test updating pitch for a queue item."""
    item_id = queue_manager.add_song(
        test_users["alice"], "youtube:vid1", "Song 1", pitch_semitones=0
    )

    result = queue_manager.update_pitch(item_id, 3)
    assert result is True

    item = queue_manager.get_item(item_id)
    assert item.settings.pitch_semitones == 3


def test_clear_queue(queue_manager, test_users):
    """Test clearing the entire queue also clears the cursor."""
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")
    queue_manager.add_song(test_users["charlie"], "youtube:vid3", "Song 3")
    queue_manager.set_cursor(id1)

    count = queue_manager.clear_queue()
    assert count == 3

    queue = queue_manager.get_queue()
    assert len(queue) == 0
    assert queue_manager.get_cursor() is None


def test_queue_persistence(temp_db, user_manager, mock_video_library):
    """Test that queue and cursor persist across QueueManager instances."""
    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")

    qm1 = QueueManager(temp_db, video_library=mock_video_library)
    item_id = qm1.add_song(alice, "youtube:vid1", "Song 1")
    qm1.update_download_status(
        item_id, QueueManager.STATUS_READY, download_path="/path/to/video.mp4"
    )
    qm1.set_cursor(item_id)
    qm1.stop_download_monitor()

    # Create new QueueManager with same database
    mock_video_library2 = MagicMock()
    qm2 = QueueManager(temp_db, video_library=mock_video_library2)
    queue = qm2.get_queue()
    assert len(queue) == 1
    assert queue[0].user_id == ALICE_ID
    assert queue[0].user_name == "Alice"
    assert queue[0].download_status == QueueManager.STATUS_READY
    # Cursor should persist across restarts
    assert qm2.get_cursor() == item_id
    qm2.stop_download_monitor()


# =============================================================================
# VideoLibrary Integration Tests
# =============================================================================


def test_download_monitor_calls_video_library(temp_db, user_manager):
    """Test that download monitor calls video_library.request for pending items."""
    mock_video_library = MagicMock()
    mock_video_library.request.return_value = None
    mock_video_library.get_path.return_value = None
    mock_video_library.manage_storage.return_value = 0

    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
    qm = QueueManager(temp_db, video_library=mock_video_library)
    qm.stop_download_monitor()  # Stop background thread, we'll call directly

    # Add song (starts as pending)
    item_id = qm.add_song(alice, "youtube:test_vid", "Test Song")

    # Directly trigger download processing (what the monitor does)
    qm._process_download_queue()

    # Verify video_library.request was called with correct args
    mock_video_library.request.assert_called()
    call_args = mock_video_library.request.call_args
    assert call_args[0][0] == "youtube:test_vid"  # video_id
    assert call_args[1]["callback"] is not None  # callback provided


def test_download_callback_updates_status_ready(temp_db, user_manager):
    """Test that download callback updates queue item to ready status."""
    captured_callback = None

    def capture_callback(video_id, callback=None):
        nonlocal captured_callback
        captured_callback = callback
        return None

    mock_video_library = MagicMock()
    mock_video_library.request.side_effect = capture_callback
    mock_video_library.get_path.return_value = None
    mock_video_library.manage_storage.return_value = 0

    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
    qm = QueueManager(temp_db, video_library=mock_video_library)
    qm.stop_download_monitor()

    item_id = qm.add_song(alice, "youtube:test_vid", "Test Song")

    # Directly trigger download processing
    qm._process_download_queue()

    assert captured_callback is not None

    # Simulate successful download
    captured_callback("ready", "/path/to/video.mp4", None)

    # Verify status was updated
    item = qm.get_item(item_id)
    assert item.download_status == QueueManager.STATUS_READY
    assert item.download_path == "/path/to/video.mp4"


def test_download_callback_updates_status_error(temp_db, user_manager):
    """Test that download callback updates queue item to error status."""
    captured_callback = None

    def capture_callback(video_id, callback=None):
        nonlocal captured_callback
        captured_callback = callback
        return None

    mock_video_library = MagicMock()
    mock_video_library.request.side_effect = capture_callback
    mock_video_library.get_path.return_value = None
    mock_video_library.manage_storage.return_value = 0

    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
    qm = QueueManager(temp_db, video_library=mock_video_library)
    qm.stop_download_monitor()

    item_id = qm.add_song(alice, "youtube:test_vid", "Test Song")

    # Directly trigger download processing
    qm._process_download_queue()

    assert captured_callback is not None

    # Simulate failed download
    captured_callback("error", None, "Download failed: network error")

    # Verify status was updated
    item = qm.get_item(item_id)
    assert item.download_status == QueueManager.STATUS_ERROR
    assert item.error_message == "Download failed: network error"


def test_cleanup_storage_calls_video_library(temp_db, user_manager):
    """Test that storage cleanup calls video_library.manage_storage with protected keys."""
    captured_callback = None

    def capture_and_succeed(video_id, callback=None):
        nonlocal captured_callback
        captured_callback = callback
        return None

    mock_video_library = MagicMock()
    mock_video_library.request.side_effect = capture_and_succeed
    mock_video_library.get_path.return_value = None
    mock_video_library.manage_storage.return_value = 0

    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
    qm = QueueManager(temp_db, video_library=mock_video_library)
    qm.stop_download_monitor()

    # Add songs that will be protected
    qm.add_song(alice, "youtube:vid1", "Song 1")
    qm.add_song(alice, "youtube:vid2", "Song 2")

    # Trigger download processing
    qm._process_download_queue()

    # Simulate successful download (triggers storage cleanup)
    assert captured_callback is not None
    captured_callback("ready", "/path/to/video.mp4", None)

    # Verify manage_storage was called with protected keys
    mock_video_library.manage_storage.assert_called()
    call_args = mock_video_library.manage_storage.call_args[0][0]
    assert "youtube:vid1" in call_args
    assert "youtube:vid2" in call_args


def test_stuck_download_recovery_uses_video_library(temp_db, user_manager):
    """Test that stuck download recovery checks video_library.get_path."""
    mock_video_library = MagicMock()
    mock_video_library.request.return_value = None
    mock_video_library.manage_storage.return_value = 0

    # Simulate that the file exists (download completed but callback failed)
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_path.__str__ = lambda self: "/recovered/path/video.mp4"
    mock_video_library.get_path.return_value = mock_path

    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
    qm = QueueManager(temp_db, video_library=mock_video_library)
    qm.stop_download_monitor()

    item_id = qm.add_song(alice, "youtube:test_vid", "Test Song")

    # Manually set to downloading (simulating a stuck download)
    qm.update_download_status(item_id, QueueManager.STATUS_DOWNLOADING)

    # Trigger the stuck download check
    qm._process_download_queue()

    # Verify get_path was called
    mock_video_library.get_path.assert_called_with("youtube:test_vid")

    # Verify item was recovered to ready status
    item = qm.get_item(item_id)
    assert item.download_status == QueueManager.STATUS_READY
    assert item.download_path == "/recovered/path/video.mp4"


def test_cursor_prevents_replaying_finished_songs(queue_manager, test_users):
    """Test that the cursor prevents replaying songs that have already been played.

    With the cursor model, auto-start uses get_ready_song_at_offset(cursor, +1)
    to look forward from the cursor. After the last song finishes, there are no
    songs after the cursor, so auto-start does not replay the song.
    """
    # Add a single song and mark it as ready
    item_id = queue_manager.add_song(
        test_users["alice"], "youtube:only_song", "Only Song", duration_seconds=180
    )
    queue_manager.update_download_status(
        item_id, QueueManager.STATUS_READY, download_path="/path/to/only_song.mp4"
    )

    # Before playing: should find the song (no cursor set)
    first_song = queue_manager.get_ready_song_at_offset(None, 0)
    assert first_song is not None
    assert first_song.id == item_id

    # Simulate song playing - cursor moves to it
    queue_manager.set_cursor(item_id)

    # After cursor is set: looking for next song AFTER cursor should return None
    # This is the auto-start path: get_ready_song_at_offset(cursor, +1)
    next_song = queue_manager.get_ready_song_at_offset(item_id, +1)
    assert next_song is None, (
        "get_ready_song_at_offset(cursor, +1) should return None when cursor is on "
        "the last song, otherwise auto-start will replay finished songs"
    )


def test_cursor_based_navigation_through_queue(queue_manager, test_users):
    """Test cursor-based queue navigation: the cursor tracks playback position."""
    # Add three songs
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    id2 = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")
    id3 = queue_manager.add_song(test_users["charlie"], "youtube:vid3", "Song 3")

    # Mark all as ready
    queue_manager.update_download_status(
        id1, QueueManager.STATUS_READY, download_path="/path/to/vid1.mp4"
    )
    queue_manager.update_download_status(
        id2, QueueManager.STATUS_READY, download_path="/path/to/vid2.mp4"
    )
    queue_manager.update_download_status(
        id3, QueueManager.STATUS_READY, download_path="/path/to/vid3.mp4"
    )

    # No cursor: first ready song from the start
    first = queue_manager.get_ready_song_at_offset(None, 0)
    assert first is not None
    assert first.id == id1

    # Simulate playing song 1 - cursor moves to it
    queue_manager.set_cursor(id1)

    # Next after cursor (song 1) should be song 2
    next_after_1 = queue_manager.get_ready_song_at_offset(id1, +1)
    assert next_after_1 is not None
    assert next_after_1.id == id2

    # Simulate playing song 2 - cursor moves to it
    queue_manager.set_cursor(id2)

    # Next after cursor (song 2) should be song 3
    next_after_2 = queue_manager.get_ready_song_at_offset(id2, +1)
    assert next_after_2 is not None
    assert next_after_2.id == id3

    # Previous before cursor (song 2) should be song 1
    prev_before_2 = queue_manager.get_ready_song_at_offset(id2, -1)
    assert prev_before_2 is not None
    assert prev_before_2.id == id1

    # Simulate playing song 3 - cursor moves to it
    queue_manager.set_cursor(id3)

    # No next song after cursor (song 3 is last)
    no_next = queue_manager.get_ready_song_at_offset(id3, +1)
    assert no_next is None
