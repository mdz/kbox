"""
Unit tests for QueueManager.
"""

import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from kbox.database import Database
from kbox.queue import MoveOutcome, MoveResult, QueueManager
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
    qm.stop_content_monitor()


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
    assert queue[0].content_status == QueueManager.STATUS_PENDING
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


def test_replace_song(queue_manager, test_users):
    """Replacing a song swaps video/metadata but keeps position and attribution."""
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    id2 = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")
    queue_manager.update_content_status(
        id2, QueueManager.STATUS_READY, content_path="/tmp/vid2.mp4"
    )

    result = queue_manager.replace_song(
        id2,
        video_id="youtube:vid2-correct",
        title="Song 2 (Correct Version)",
        duration_seconds=210,
        thumbnail_url="http://example.com/correct.jpg",
        channel="Correct Channel",
    )
    assert result is True

    queue = queue_manager.get_queue()
    assert len(queue) == 2

    replaced = queue_manager.get_item(id2)
    assert replaced.id == id2
    assert replaced.position == 2  # unchanged
    assert replaced.user_id == BOB_ID  # attribution unchanged
    assert replaced.user_name == "Bob"
    assert replaced.video_id == "youtube:vid2-correct"
    assert replaced.metadata.title == "Song 2 (Correct Version)"
    assert replaced.metadata.duration_seconds == 210
    assert replaced.content_status == QueueManager.STATUS_PENDING  # reset for redownload
    assert replaced.content_path is None

    # Other item untouched
    other = queue_manager.get_item(id1)
    assert other.video_id == "youtube:vid1"
    assert other.position == 1


def test_replace_nonexistent_song(queue_manager):
    """Replacing a non-existent song fails cleanly."""
    result = queue_manager.replace_song(999, video_id="youtube:x", title="X")
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


def test_move_down(queue_manager, test_users):
    """Test moving a song down one position."""
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    id2 = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")
    queue_manager.add_song(test_users["charlie"], "youtube:vid3", "Song 3")

    result = queue_manager.move_down(id1)

    assert result == MoveResult(MoveOutcome.MOVED, old_position=1, new_position=2)
    queue = queue_manager.get_queue()
    assert [item.id for item in queue] == [id2, id1, queue[2].id]


def test_move_down_at_end_of_queue(queue_manager, test_users):
    """The last song has nowhere to go, and says so rather than failing."""
    queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    id2 = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")

    result = queue_manager.move_down(id2)

    assert result == MoveResult(MoveOutcome.ALREADY_AT_BOUNDARY, old_position=2)


def test_move_down_nonexistent_song(queue_manager):
    assert queue_manager.move_down(9999) == MoveResult(MoveOutcome.NOT_FOUND)


def test_move_up(queue_manager, test_users):
    """Test moving a song up one position."""
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    id2 = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")

    result = queue_manager.move_up(id2)

    assert result == MoveResult(MoveOutcome.MOVED, old_position=2, new_position=1)
    assert [item.id for item in queue_manager.get_queue()] == [id2, id1]


def test_move_up_at_start_of_queue(queue_manager, test_users):
    """The first song has nowhere to go, and says so rather than failing."""
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")

    result = queue_manager.move_up(id1)

    assert result == MoveResult(MoveOutcome.ALREADY_AT_BOUNDARY, old_position=1)


def test_move_up_nonexistent_song(queue_manager):
    assert queue_manager.move_up(9999) == MoveResult(MoveOutcome.NOT_FOUND)


def test_move_after_uses_fresh_reference_position(queue_manager, test_users):
    """move_after queries the reference song's position fresh, not from a cache.

    Regression test: after an earlier reorder, a position the caller is
    holding has gone stale, so the reference must be looked up again.
    """
    ids = [
        queue_manager.add_song(test_users["alice"], f"youtube:vid{n}", f"Song {n}")
        for n in range(1, 7)
    ]
    reference_id = ids[4]  # position 5
    song_to_move = ids[1]  # position 2

    # Shuffle the queue so any position captured before now is stale: moving
    # the first song to the end shifts everything behind it up one.
    queue_manager.reorder_song(ids[0], 6)
    assert queue_manager.get_item(reference_id).position == 4

    new_position = queue_manager.move_after(song_to_move, reference_id)

    # Directly after the reference at its fresh position, not its stale one
    order = [s.id for s in queue_manager.get_queue()]
    assert order.index(song_to_move) == order.index(reference_id) + 1
    assert queue_manager.get_item(song_to_move).position == new_position


