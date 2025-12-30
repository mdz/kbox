"""
Integration tests for kbox.

Tests the integration between components without external dependencies.
"""

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

import pytest

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.history import HistoryManager
from kbox.playback import PlaybackController, PlaybackState
from kbox.queue import QueueManager
from kbox.user import UserManager
from kbox.video_library import VideoLibrary, VideoSource

# Test user IDs
ALICE_ID = "alice-uuid-1234"
BOB_ID = "bob-uuid-5678"
CHARLIE_ID = "charlie-uuid-9012"


class FakeVideoSource(VideoSource):
    """
    Fake video source for integration testing.

    Simulates video search and download without hitting real APIs.
    """

    def __init__(self, source_id: str = "fake"):
        self._source_id = source_id
        self._configured = True
        self._search_results: List[Dict[str, Any]] = []
        self._video_info: Dict[str, Dict[str, Any]] = {}
        self._fail_downloads: bool = False
        self._fail_message: str = "Simulated download error"

    @property
    def source_id(self) -> str:
        return self._source_id

    def is_configured(self) -> bool:
        return self._configured

    def set_search_results(self, results: List[Dict[str, Any]]) -> None:
        """Set the results that will be returned by search()."""
        self._search_results = results

    def set_video_info(self, video_id: str, info: Dict[str, Any]) -> None:
        """Set info that will be returned by get_video_info()."""
        self._video_info[video_id] = info

    def set_fail_downloads(self, fail: bool, message: str = "Simulated download error") -> None:
        """Configure the source to fail all downloads."""
        self._fail_downloads = fail
        self._fail_message = message

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        return self._search_results[:max_results]

    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        return self._video_info.get(video_id)

    def download(self, video_id: str, output_dir: Path) -> Path:
        """Create a fake cached file synchronously."""
        if self._fail_downloads:
            raise RuntimeError(self._fail_message)

        # Create fake video file in the output directory
        output_dir.mkdir(parents=True, exist_ok=True)
        video_file = output_dir / "video.mp4"
        video_file.write_bytes(b"fake video content for " + video_id.encode())
        return video_file


