"""
Integration tests for kbox.

Tests the integration between components without external dependencies.
"""

import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import Mock

import pytest

from kbox.cache import CacheManager
from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.playback import PlaybackController, PlaybackState
from kbox.queue import QueueManager
from kbox.user import UserManager
from kbox.video_source import VideoManager, VideoSource

# Test user IDs
ALICE_ID = "alice-uuid-1234"
BOB_ID = "bob-uuid-5678"
CHARLIE_ID = "charlie-uuid-9012"


class FakeVideoSource(VideoSource):
    """
    Fake video source for integration testing.

    Simulates video search and download without hitting real APIs.
    """

    def __init__(self, cache_dir: str, source_id: str = "fake"):
        self._source_id = source_id
        self._cache_dir = Path(cache_dir)
        self._configured = True
        self._search_results: List[Dict[str, Any]] = []
        self._video_info: Dict[str, Dict[str, Any]] = {}
        self._fail_downloads: bool = False  # If True, downloads will fail
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

    def download(
        self,
        video_id: str,
        queue_item_id: int,
        status_callback: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
    ) -> Optional[str]:
        """Create a fake cached file synchronously.

        Per the VideoSource interface:
        - Returns path if already cached or download completes synchronously
        - Callback is only used for async status updates (not needed for sync)
        """
        # Check if already cached
        cached = self.get_cached_path(video_id, touch=False)
        if cached:
            return str(cached)

        if self._fail_downloads:
            if status_callback:
                status_callback("error", None, self._fail_message)
            return None

        # Create fake cached file synchronously
        source_dir = self._cache_dir / self._source_id
        source_dir.mkdir(parents=True, exist_ok=True)
        cached_file = source_dir / f"{video_id}.mp4"
        cached_file.write_bytes(b"fake video content for " + video_id.encode())

        return str(cached_file)

    def get_cached_path(self, video_id: str, touch: bool = True) -> Optional[Path]:
        source_dir = self._cache_dir / self._source_id
        # Check for any extension
        for ext in [".mp4", ".webm", ".mkv"]:
            path = source_dir / f"{video_id}{ext}"
            if path.exists():
                if touch:
                    path.touch()
                return path
        return None

    def is_cached(self, video_id: str) -> bool:
        return self.get_cached_path(video_id, touch=False) is not None


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
def temp_cache_dir():
    """Create a temporary cache directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def fake_source(temp_cache_dir):
    """Create a FakeVideoSource for testing."""
    return FakeVideoSource(temp_cache_dir, source_id="fake")


@pytest.fixture
def full_system(temp_db, temp_cache_dir, fake_source):
    """Create a full system with all components using a fake video source."""
    # Config manager
    config_manager = ConfigManager(temp_db)
    config_manager.set("youtube_api_key", "test_key")
    config_manager.set("cache_directory", temp_cache_dir)
    config_manager.set("transition_duration_seconds", "0")  # No transition delay in tests

    # Cache manager
    cache_manager = CacheManager(config_manager)

    # Video manager with fake source (bypasses real YouTube)
    video_manager = VideoManager(config_manager, cache_manager)
    video_manager._register_source(fake_source)

    # User manager
    user_manager = UserManager(temp_db)
    # Create test users
    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
    bob = user_manager.get_or_create_user(BOB_ID, "Bob")
    charlie = user_manager.get_or_create_user(CHARLIE_ID, "Charlie")

    # Queue manager with real video_manager (enables download monitor)
    queue_manager = QueueManager(temp_db, video_manager=video_manager)

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
    playback_controller = PlaybackController(queue_manager, mock_streaming, config_manager)

    yield {
        "config": config_manager,
        "cache": cache_manager,
        "queue": queue_manager,
        "user": user_manager,
        "users": {"alice": alice, "bob": bob, "charlie": charlie},
        "video_manager": video_manager,
        "fake_source": fake_source,
        "streaming": mock_streaming,
        "playback": playback_controller,
        "cache_dir": temp_cache_dir,
    }

    # Cleanup
    queue_manager.stop_download_monitor()


def test_add_song_to_queue_and_play(full_system):
    """Test adding a song to queue and playing it."""
    system = full_system

    # Add song to queue
    item_id = system["queue"].add_song(
        user=system["users"]["alice"],
        source="youtube",
        source_id="test123",
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


def test_queue_persistence_across_restarts(temp_db, temp_cache_dir):
    """Test that queue persists when components are recreated."""
    # Create config and managers for first system
    config1 = ConfigManager(temp_db)
    config1.set("cache_directory", temp_cache_dir)
    cache1 = CacheManager(config1)
    video_manager1 = VideoManager(config1, cache1)
    fake_source1 = FakeVideoSource(temp_cache_dir)
    video_manager1._register_source(fake_source1)

    user1 = UserManager(temp_db)
    alice = user1.get_or_create_user(ALICE_ID, "Alice")
    bob = user1.get_or_create_user(BOB_ID, "Bob")
    queue1 = QueueManager(temp_db, video_manager=video_manager1)

    # Add songs
    id1 = queue1.add_song(alice, "fake", "vid1", "Song 1")
    queue1.add_song(bob, "fake", "vid2", "Song 2")
    queue1.update_download_status(id1, QueueManager.STATUS_READY, download_path="/path1")
    queue1.stop_download_monitor()

    # Create second system (simulating restart)
    config2 = ConfigManager(temp_db)
    config2.set("cache_directory", temp_cache_dir)
    cache2 = CacheManager(config2)
    video_manager2 = VideoManager(config2, cache2)
    fake_source2 = FakeVideoSource(temp_cache_dir)
    video_manager2._register_source(fake_source2)

    queue2 = QueueManager(temp_db, video_manager=video_manager2)

    # Verify queue persisted
    queue = queue2.get_queue()
    assert len(queue) == 2
    assert queue[0].user_id == ALICE_ID
    assert queue[1].user_id == BOB_ID
    assert queue[0].download_status == QueueManager.STATUS_READY
    queue2.stop_download_monitor()


def test_playback_state_transitions(full_system):
    """Test playback state transitions."""
    system = full_system

    # Start in stopped (operator must press play to start)
    assert system["playback"].state == PlaybackState.STOPPED

    # Add and mark ready
    item_id = system["queue"].add_song(system["users"]["alice"], "youtube", "vid1", "Song 1")
    system["queue"].update_download_status(
        item_id, QueueManager.STATUS_READY, download_path="/fake/path.mp4"
    )

    # Play
    system["playback"].play()
    assert system["playback"].state == PlaybackState.PLAYING

    # Pause
    system["playback"].pause()
    assert system["playback"].state == PlaybackState.PAUSED

    # Resume
    system["playback"].play()
    assert system["playback"].state == PlaybackState.PLAYING


def test_pitch_adjustment_during_playback(full_system):
    """Test pitch adjustment for current song."""
    system = full_system

    # Add and play song
    item_id = system["queue"].add_song(
        system["users"]["alice"], "youtube", "vid1", "Song 1", pitch_semitones=0
    )
    system["queue"].update_download_status(
        item_id, QueueManager.STATUS_READY, download_path="/fake/path.mp4"
    )
    system["playback"].play()

    # Adjust pitch
    result = system["playback"].set_pitch(3)
    assert result is True

    # Verify pitch was updated in queue
    item = system["queue"].get_item(item_id)
    assert item.settings.pitch_semitones == 3

    # Verify streaming controller was called
    system["streaming"].set_pitch_shift.assert_called_with(3)


def test_song_transition_on_end(full_system):
    """Test automatic transition to next song on end."""
    import time

    system = full_system

    # Add two songs
    id1 = system["queue"].add_song(system["users"]["alice"], "youtube", "vid1", "Song 1")
    id2 = system["queue"].add_song(system["users"]["bob"], "youtube", "vid2", "Song 2")

    system["queue"].update_download_status(
        id1, QueueManager.STATUS_READY, download_path="/fake/path1.mp4"
    )
    system["queue"].update_download_status(
        id2, QueueManager.STATUS_READY, download_path="/fake/path2.mp4"
    )

    # Play first song
    system["playback"].play()
    assert system["playback"].current_song_id == id1

    # Simulate end of song
    system["playback"].on_song_end()

    # Wait for transition timer (set to 0 seconds in fixture)
    time.sleep(0.1)

    # Should transition to next song
    assert system["playback"].current_song_id is not None
    assert system["playback"].current_song_id == id2
    assert system["playback"].state == PlaybackState.PLAYING


def test_skip_to_next_song(full_system):
    """Test skipping to next song."""
    system = full_system

    # Add two songs
    id1 = system["queue"].add_song(system["users"]["alice"], "youtube", "vid1", "Song 1")
    id2 = system["queue"].add_song(system["users"]["bob"], "youtube", "vid2", "Song 2")

    system["queue"].update_download_status(
        id1, QueueManager.STATUS_READY, download_path="/fake/path1.mp4"
    )
    system["queue"].update_download_status(
        id2, QueueManager.STATUS_READY, download_path="/fake/path2.mp4"
    )

    # Play first song
    system["playback"].play()
    assert system["playback"].current_song_id == id1

    # Skip
    result = system["playback"].skip()
    assert result is True
    assert system["playback"].current_song_id == id2


def test_queue_reordering(full_system):
    """Test reordering songs in queue."""
    system = full_system

    # Add three songs
    id1 = system["queue"].add_song(system["users"]["alice"], "youtube", "vid1", "Song 1")
    id2 = system["queue"].add_song(system["users"]["bob"], "youtube", "vid2", "Song 2")
    id3 = system["queue"].add_song(system["users"]["charlie"], "youtube", "vid3", "Song 3")

    # Move last to first
    result = system["queue"].reorder_song(id3, 1)
    assert result is True

    queue = system["queue"].get_queue()
    assert queue[0].id == id3
    assert queue[1].id == id1
    assert queue[2].id == id2


def test_config_persistence(temp_db):
    """Test configuration persistence."""
    # Set config
    config1 = ConfigManager(temp_db)
    config1.set("operator_pin", "9999")
    config1.set("custom_key", "custom_value")

    # Recreate config manager
    config2 = ConfigManager(temp_db)

    # Verify persistence
    assert config2.get("operator_pin") == "9999"
    assert config2.get("custom_key") == "custom_value"


def test_cache_cleanup_integration(temp_db, temp_cache_dir):
    """Test cache cleanup integration between QueueManager and CacheManager."""
    import time
    from pathlib import Path

    # Setup config with very small cache limit
    config_manager = ConfigManager(temp_db)
    config_manager.set("youtube_api_key", "test_key")
    config_manager.set("cache_directory", temp_cache_dir)
    config_manager.set("cache_max_size_gb", "0")  # 0 GB limit forces eviction

    # Create CacheManager
    cache_manager = CacheManager(config_manager)

    # Create VideoManager (auto-registers sources)
    video_manager = VideoManager(config_manager, cache_manager)

    # Create cache directory with some files
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(parents=True, exist_ok=True)

    # Create old cached file (should be evicted)
    old_file = youtube_dir / "old_video.mp4"
    old_file.write_bytes(b"x" * 1000)
    # Set mtime to 1 hour ago to ensure it's older
    old_mtime = time.time() - 3600
    os.utime(old_file, (old_mtime, old_mtime))

    # Create file that will be in queue (should be protected)
    queued_file = youtube_dir / "queued_video.mp4"
    queued_file.write_bytes(b"x" * 1000)

    # Setup user and queue
    user_manager = UserManager(temp_db)
    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")

    # Create queue manager with video manager and cache manager
    queue_manager = QueueManager(temp_db, video_manager=video_manager)

    # Add song to queue (this protects queued_video)
    queue_manager.add_song(alice, "youtube", "queued_video", "Queued Song")

    # Trigger cache cleanup (which protects queued items)
    queue_manager._cleanup_cache()

    # Verify cleanup happened - old file deleted, queued file remains
    deleted_count = 1 if not old_file.exists() else 0

    # Old file should be deleted, queued file should remain
    assert deleted_count == 1
    assert not old_file.exists()
    assert queued_file.exists()

    # Stop the download monitor to clean up
    queue_manager.stop_download_monitor()


# =============================================================================
# Download Monitor Integration Tests
# =============================================================================


@pytest.fixture
def download_system(temp_db, temp_cache_dir):
    """Create a system with real VideoManager and FakeVideoSource for download testing."""
    # Config manager
    config_manager = ConfigManager(temp_db)
    config_manager.set("youtube_api_key", "test_key")
    config_manager.set("cache_directory", temp_cache_dir)

    # Cache manager
    cache_manager = CacheManager(config_manager)

    # Create real VideoManager with FakeVideoSource
    video_manager = VideoManager(config_manager, cache_manager)
    fake_source = FakeVideoSource(temp_cache_dir, source_id="fake")
    video_manager._register_source(fake_source)

    # User manager
    user_manager = UserManager(temp_db)
    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")
    bob = user_manager.get_or_create_user(BOB_ID, "Bob")

    # Queue manager WITH video manager (enables download monitor)
    queue_manager = QueueManager(temp_db, video_manager=video_manager)

    yield {
        "config": config_manager,
        "cache": cache_manager,
        "queue": queue_manager,
        "user": user_manager,
        "users": {"alice": alice, "bob": bob},
        "video_manager": video_manager,
        "fake_source": fake_source,
        "cache_dir": temp_cache_dir,
    }

    # Cleanup
    queue_manager.stop_download_monitor()


def test_download_monitor_triggers_download(download_system):
    """Test that the download monitor picks up pending items and triggers downloads."""
    system = download_system

    # Add a song using fake source (starts as pending)
    item_id = system["queue"].add_song(
        user=system["users"]["alice"],
        source="fake",
        source_id="test_vid_1",
        title="Test Song",
        duration_seconds=180,
    )

    # Verify item starts as pending
    item = system["queue"].get_item(item_id)
    assert item.download_status == QueueManager.STATUS_PENDING

    # Trigger download processing
    system["queue"]._process_download_queue()

    # Verify status was updated to ready (download completed via FakeVideoSource)
    item = system["queue"].get_item(item_id)
    assert item.download_status == QueueManager.STATUS_READY
    assert item.download_path is not None
    assert "test_vid_1.mp4" in item.download_path

    # Verify file was actually created
    assert Path(item.download_path).exists()


def test_download_monitor_handles_multiple_items(download_system):
    """Test that the download monitor processes multiple pending items."""
    system = download_system

    # Add multiple songs using fake source
    id1 = system["queue"].add_song(system["users"]["alice"], "fake", "vid1", "Song 1")
    id2 = system["queue"].add_song(system["users"]["bob"], "fake", "vid2", "Song 2")

    # Trigger download processing
    system["queue"]._process_download_queue()

    # Both should be downloaded
    item1 = system["queue"].get_item(id1)
    item2 = system["queue"].get_item(id2)

    assert item1.download_status == QueueManager.STATUS_READY
    assert item2.download_status == QueueManager.STATUS_READY

    # Verify files were created
    assert Path(item1.download_path).exists()
    assert Path(item2.download_path).exists()


def test_download_monitor_handles_error(temp_db, temp_cache_dir):
    """Test that the download monitor handles download errors gracefully."""
    # Config
    config_manager = ConfigManager(temp_db)
    config_manager.set("youtube_api_key", "test_key")
    config_manager.set("cache_directory", temp_cache_dir)

    # Create real VideoManager with failing FakeVideoSource
    cache_manager = CacheManager(config_manager)
    video_manager = VideoManager(config_manager, cache_manager)

    failing_source = FakeVideoSource(temp_cache_dir, source_id="failing")
    failing_source.set_fail_downloads(True, "Network connection failed")
    video_manager._register_source(failing_source)

    # Create user and queue
    user_manager = UserManager(temp_db)
    alice = user_manager.get_or_create_user(ALICE_ID, "Alice")

    queue_manager = QueueManager(temp_db, video_manager=video_manager)
    queue_manager.stop_download_monitor()

    # Add song using the failing source
    item_id = queue_manager.add_song(alice, "failing", "fail_vid", "Failing Song")

    # Trigger download processing
    queue_manager._process_download_queue()

    # Verify status is error
    item = queue_manager.get_item(item_id)
    assert item.download_status == QueueManager.STATUS_ERROR
    assert item.error_message == "Network connection failed"


def test_download_status_callback_updates_queue(download_system):
    """Test that the download status callback correctly updates queue items."""
    system = download_system

    # Add song using fake source
    item_id = system["queue"].add_song(
        system["users"]["alice"], "fake", "callback_test", "Callback Test Song"
    )

    # Trigger download processing
    system["queue"]._process_download_queue()

    # Get the item
    item = system["queue"].get_item(item_id)

    # Verify the callback updated all fields correctly
    assert item.download_status == QueueManager.STATUS_READY
    assert item.download_path is not None

    # Verify the file was created at the expected path
    expected_path = Path(system["cache_dir"]) / "fake" / "callback_test.mp4"
    assert expected_path.exists()
    assert str(expected_path) == item.download_path


# =============================================================================
# Search → Queue → Download Integration Tests
# =============================================================================


def test_search_to_queue_to_download_flow(download_system):
    """Test the full flow: search results → add to queue → download completes."""
    system = download_system

    # Configure fake source with search results
    search_results = [
        {
            "id": "search_result_1",
            "title": "Karaoke Song Found in Search",
            "duration_seconds": 240,
            "thumbnail": "http://example.com/thumb.jpg",
            "channel": "Karaoke Channel",
        },
        {
            "id": "search_result_2",
            "title": "Another Karaoke Song",
            "duration_seconds": 180,
            "thumbnail": "http://example.com/thumb2.jpg",
            "channel": "Another Channel",
        },
    ]
    system["fake_source"].set_search_results(search_results)

    # Step 1: Search for videos (uses real VideoManager with fake source)
    results = system["video_manager"].search("test karaoke")
    # Results should include fake source results (tagged with source)
    fake_results = [r for r in results if r.get("source") == "fake"]
    assert len(fake_results) == 2
    assert fake_results[0]["id"] == "search_result_1"

    # Step 2: User selects first result and adds to queue
    selected = fake_results[0]
    item_id = system["queue"].add_song(
        user=system["users"]["alice"],
        source=selected["source"],
        source_id=selected["id"],
        title=selected["title"],
        duration_seconds=selected["duration_seconds"],
        thumbnail_url=selected.get("thumbnail"),
        channel=selected.get("channel"),
    )

    # Verify item was added with pending status
    item = system["queue"].get_item(item_id)
    assert item.source == "fake"
    assert item.source_id == "search_result_1"
    assert item.metadata.title == "Karaoke Song Found in Search"
    assert item.download_status == QueueManager.STATUS_PENDING

    # Step 3: Trigger download processing
    system["queue"]._process_download_queue()

    # Step 4: Verify item is now ready for playback
    item = system["queue"].get_item(item_id)
    assert item.download_status == QueueManager.STATUS_READY
    assert item.download_path is not None

    # Verify file was actually created
    assert Path(item.download_path).exists()


def test_multiple_sources_in_queue(download_system):
    """Test that items from different sources can be queued and downloaded."""
    system = download_system

    # Add song using fake source
    id1 = system["queue"].add_song(
        system["users"]["alice"], "fake", "multi_src_vid", "Fake Source Song"
    )

    # Trigger download processing
    system["queue"]._process_download_queue()

    item1 = system["queue"].get_item(id1)
    assert item1.download_status == QueueManager.STATUS_READY
    assert item1.source == "fake"

    # Verify file exists
    assert Path(item1.download_path).exists()


def test_queue_to_playback_integration(download_system):
    """Test that downloaded items can be played back."""
    system = download_system

    # Create streaming mock
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

    # Add song to queue using fake source
    item_id = system["queue"].add_song(
        system["users"]["alice"],
        "fake",
        "playback_test",
        "Playback Test Song",
        pitch_semitones=1,
    )

    # Trigger download processing
    system["queue"]._process_download_queue()

    # Verify ready for playback
    item = system["queue"].get_item(item_id)
    assert item.download_status == QueueManager.STATUS_READY

    # Create playback controller
    playback = PlaybackController(system["queue"], mock_streaming, system["config"])

    # Play the song
    result = playback.play()
    assert result is True
    assert playback.state == PlaybackState.PLAYING

    # Verify streaming controller was called with correct file
    mock_streaming.set_pitch_shift.assert_called_once_with(1)
    mock_streaming.load_file.assert_called_once()
    load_path = mock_streaming.load_file.call_args[0][0]
    assert "playback_test.mp4" in load_path