def test_move_after_moving_down_lands_right_after_reference(queue_manager, test_users):
    """Moving a song *down* puts it directly after the reference, not one further.

    reorder() shifts the songs in between up before inserting, so the target
    is the reference's own position, not position + 1. Matters for giving a
    singer another go right after whoever is singing now.
    """
    a = queue_manager.add_song(test_users["alice"], "youtube:a", "A")
    b = queue_manager.add_song(test_users["bob"], "youtube:b", "B")
    c = queue_manager.add_song(test_users["alice"], "youtube:c", "C")

    assert queue_manager.move_after(a, b) == 2
    assert [s.id for s in queue_manager.get_queue()] == [b, a, c]


def test_move_after_last_song_clamps_to_end_of_queue(queue_manager, test_users):
    """Regression test for https://github.com/mdz/kbox/issues/89.

    When the reference song is last in the queue, reference.position + 1
    exceeds the max position, so reorder() would reject it as an "invalid
    position" - which the API layer misreports as "Queue item not found".
    "After" the last song should simply mean the end of the queue.
    """
    song_to_move = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    reference_id = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")

    new_position = queue_manager.move_after(song_to_move, reference_id)

    # Clamped to max position (2), not 3 (which would be rejected as invalid)
    assert new_position == 2
    assert queue_manager.get_item(song_to_move).position == 2


def test_move_after_with_no_reference_moves_to_front(queue_manager, test_users):
    queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    id2 = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")

    assert queue_manager.move_after(id2, None) == 1
    assert queue_manager.get_queue()[0].id == id2


def test_move_after_nonexistent_reference(queue_manager, test_users):
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")

    assert queue_manager.move_after(id1, 9999) is None


def test_move_to_end(queue_manager, test_users):
    """Test moving a song to the end of the queue."""
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    id2 = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")
    id3 = queue_manager.add_song(test_users["charlie"], "youtube:vid3", "Song 3")

    assert queue_manager.move_to_end(id1) == 3
    assert [item.id for item in queue_manager.get_queue()] == [id2, id3, id1]


def test_move_to_end_empty_queue(queue_manager):
    assert queue_manager.move_to_end(9999) is None


def test_move_to_end_nonexistent_song(queue_manager, test_users):
    queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")

    assert queue_manager.move_to_end(9999) is None


def test_get_song_at_offset(queue_manager, test_users):
    """Test getting song at offset (regardless of download status)."""
    id1 = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    id2 = queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")
    id3 = queue_manager.add_song(test_users["alice"], "youtube:vid3", "Song 3")

    # Get first song (no reference)
    first = queue_manager.get_song_at_offset(None, 0)
    assert first is not None
    assert first.id == id1

    # Get next song after first (even though none are ready)
    second = queue_manager.get_song_at_offset(id1, +1)
    assert second is not None
    assert second.id == id2
    assert second.content_status == QueueManager.STATUS_PENDING  # Not ready!

    # Get previous song before second
    prev = queue_manager.get_song_at_offset(id2, -1)
    assert prev is not None
    assert prev.id == id1

    # No next song after last
    no_next = queue_manager.get_song_at_offset(id3, +1)
    assert no_next is None

    # No previous song before first
    no_prev = queue_manager.get_song_at_offset(id1, -1)
    assert no_prev is None

    # Reference song not in queue - falls back to first
    fallback = queue_manager.get_song_at_offset(9999, +1)
    assert fallback is not None
    assert fallback.id == id1

    # Empty queue
    queue_manager.clear_queue()
    assert queue_manager.get_song_at_offset(None, 0) is None


def test_update_content_status(queue_manager, test_users):
    """Test updating download status."""
    item_id = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")

    # Update to downloading
    result = queue_manager.update_content_status(item_id, QueueManager.STATUS_PREPARING)
    assert result is True

    item = queue_manager.get_item(item_id)
    assert item.content_status == QueueManager.STATUS_PREPARING

    # Update to ready with path
    result = queue_manager.update_content_status(
        item_id, QueueManager.STATUS_READY, content_path="/path/to/video.mp4"
    )
    assert result is True

    item = queue_manager.get_item(item_id)
    assert item.content_status == QueueManager.STATUS_READY
    assert item.content_path == "/path/to/video.mp4"


def test_update_content_status_error(queue_manager, test_users):
    """Test updating download status with error."""
    item_id = queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")

    result = queue_manager.update_content_status(
        item_id, QueueManager.STATUS_ERROR, error_message="Download failed"
    )
    assert result is True

    item = queue_manager.get_item(item_id)
    assert item.content_status == QueueManager.STATUS_ERROR
    assert item.error_message == "Download failed"


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
    """Test clearing the entire queue."""
    queue_manager.add_song(test_users["alice"], "youtube:vid1", "Song 1")
    queue_manager.add_song(test_users["bob"], "youtube:vid2", "Song 2")
    queue_manager.add_song(test_users["charlie"], "youtube:vid3", "Song 3")

    count = queue_manager.clear_queue()
    assert count == 3

    queue = queue_manager.get_queue()
    assert len(queue) == 0