def wait_for_download(queue, item_id, expected_status, timeout=0.5):
    """Wait for a download to reach the expected status."""
    iterations = int(timeout / 0.01)
    for _ in range(iterations):
        item = queue.get_item(item_id)
        if item and item.download_status == expected_status:
            return item
        time.sleep(0.01)
    return queue.get_item(item_id)


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
def temp_storage_dir():
    """Create a temporary storage directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def fake_source():
    """Create a FakeVideoSource for testing."""
    return FakeVideoSource(source_id="fake")


@pytest.fixture
def full_system(temp_db, temp_storage_dir, fake_source):
    """Create a full system with all components using a fake video source."""
    # Config manager
    config_manager = ConfigManager(temp_db)
    config_manager.set("youtube_api_key", "test_key")
    config_manager.set("cache_directory", temp_storage_dir)
    config_manager.set("transition_duration_seconds", "0")

    # Video library with fake source (bypasses real YouTube)
    video_library = VideoLibrary(config_manager)
    video_library.register_source(fake_source)

    # User manager
    user_manager = UserManager(temp_db)
    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
    bob = user_manager.get_or_create_user(BOB_ID, "Bob")
    charlie = user_manager.get_or_create_user(CHARLIE_ID, "Charlie")

    # History manager
    history_manager = HistoryManager(temp_db)

    # Queue manager
    queue_manager = QueueManager(temp_db, video_library=video_library)

    # Streaming controller (mocked)
    mock_streaming = Mock()
    mock_streaming.set_pitch_shift = Mock()
    mock_streaming.load_file = Mock()
    mock_streaming.pause = Mock()
    mock_streaming.resume = Mock()
    mock_streaming.stop = Mock()
    mock_streaming.stop_playback = Mock()
    mock_streaming.set_eos_callback = Mock()
    mock_streaming.get_position = Mock(return_value=0)
    mock_streaming.seek = Mock(return_value=True)
    mock_streaming.show_notification = Mock()
    mock_streaming.display_image = Mock()
    mock_streaming.server = None

    # Playback controller
    playback_controller = PlaybackController(
        queue_manager, mock_streaming, config_manager, history_manager
    )

    yield {
        "config": config_manager,
        "queue": queue_manager,
        "user": user_manager,
        "users": {"alice": alice, "bob": bob, "charlie": charlie},
        "video_library": video_library,
        "fake_source": fake_source,
        "streaming": mock_streaming,
        "playback": playback_controller,
        "history": history_manager,
        "storage_dir": temp_storage_dir,
    }

    # Cleanup
    queue_manager.stop_download_monitor()


def test_add_song_to_queue_and_play(full_system):
    """Test adding a song to queue and playing it."""
    system = full_system

    # Add song to queue
    item_id = system["queue"].add_song(
        user=system["users"]["alice"],
        video_id="fake:test123",
        title="Test Song",
        duration_seconds=180,
        pitch_semitones=2,
    )

    assert item_id == 1

    # Mark as ready
    system["queue"].update_download_status(
        item_id, QueueManager.STATUS_READY, download_path="/fake/path/to/video.mp4"
    )

    # Try to play
    result = system["playback"].play()

    assert result is True
    assert system["playback"].state == PlaybackState.PLAYING
    assert system["playback"].current_song_id is not None
    assert system["playback"].current_song_id == item_id

    # Verify streaming controller was called
    system["streaming"].set_pitch_shift.assert_called_once_with(2)
    system["streaming"].load_file.assert_called_once_with("/fake/path/to/video.mp4")


def test_queue_persistence_across_restarts(temp_db, temp_storage_dir):
    """Test that queue persists when components are recreated."""
    # Create config and managers for first system
    config1 = ConfigManager(temp_db)
    config1.set("cache_directory", temp_storage_dir)

    video_library1 = VideoLibrary(config1)
    fake_source1 = FakeVideoSource()
    video_library1.register_source(fake_source1)

    user1 = UserManager(temp_db)
    alice = user1.get_or_create_user(ALICE_ID, "Alice")
    bob = user1.get_or_create_user(BOB_ID, "Bob")
    queue1 = QueueManager(temp_db, video_library=video_library1)

    # Add songs
    id1 = queue1.add_song(alice, "fake:vid1", "Song 1")
    queue1.add_song(bob, "fake:vid2", "Song 2")
    queue1.update_download_status(id1, QueueManager.STATUS_READY, download_path="/path1")
    queue1.stop_download_monitor()

    # Create second system (simulating restart)
    video_library2 = VideoLibrary(config1)
    fake_source2 = FakeVideoSource()
    video_library2.register_source(fake_source2)

    queue2 = QueueManager(temp_db, video_library=video_library2)

    # Verify queue persisted
    queue = queue2.get_queue()
    assert len(queue) == 2
    assert queue[0].video_id == "fake:vid1"
    assert queue[0].download_status == QueueManager.STATUS_READY
    assert queue[1].video_id == "fake:vid2"
    queue2.stop_download_monitor()


def test_download_and_ready_status(full_system):
    """Test that download monitor updates status correctly."""
    system = full_system

    # Add song
    item_id = system["queue"].add_song(
        user=system["users"]["alice"],
        video_id="fake:downloadtest",
        title="Download Test Song",
    )

    # Song starts as pending
    item = system["queue"].get_item(item_id)
    assert item.download_status == QueueManager.STATUS_PENDING

    # Trigger download processing directly
    system["queue"]._process_download_queue()

    # Wait for async download to complete
    item = wait_for_download(system["queue"], item_id, QueueManager.STATUS_READY)

    # Song should be ready after download
    assert item.download_status == QueueManager.STATUS_READY
    assert item.download_path is not None


def test_download_error_handling(full_system):
    """Test that download errors are handled correctly."""
    system = full_system

    # Configure source to fail downloads
    system["fake_source"].set_fail_downloads(True, "Test error message")

    # Add song
    item_id = system["queue"].add_song(
        user=system["users"]["alice"],
        video_id="fake:failtest",
        title="Fail Test Song",
    )

    # Trigger download processing
    system["queue"]._process_download_queue()

    # Wait for async download to complete
    item = wait_for_download(system["queue"], item_id, QueueManager.STATUS_ERROR)

    # Song should have error status
    assert item.download_status == QueueManager.STATUS_ERROR
    assert item.error_message == "Test error message"


def test_multiple_users_queue_interaction(full_system):
    """Test queue with songs from multiple users."""
    system = full_system
    queue = system["queue"]

    # Each user adds a song
    id1 = queue.add_song(system["users"]["alice"], "fake:alice_song", "Alice's Song")
    id2 = queue.add_song(system["users"]["bob"], "fake:bob_song", "Bob's Song")
    id3 = queue.add_song(system["users"]["charlie"], "fake:charlie_song", "Charlie's Song")

    # Mark all as ready
    for item_id in [id1, id2, id3]:
        queue.update_download_status(
            item_id, QueueManager.STATUS_READY, download_path=f"/path/to/{item_id}.mp4"
        )

    # Verify queue order
    items = queue.get_queue()
    assert len(items) == 3
    assert items[0].user_name == "Alice"
    assert items[1].user_name == "Bob"
    assert items[2].user_name == "Charlie"

    # Remove Bob's song
    queue.remove_song(id2)

    # Verify positions updated
    items = queue.get_queue()
    assert len(items) == 2
    assert items[0].position == 1
    assert items[1].position == 2


def test_pitch_adjustment_flow(full_system):
    """Test pitch adjustment during queue add and playback."""
    system = full_system

    # Add song with pitch adjustment
    item_id = system["queue"].add_song(
        user=system["users"]["alice"],
        video_id="fake:pitch_test",
        title="Pitch Test Song",
        pitch_semitones=3,
    )

    # Mark as ready
    system["queue"].update_download_status(
        item_id, QueueManager.STATUS_READY, download_path="/path/to/video.mp4"
    )

    # Play the song
    system["playback"].play()

    # Verify pitch was set
    system["streaming"].set_pitch_shift.assert_called_with(3)

    # Change pitch during playback
    system["queue"].update_pitch(item_id, 5)

    # Refresh the song in playback (simulate UI re-applying settings)
    item = system["queue"].get_item(item_id)
    assert item.settings.pitch_semitones == 5


def test_storage_cleanup_integration(full_system):
    """Test that storage cleanup is triggered after downloads."""
    system = full_system

    # Add multiple songs
    for i in range(3):
        system["queue"].add_song(
            system["users"]["alice"],
            f"fake:cleanup_test_{i}",
            f"Cleanup Test Song {i}",
        )

    # Get item IDs for waiting
    queue_items = system["queue"].get_queue()
    item_ids = [item.id for item in queue_items]

    # Trigger downloads
    system["queue"]._process_download_queue()

    # Wait for all downloads to complete
    for item_id in item_ids:
        wait_for_download(system["queue"], item_id, QueueManager.STATUS_READY)

    # Verify files were created
    storage_dir = Path(system["storage_dir"])
    fake_dir = storage_dir / "fake"
    assert fake_dir.exists()
    assert len(list(fake_dir.iterdir())) == 3  # 3 video directories


def test_search_to_queue_flow(full_system):
    """Test the flow from search to adding to queue."""
    system = full_system

    # Set up search results
    system["fake_source"].set_search_results(
        [
            {"id": "vid1", "title": "Search Result 1", "duration_seconds": 200},
            {"id": "vid2", "title": "Search Result 2", "duration_seconds": 180},
        ]
    )

    # Search
    results = system["video_library"].search("test query")

    assert len(results) == 2
    assert results[0]["id"] == "fake:vid1"
    assert results[1]["id"] == "fake:vid2"

    # Add first result to queue
    item_id = system["queue"].add_song(
        user=system["users"]["alice"],
        video_id=results[0]["id"],
        title=results[0]["title"],
        duration_seconds=results[0]["duration_seconds"],
    )

    # Trigger download
    system["queue"]._process_download_queue()

    # Wait for download to complete
    item = wait_for_download(system["queue"], item_id, QueueManager.STATUS_READY)

    # Verify it's ready
    assert item.download_status == QueueManager.STATUS_READY


def test_playback_history_recording(full_system):
    """Test that playback history is recorded correctly."""
    system = full_system

    # Add and prepare song
    item_id = system["queue"].add_song(
        user=system["users"]["alice"],
        video_id="fake:history_test",
        title="History Test Song",
        duration_seconds=180,
        pitch_semitones=2,
    )
    system["queue"].update_download_status(
        item_id, QueueManager.STATUS_READY, download_path="/path/to/video.mp4"
    )

    # Play the song
    system["playback"].play()

    # Record history (normally done when song finishes)
    system["history"].record_performance(
        user_id=ALICE_ID,
        user_name="Alice",
        video_id="fake:history_test",
        metadata=system["queue"].get_item(item_id).metadata,
        settings=system["queue"].get_item(item_id).settings,
        played_duration_seconds=180,
        playback_end_position_seconds=180,
        completion_percentage=100.0,
    )

    # Verify history was recorded
    history = system["history"].get_user_history(ALICE_ID)
    assert len(history) == 1
    assert history[0].video_id == "fake:history_test"
    assert history[0].settings.pitch_semitones == 2


def test_video_availability_check(full_system):
    """Test checking if a video is available."""
    system = full_system
    storage_dir = Path(system["storage_dir"])

    # Video not downloaded yet
    assert system["video_library"].is_available("fake:newvideo") is False

    # Create video directory and file
    video_dir = storage_dir / "fake" / "newvideo"
    video_dir.mkdir(parents=True)
    (video_dir / "video.mp4").write_bytes(b"test content")

    # Video should now be available
    assert system["video_library"].is_available("fake:newvideo") is True

    # Get path should return the file
    path = system["video_library"].get_path("fake:newvideo")
    assert path is not None
    assert path.name == "video.mp4"