def test_queue_persistence(temp_db, user_manager, mock_video_library):
    """Test that queue persists across QueueManager instances."""
    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")

    qm1 = QueueManager(temp_db, video_library=mock_video_library)
    item_id = qm1.add_song(alice, "youtube:vid1", "Song 1")
    qm1.update_content_status(item_id, QueueManager.STATUS_READY, content_path="/path/to/video.mp4")
    qm1.stop_content_monitor()

    # Create new QueueManager with same database
    mock_video_library2 = MagicMock()
    qm2 = QueueManager(temp_db, video_library=mock_video_library2)
    queue = qm2.get_queue()
    assert len(queue) == 1
    assert queue[0].user_id == ALICE_ID
    assert queue[0].user_name == "Alice"
    assert queue[0].content_status == QueueManager.STATUS_READY
    qm2.stop_content_monitor()


def test_content_monitor_runs_until_stopped(temp_db, mock_video_library):
    """The content monitor ticks on its own thread, and stop_content_monitor()
    ends it without waiting out the interval (the join is only a guard)."""
    ticked = threading.Event()
    with patch.object(QueueManager, "_process_pending_content", side_effect=ticked.set):
        qm = QueueManager(temp_db, video_library=mock_video_library)
        assert ticked.wait(10)

        qm.stop_content_monitor()

    assert not qm._content_monitor.is_alive()
    # Idempotent: the fixtures and main.py may both stop it
    qm.stop_content_monitor()


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
    qm.stop_content_monitor()  # Stop background thread, we'll call directly

    # Add song (starts as pending)
    item_id = qm.add_song(alice, "youtube:test_vid", "Test Song")

    # Directly trigger download processing (what the monitor does)
    qm._process_pending_content()

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
    qm.stop_content_monitor()

    item_id = qm.add_song(alice, "youtube:test_vid", "Test Song")

    # Directly trigger download processing
    qm._process_pending_content()

    assert captured_callback is not None

    # Simulate successful download
    captured_callback("ready", "/path/to/video.mp4", None)

    # Verify status was updated
    item = qm.get_item(item_id)
    assert item.content_status == QueueManager.STATUS_READY
    assert item.content_path == "/path/to/video.mp4"


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
    qm.stop_content_monitor()

    item_id = qm.add_song(alice, "youtube:test_vid", "Test Song")

    # Directly trigger download processing
    qm._process_pending_content()

    assert captured_callback is not None

    # Simulate failed download
    captured_callback("error", None, "Download failed: network error")

    # Verify status was updated
    item = qm.get_item(item_id)
    assert item.content_status == QueueManager.STATUS_ERROR
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
    qm.stop_content_monitor()

    # Add songs that will be protected
    qm.add_song(alice, "youtube:vid1", "Song 1")
    qm.add_song(alice, "youtube:vid2", "Song 2")

    # Trigger download processing
    qm._process_pending_content()

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
    qm.stop_content_monitor()

    item_id = qm.add_song(alice, "youtube:test_vid", "Test Song")

    # Manually set to downloading (simulating a stuck download)
    qm.update_content_status(item_id, QueueManager.STATUS_PREPARING)

    # Trigger the stuck download check
    qm._process_pending_content()

    # Verify get_path was called
    mock_video_library.get_path.assert_called_with("youtube:test_vid")

    # Verify item was recovered to ready status
    item = qm.get_item(item_id)
    assert item.content_status == QueueManager.STATUS_READY
    assert item.content_path == "/recovered/path/video.mp4"


def test_stuck_check_after_replace_does_not_false_positive(temp_db, user_manager):
    """A replace on a long-queued item must not look instantly 'stuck'.

    The item's created_at can be arbitrarily old (it's the original queue
    time, unaffected by replace), but the *current* content-preparation
    attempt just started — the stuck check must key off that, not
    created_at, or it immediately resets the item back to pending and
    triggers a redundant re-download.
    """
    mock_video_library = MagicMock()
    mock_video_library.request.return_value = None
    mock_video_library.get_path.return_value = None
    mock_video_library.manage_storage.return_value = 0

    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
    qm = QueueManager(temp_db, video_library=mock_video_library)
    qm.stop_content_monitor()

    item_id = qm.add_song(alice, "youtube:original", "Original Song")

    # Simulate the item having been queued a long time ago (well past the
    # stuck timeout), as would be true right after a same-item replace.
    old_time = datetime.now() - timedelta(minutes=27)
    conn = temp_db.get_connection()
    conn.execute(
        "UPDATE queue_items SET created_at = ? WHERE id = ?",
        (old_time.isoformat(sep=" "), item_id),
    )
    conn.commit()
    conn.close()

    qm.replace_song(item_id, video_id="youtube:replacement", title="Replacement Song")

    # Content monitor picks up the now-pending replacement and starts prep.
    qm._process_pending_content()
    item = qm.get_item(item_id)
    assert item.content_status == QueueManager.STATUS_PREPARING

    # Immediately checking again (as the 2s-later poll would) must not treat
    # this fresh prep attempt as stuck just because created_at is old.
    qm._process_pending_content()
    item = qm.get_item(item_id)
    assert item.content_status == QueueManager.STATUS_PREPARING
    mock_video_library.request.assert_called_once()


def test_no_duplicate_download_while_prep_in_flight(temp_db, user_manager):
    """Two rapid poll cycles for the same item must not spawn two downloads.

    Regression test: previously content_status only flipped to PREPARING
    asynchronously (from the download thread's callback), so if that thread
    hadn't started running yet, a second poll would see STATUS_PENDING and
    call video_library.request() again for the same video.
    """
    mock_video_library = MagicMock()
    mock_video_library.request.return_value = None  # download thread hasn't run yet
    mock_video_library.get_path.return_value = None
    mock_video_library.manage_storage.return_value = 0

    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
    qm = QueueManager(temp_db, video_library=mock_video_library)
    qm.stop_content_monitor()

    item_id = qm.add_song(alice, "youtube:slow_start", "Slow Start Song")

    qm._process_pending_content()
    qm._process_pending_content()
    qm._process_pending_content()

    mock_video_library.request.assert_called_once()
    item = qm.get_item(item_id)
    assert item.content_status == QueueManager.STATUS_PREPARING


# =============================================================================
# Metadata Extraction Tests
# =============================================================================


class TestMetadataExtraction:
    """Tests for metadata extraction triggered by content becoming ready."""

    def _make_qm_with_captured_callback(self, temp_db, mock_extractor):
        """Create a QueueManager whose download callback is captured, not run."""
        captured_callback = {}

        def capture_callback(video_id, callback=None):
            captured_callback["fn"] = callback
            return None

        mock_video_library = MagicMock()
        mock_video_library.request.side_effect = capture_callback
        mock_video_library.get_path.return_value = None
        mock_video_library.manage_storage.return_value = 0

        qm = QueueManager(
            temp_db,
            video_library=mock_video_library,
            metadata_extractor=mock_extractor,
        )
        qm.stop_content_monitor()
        return qm, captured_callback

    def test_extract_not_called_at_add_time(self, temp_db, user_manager):
        """Adding a song does not trigger metadata extraction."""
        mock_extractor = Mock()
        mock_extractor.extract.return_value = ("Journey", "Don't Stop Believin'")

        qm, captured_callback = self._make_qm_with_captured_callback(temp_db, mock_extractor)

        user = user_manager.get_or_create_user("u1", "Alice")
        qm.add_song(user, "youtube:abc", "Don't Stop Believin Karaoke", duration_seconds=180)
        qm._process_pending_content()

        assert not mock_extractor.extract.called

    def test_extract_called_when_content_ready(self, temp_db, user_manager):
        """When a queue item's content becomes ready, metadata extraction runs."""
        mock_extractor = Mock()
        mock_extractor.extract.return_value = ("Journey", "Don't Stop Believin'")

        qm, captured_callback = self._make_qm_with_captured_callback(temp_db, mock_extractor)

        user = user_manager.get_or_create_user("u1", "Alice")
        qm.add_song(user, "youtube:abc", "Don't Stop Believin Karaoke", duration_seconds=180)
        qm._process_pending_content()

        assert captured_callback["fn"] is not None
        captured_callback["fn"]("ready", "/path/to/video.mp4", None)

        assert mock_extractor.extract.called

    def test_extracted_metadata_persisted(self, temp_db, user_manager):
        """Extracted artist/song_name are saved back to the queue item."""
        mock_extractor = Mock()
        mock_extractor.extract.return_value = ("Queen", "Bohemian Rhapsody")

        qm, captured_callback = self._make_qm_with_captured_callback(temp_db, mock_extractor)

        user = user_manager.get_or_create_user("u1", "Alice")
        item_id = qm.add_song(
            user, "youtube:xyz", "Bohemian Rhapsody Karaoke", duration_seconds=354
        )
        qm._process_pending_content()

        captured_callback["fn"]("ready", "/path/to/video.mp4", None)

        item = qm.get_item(item_id)
        assert item.metadata.artist == "Queen"
        assert item.metadata.song_name == "Bohemian Rhapsody"

    def test_extraction_error_does_not_break_queue(self, temp_db, user_manager):
        """If metadata extraction fails, queue operations continue normally."""
        mock_extractor = Mock()
        mock_extractor.extract.side_effect = Exception("LLM API error")

        qm, captured_callback = self._make_qm_with_captured_callback(temp_db, mock_extractor)

        user = user_manager.get_or_create_user("u1", "Alice")
        item_id = qm.add_song(user, "youtube:err", "Some Song", duration_seconds=200)
        qm._process_pending_content()

        captured_callback["fn"]("ready", "/path/to/video.mp4", None)

        # Queue should still work fine
        item = qm.get_item(item_id)
        assert item is not None
        assert item.metadata.title == "Some Song"
        assert item.content_status == QueueManager.STATUS_READY

    def test_extract_called_on_stuck_content_recovery(self, temp_db, user_manager):
        """Recovering a stuck item that already has a downloaded file also triggers extraction."""
        mock_extractor = Mock()
        mock_extractor.extract.return_value = ("Queen", "Bohemian Rhapsody")

        mock_video_library = MagicMock()
        mock_video_library.request.return_value = None
        mock_video_library.manage_storage.return_value = 0

        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.__str__ = lambda self: "/recovered/path/video.mp4"
        mock_video_library.get_path.return_value = mock_path

        qm = QueueManager(
            temp_db,
            video_library=mock_video_library,
            metadata_extractor=mock_extractor,
        )
        qm.stop_content_monitor()

        user = user_manager.get_or_create_user("u1", "Alice")
        item_id = qm.add_song(user, "youtube:stuck", "Bohemian Rhapsody Karaoke")

        qm.update_content_status(item_id, QueueManager.STATUS_PREPARING)
        qm._process_pending_content()

        assert mock_extractor.extract.called
        item = qm.get_item(item_id)
        assert item.metadata.artist == "Queen"
        assert item.metadata.song_name == "Bohemian Rhapsody"


class TestSilenceAnalysis:
    """Tests for trailing-silence analysis, triggered once content is ready."""

    def test_analyze_called_when_content_ready(self, temp_db, user_manager):
        """When a song's content becomes ready, silence analysis runs."""
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = 120

        captured_callback = None

        def capture_callback(video_id, callback=None):
            nonlocal captured_callback
            captured_callback = callback
            return None

        mock_video_library = MagicMock()
        mock_video_library.request.side_effect = capture_callback
        mock_video_library.get_path.return_value = None
        mock_video_library.manage_storage.return_value = 0

        qm = QueueManager(
            temp_db,
            video_library=mock_video_library,
            silence_analyzer=mock_analyzer,
        )
        qm.stop_content_monitor()

        user = user_manager.get_or_create_user("u1", "Alice")
        qm.add_song(user, "youtube:abc", "Some Karaoke Track", duration_seconds=180)

        qm._process_pending_content()
        captured_callback("ready", "/path/to/video.mp4", None)

        mock_analyzer.analyze.assert_called_once_with("youtube:abc", "/path/to/video.mp4")

    def test_analysis_error_does_not_break_queue(self, temp_db, user_manager):
        """If silence analysis fails, queue operations continue normally."""
        mock_analyzer = Mock()
        mock_analyzer.analyze.side_effect = Exception("ffmpeg not found")

        captured_callback = None

        def capture_callback(video_id, callback=None):
            nonlocal captured_callback
            captured_callback = callback
            return None

        mock_video_library = MagicMock()
        mock_video_library.request.side_effect = capture_callback
        mock_video_library.get_path.return_value = None
        mock_video_library.manage_storage.return_value = 0

        qm = QueueManager(
            temp_db,
            video_library=mock_video_library,
            silence_analyzer=mock_analyzer,
        )
        qm.stop_content_monitor()

        user = user_manager.get_or_create_user("u1", "Alice")
        item_id = qm.add_song(user, "youtube:err", "Some Song", duration_seconds=200)

        qm._process_pending_content()
        captured_callback("ready", "/path/to/video.mp4", None)

        item = qm.get_item(item_id)
        assert item is not None
        assert item.content_status == QueueManager.STATUS_READY

    def test_analyze_called_on_stuck_content_recovery(self, temp_db, user_manager):
        """Recovering a stuck item that already has a downloaded file also triggers silence analysis."""
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = 120

        mock_video_library = MagicMock()
        mock_video_library.request.return_value = None
        mock_video_library.manage_storage.return_value = 0

        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.__str__ = lambda self: "/recovered/path/video.mp4"
        mock_video_library.get_path.return_value = mock_path

        qm = QueueManager(
            temp_db,
            video_library=mock_video_library,
            silence_analyzer=mock_analyzer,
        )
        qm.stop_content_monitor()

        user = user_manager.get_or_create_user("u1", "Alice")
        item_id = qm.add_song(user, "youtube:stuck", "Some Karaoke Track")

        qm.update_content_status(item_id, QueueManager.STATUS_PREPARING)
        qm._process_pending_content()

        mock_analyzer.analyze.assert_called_once_with("youtube:stuck", "/recovered/path/video.mp4")


class TestLoudnessAnalysis:
    """Tests for loudness analysis, triggered once content is ready."""

    def test_analyze_called_when_content_ready(self, temp_db, user_manager):
        """When a song's content becomes ready, loudness analysis runs."""
        from kbox.loudness import LoudnessInfo

        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = LoudnessInfo(
            integrated_lufs=-14.0, true_peak_dbtp=-1.0
        )

        captured_callback = None

        def capture_callback(video_id, callback=None):
            nonlocal captured_callback
            captured_callback = callback
            return None

        mock_video_library = MagicMock()
        mock_video_library.request.side_effect = capture_callback
        mock_video_library.get_path.return_value = None
        mock_video_library.manage_storage.return_value = 0

        qm = QueueManager(
            temp_db,
            video_library=mock_video_library,
            loudness_analyzer=mock_analyzer,
        )
        qm.stop_content_monitor()

        user = user_manager.get_or_create_user("u1", "Alice")
        qm.add_song(user, "youtube:abc", "Some Karaoke Track", duration_seconds=180)

        qm._process_pending_content()
        captured_callback("ready", "/path/to/video.mp4", None)

        mock_analyzer.analyze.assert_called_once_with("youtube:abc", "/path/to/video.mp4")

    def test_analysis_error_does_not_break_queue(self, temp_db, user_manager):
        """If loudness analysis fails, queue operations continue normally."""
        mock_analyzer = Mock()
        mock_analyzer.analyze.side_effect = Exception("ffmpeg not found")

        captured_callback = None

        def capture_callback(video_id, callback=None):
            nonlocal captured_callback
            captured_callback = callback
            return None

        mock_video_library = MagicMock()
        mock_video_library.request.side_effect = capture_callback
        mock_video_library.get_path.return_value = None
        mock_video_library.manage_storage.return_value = 0

        qm = QueueManager(
            temp_db,
            video_library=mock_video_library,
            loudness_analyzer=mock_analyzer,
        )
        qm.stop_content_monitor()

        user = user_manager.get_or_create_user("u1", "Alice")
        item_id = qm.add_song(user, "youtube:err", "Some Song", duration_seconds=200)

        qm._process_pending_content()
        captured_callback("ready", "/path/to/video.mp4", None)

        item = qm.get_item(item_id)
        assert item is not None
        assert item.content_status == QueueManager.STATUS_READY

    def test_analyze_called_on_stuck_content_recovery(self, temp_db, user_manager):
        """Recovering a stuck item that already has a downloaded file also triggers loudness analysis."""
        from kbox.loudness import LoudnessInfo

        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = LoudnessInfo(
            integrated_lufs=-14.0, true_peak_dbtp=-1.0
        )

        mock_video_library = MagicMock()
        mock_video_library.request.return_value = None
        mock_video_library.manage_storage.return_value = 0

        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.__str__ = lambda self: "/recovered/path/video.mp4"
        mock_video_library.get_path.return_value = mock_path

        qm = QueueManager(
            temp_db,
            video_library=mock_video_library,
            loudness_analyzer=mock_analyzer,
        )
        qm.stop_content_monitor()

        user = user_manager.get_or_create_user("u1", "Alice")
        item_id = qm.add_song(user, "youtube:stuck", "Some Karaoke Track")

        qm.update_content_status(item_id, QueueManager.STATUS_PREPARING)
        qm._process_pending_content()

        mock_analyzer.analyze.assert_called_once_with("youtube:stuck", "/recovered/path/video.mp4")


@pytest.mark.parametrize("operation", ["add", "remove", "reorder"])
def test_position_reads_happen_inside_the_write_transaction(
    queue_manager, test_users, temp_db, operation
):
    """add/remove/reorder read a position and then write rows based on it.

    The read must come after BEGIN IMMEDIATE, so it runs under the write
    lock; otherwise two concurrent adds can both read the same
    MAX(position) and land in the same slot. Checked by tracing the SQL
    each operation sends, since a thread race would only fail sometimes.
    """
    first = queue_manager.add_song(test_users["alice"], "youtube:a", "A")
    queue_manager.add_song(test_users["bob"], "youtube:b", "B")

    statements = []
    real_get_connection = temp_db.get_connection

    def traced_connection():
        conn = real_get_connection()
        conn.set_trace_callback(statements.append)
        return conn

    temp_db.get_connection = traced_connection
    try:
        if operation == "add":
            queue_manager.add_song(test_users["charlie"], "youtube:c", "C")
        elif operation == "remove":
            queue_manager.remove_song(first)
        else:
            queue_manager.reorder_song(first, 2)
    finally:
        temp_db.get_connection = real_get_connection

    assert statements[0] == "BEGIN IMMEDIATE"
    assert any("SELECT" in sql and "position" in sql for sql in statements[1:])


class TestReplacedWhileDownloading:
    """
    A download runs on the provider's worker for many seconds, and its item
    can be replaced with a different video meanwhile. The old download's
    result must not be applied to the replacement.
    """

    def _make_qm(self, temp_db, **kwargs):
        captured = {}

        def capture(video_id, callback=None):
            captured[video_id] = callback
            return None

        mock_video_library = MagicMock()
        mock_video_library.request.side_effect = capture
        mock_video_library.get_path.return_value = None
        mock_video_library.manage_storage.return_value = 0

        qm = QueueManager(temp_db, video_library=mock_video_library, **kwargs)
        qm.stop_content_monitor()
        return qm, captured, mock_video_library

    def test_old_download_finishing_does_not_mark_replacement_ready(self, temp_db, user_manager):
        silence = Mock()
        loudness = Mock()
        extractor = Mock()
        qm, captured, _ = self._make_qm(
            temp_db,
            silence_analyzer=silence,
            loudness_analyzer=loudness,
            metadata_extractor=extractor,
        )
        alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
        item_id = qm.add_song(alice, "youtube:a", "Wrong Version")
        qm._process_pending_content()

        qm.replace_song(item_id, video_id="youtube:b", title="Right Version")
        captured["youtube:a"]("ready", "/lib/youtube/a/video.mp4", None)

        item = qm.get_item(item_id)
        assert item.video_id == "youtube:b"
        assert item.content_status == QueueManager.STATUS_PENDING
        assert item.content_path is None
        # Nothing about A's file is cached under B (or at all).
        silence.analyze.assert_not_called()
        loudness.analyze.assert_not_called()
        extractor.extract.assert_not_called()

    def test_old_download_failing_does_not_mark_replacement_failed(self, temp_db, user_manager):
        qm, captured, _ = self._make_qm(temp_db)
        alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
        item_id = qm.add_song(alice, "youtube:a", "Wrong Version")
        qm._process_pending_content()

        qm.replace_song(item_id, video_id="youtube:b", title="Right Version")
        captured["youtube:a"]("error", None, "Video unavailable")

        item = qm.get_item(item_id)
        assert item.content_status == QueueManager.STATUS_PENDING
        assert item.error_message is None

    def test_replacement_still_downloads_after_old_result_is_dropped(self, temp_db, user_manager):
        qm, captured, _ = self._make_qm(temp_db)
        alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
        item_id = qm.add_song(alice, "youtube:a", "Wrong Version")
        qm._process_pending_content()

        qm.replace_song(item_id, video_id="youtube:b", title="Right Version")
        captured["youtube:a"]("ready", "/lib/youtube/a/video.mp4", None)
        qm._process_pending_content()
        captured["youtube:b"]("ready", "/lib/youtube/b/video.mp4", None)

        item = qm.get_item(item_id)
        assert item.content_status == QueueManager.STATUS_READY
        assert item.content_path == "/lib/youtube/b/video.mp4"

    def test_replace_during_metadata_extraction_keeps_replacement_metadata(
        self, temp_db, user_manager
    ):
        """The LLM call takes seconds; a replace can land while it runs."""
        silence = Mock()
        extractor = Mock()
        qm, captured, _ = self._make_qm(
            temp_db, silence_analyzer=silence, metadata_extractor=extractor
        )
        alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
        item_id = qm.add_song(alice, "youtube:a", "Wrong Version")
        qm._process_pending_content()

        def replace_mid_extraction(**kwargs):
            qm.replace_song(item_id, video_id="youtube:b", title="Right Version")
            return ("Artist A", "Song A")

        extractor.extract.side_effect = replace_mid_extraction
        captured["youtube:a"]("ready", "/lib/youtube/a/video.mp4", None)

        item = qm.get_item(item_id)
        assert item.metadata.title == "Right Version"
        assert item.metadata.artist is None
        assert item.metadata.song_name is None
        # The analysis that did run is keyed by the video the file holds.
        silence.analyze.assert_called_once_with("youtube:a", "/lib/youtube/a/video.mp4")

    def test_stale_poll_snapshot_does_not_request_old_video(self, temp_db, user_manager):
        """An item replaced after the poll read the queue is not prepared as the old video."""
        qm, captured, mock_video_library = self._make_qm(temp_db)
        alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
        item_id = qm.add_song(alice, "youtube:a", "Wrong Version")
        stale = qm.get_item(item_id)

        qm.replace_song(item_id, video_id="youtube:b", title="Right Version")
        qm._start_content_preparation(stale)

        mock_video_library.request.assert_not_called()
        assert qm.get_item(item_id).content_status == QueueManager.STATUS_PENDING

    def test_stale_poll_snapshot_does_not_recover_old_file(self, temp_db, user_manager):
        """The stuck check must not mark the replacement READY with the old video's file."""
        silence = Mock()
        qm, captured, mock_video_library = self._make_qm(temp_db, silence_analyzer=silence)
        alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
        item_id = qm.add_song(alice, "youtube:a", "Wrong Version")
        qm._process_pending_content()
        stale = qm.get_item(item_id)

        qm.replace_song(item_id, video_id="youtube:b", title="Right Version")
        found = MagicMock(spec=Path)
        found.exists.return_value = True
        found.__str__ = lambda self: "/lib/youtube/a/video.mp4"
        mock_video_library.get_path.return_value = found
        qm._check_stuck_content(stale)

        item = qm.get_item(item_id)
        assert item.content_status == QueueManager.STATUS_PENDING
        assert item.content_path is None
        silence.analyze.assert_not_called()


class TestDownloadFinishedTwice:
    """
    A finished download can be noticed by two threads: its own completion
    callback on the library worker, and the content monitor's stuck check,
    which acts on a queue snapshot taken at the start of its poll. Only the
    one that marks the item READY analyses the file.
    """

    def _make_qm(self, temp_db, **kwargs):
        captured = {}

        def capture(video_id, callback=None):
            captured[video_id] = callback
            return None

        mock_video_library = MagicMock()
        mock_video_library.request.side_effect = capture
        mock_video_library.get_path.return_value = None
        mock_video_library.manage_storage.return_value = 0

        qm = QueueManager(temp_db, video_library=mock_video_library, **kwargs)
        qm.stop_content_monitor()
        return qm, captured, mock_video_library

    @staticmethod
    def _file_found(mock_video_library, path):
        found = MagicMock(spec=Path)
        found.exists.return_value = True
        found.__str__ = lambda self: path
        mock_video_library.get_path.return_value = found

    def test_callback_first_then_stale_monitor_snapshot(self, temp_db, user_manager):
        silence = Mock()
        extractor = Mock()
        extractor.extract.return_value = ("Don McLean", "American Pie")
        qm, captured, lib = self._make_qm(
            temp_db, silence_analyzer=silence, metadata_extractor=extractor
        )
        alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
        item_id = qm.add_song(alice, "youtube:a", "American Pie")
        qm._process_pending_content()
        snapshot = qm.get_item(item_id)  # the monitor's read, still PREPARING

        captured["youtube:a"]("ready", "/lib/youtube/a/video.mp4", None)
        self._file_found(lib, "/lib/youtube/a/video.mp4")
        qm._check_stuck_content(snapshot)

        assert qm.get_item(item_id).content_status == QueueManager.STATUS_READY
        assert extractor.extract.call_count == 1
        assert silence.analyze.call_count == 1

    def test_monitor_first_then_callback(self, temp_db, user_manager):
        silence = Mock()
        extractor = Mock()
        extractor.extract.return_value = ("Don McLean", "American Pie")
        qm, captured, lib = self._make_qm(
            temp_db, silence_analyzer=silence, metadata_extractor=extractor
        )
        alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
        item_id = qm.add_song(alice, "youtube:a", "American Pie")
        qm._process_pending_content()

        self._file_found(lib, "/lib/youtube/a/video.mp4")
        qm._process_pending_content()
        captured["youtube:a"]("ready", "/lib/youtube/a/video.mp4", None)

        assert qm.get_item(item_id).content_status == QueueManager.STATUS_READY
        assert extractor.extract.call_count == 1
        assert silence.analyze.call_count == 1
        # The callback still keeps the library within its size limit.
        lib.manage_storage.assert_called()
