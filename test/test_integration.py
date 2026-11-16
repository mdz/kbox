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
from kbox.video_library import VideoLibrary, VideoProvider, VideoSearchSource

# Test user IDs
ALICE_ID = "alice-uuid-1234"
BOB_ID = "bob-uuid-5678"
CHARLIE_ID = "charlie-uuid-9012"


class FakeVideoSource(VideoSearchSource):
    """Fake search source for integration testing."""

    def __init__(self, source_id: str = "fake"):
        self._source_id = source_id
        self._configured = True
        self._search_results: List[Dict[str, Any]] = []
        self._video_info: Dict[str, Dict[str, Any]] = {}

    @property
    def source_id(self) -> str:
        return self._source_id

    def is_configured(self) -> bool:
        return self._configured

    def set_search_results(self, results: List[Dict[str, Any]]) -> None:
        self._search_results = results

    def set_video_info(self, video_id: str, info: Dict[str, Any]) -> None:
        self._video_info[video_id] = info

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        return self._search_results[:max_results]

    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        return self._video_info.get(video_id)


class FakeVideoProvider(VideoProvider):
    """Fake content provider for integration testing."""

    def __init__(self):
        self._fail_downloads: bool = False
        self._fail_message: str = "Simulated download error"

    def set_fail_downloads(self, fail: bool, message: str = "Simulated download error") -> None:
        self._fail_downloads = fail
        self._fail_message = message

    def provide(self, video_id: str, output_dir: Path) -> Path:
        if self._fail_downloads:
            raise RuntimeError(self._fail_message)

        output_dir.mkdir(parents=True, exist_ok=True)
        video_file = output_dir / "video.mp4"
        video_file.write_bytes(b"fake video content for " + video_id.encode())
        return video_file


def wait_for_download(queue, item_id, expected_status, timeout=0.5):
    """Wait for a download to reach the expected status."""
    iterations = int(timeout / 0.01)
    for _ in range(iterations):
        item = queue.get_item(item_id)
        if item and item.content_status == expected_status:
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
def fake_provider():
    """Create a FakeVideoProvider for testing."""
    return FakeVideoProvider()


@pytest.fixture
def full_system(temp_db, temp_storage_dir, fake_source, fake_provider):
    """Create a full system with all components using fake video source and provider."""
    config_manager = ConfigManager(temp_db)
    config_manager.set("youtube_api_key", "test_key")
    config_manager.set("cache_directory", temp_storage_dir)
    config_manager.set("transition_duration_seconds", "0")

    video_library = VideoLibrary(config_manager)
    video_library.register_source(fake_source)
    video_library.set_provider(fake_provider)

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
        "fake_provider": fake_provider,
        "streaming": mock_streaming,
        "playback": playback_controller,
        "history": history_manager,
        "storage_dir": temp_storage_dir,
    }

    # Cleanup
    playback_controller.shutdown()
    queue_manager.stop_content_monitor()


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
    system["queue"].update_content_status(
        item_id, QueueManager.STATUS_READY, content_path="/fake/path/to/video.mp4"
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
    queue1.update_content_status(id1, QueueManager.STATUS_READY, content_path="/path1")
    queue1.stop_content_monitor()

    # Create second system (simulating restart)
    video_library2 = VideoLibrary(config1)
    fake_source2 = FakeVideoSource()
    video_library2.register_source(fake_source2)

    queue2 = QueueManager(temp_db, video_library=video_library2)

    # Verify queue persisted
    queue = queue2.get_queue()
    assert len(queue) == 2
    assert queue[0].video_id == "fake:vid1"
    assert queue[0].content_status == QueueManager.STATUS_READY
    assert queue[1].video_id == "fake:vid2"
    queue2.stop_content_monitor()


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
    assert item.content_status == QueueManager.STATUS_PENDING

    # Trigger download processing directly
    system["queue"]._process_pending_content()

    # Wait for async download to complete
    item = wait_for_download(system["queue"], item_id, QueueManager.STATUS_READY)

    # Song should be ready after download
    assert item.content_status == QueueManager.STATUS_READY
    assert item.content_path is not None


def test_download_error_handling(full_system):
    """Test that download errors are handled correctly."""
    system = full_system

    system["fake_provider"].set_fail_downloads(True, "Test error message")

    # Add song
    item_id = system["queue"].add_song(
        user=system["users"]["alice"],
        video_id="fake:failtest",
        title="Fail Test Song",
    )

    # Trigger download processing
    system["queue"]._process_pending_content()

    # Wait for async download to complete
    item = wait_for_download(system["queue"], item_id, QueueManager.STATUS_ERROR)

    # Song should have error status
    assert item.content_status == QueueManager.STATUS_ERROR
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
        queue.update_content_status(
            item_id, QueueManager.STATUS_READY, content_path=f"/path/to/{item_id}.mp4"
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
    system["queue"].update_content_status(
        item_id, QueueManager.STATUS_READY, content_path="/path/to/video.mp4"
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
    system["queue"]._process_pending_content()

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
    system["queue"]._process_pending_content()

    # Wait for download to complete
    item = wait_for_download(system["queue"], item_id, QueueManager.STATUS_READY)

    # Verify it's ready
    assert item.content_status == QueueManager.STATUS_READY


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
    system["queue"].update_content_status(
        item_id, QueueManager.STATUS_READY, content_path="/path/to/video.mp4"
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


def test_end_of_queue_behavior(full_system):
    """Test that when the last song ends, it shows end-of-queue screen and goes to IDLE."""
    system = full_system

    # Add a single song
    item_id = system["queue"].add_song(
        user=system["users"]["alice"],
        video_id="fake:last_song",
        title="Last Song",
        duration_seconds=180,
    )
    system["queue"].update_content_status(
        item_id, QueueManager.STATUS_READY, content_path="/path/to/last.mp4"
    )

    # Play the song
    system["playback"].play()
    assert system["playback"].state == PlaybackState.PLAYING
    assert system["playback"].current_song_id == item_id

    # Simulate song ending (no next song available)
    system["playback"].on_song_end()

    # Should go to IDLE state (end of queue)
    assert system["playback"].state == PlaybackState.IDLE
    assert system["playback"].current_song_id is None
    # Should show end-of-queue screen
    system["streaming"].display_image.assert_called()
    # Should reset pitch
    system["streaming"].set_pitch_shift.assert_any_call(0)
    # Cursor should point to the song that just finished
    assert system["playback"].get_cursor() == item_id


def test_next_song_not_ready_waits_then_auto_starts(full_system):
    """Test that when a song ends and the next isn't downloaded yet, playback
    waits (goes IDLE) and then auto-starts once the download completes."""
    system = full_system

    # Add two songs; only the first is ready
    id1 = system["queue"].add_song(
        user=system["users"]["alice"],
        video_id="fake:song1",
        title="Song 1",
        duration_seconds=120,
    )
    id2 = system["queue"].add_song(
        user=system["users"]["bob"],
        video_id="fake:song2",
        title="Song 2",
        duration_seconds=180,
    )
    system["queue"].update_content_status(
        id1, QueueManager.STATUS_READY, content_path="/path/to/song1.mp4"
    )
    # Song 2 is still pending (not ready)

    # Play song 1
    system["playback"].play()
    assert system["playback"].state == PlaybackState.PLAYING
    assert system["playback"].current_song_id == id1

    # Song 1 ends — next song isn't ready, so playback should go IDLE
    system["playback"].on_song_end()
    assert system["playback"].state == PlaybackState.IDLE
    assert system["playback"].current_song_id is None
    assert system["playback"].get_cursor() == id1

    # Now song 2 finishes downloading
    system["queue"].update_content_status(
        id2, QueueManager.STATUS_READY, content_path="/path/to/song2.mp4"
    )

    # Simulate the monitor's periodic check (normally runs every 2s on a thread)
    system["playback"]._on_monitor_tick()

    # Should have auto-started song 2
    assert system["playback"].state == PlaybackState.PLAYING
    assert system["playback"].current_song_id == id2
    assert system["playback"].get_cursor() == id2


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


# =============================================================================
# Removing a song that is playing, or that playback still refers to
# =============================================================================


def _ready_song(system, user_key, video_id, title):
    """Add a song for a user and mark it downloaded."""
    item_id = system["queue"].add_song(
        user=system["users"][user_key], video_id=video_id, title=title, duration_seconds=180
    )
    system["queue"].update_content_status(
        item_id, QueueManager.STATUS_READY, content_path=f"/path/to/{title}.mp4"
    )
    return item_id


def _start_second_song(system):
    """Alice's song has been sung; Bob's is playing; Charlie's is next.

    The transition is long so that no timer thread starts a song behind the
    test's back: play() during the interstitial starts the pending song.
    """
    playback = system["playback"]
    playback._monitor.request_stop()
    system["config"].set("transition_duration_seconds", "3600")

    alice_id = _ready_song(system, "alice", "fake:a", "Song A")
    bob_id = _ready_song(system, "bob", "fake:b", "Song B")
    charlie_id = _ready_song(system, "charlie", "fake:c", "Song C")

    playback.play()
    system["streaming"].get_position.return_value = 180
    playback.on_song_end()  # Alice finishes; interstitial for Bob
    playback.play()  # skip the wait
    assert playback.current_song_id == bob_id
    system["streaming"].get_position.return_value = 30
    return alice_id, bob_id, charlie_id


def _api_client(system):
    """A TestClient over the real queue and playback controller."""
    from fastapi.testclient import TestClient

    from kbox.favorites import FavoritesManager
    from kbox.web.app import create_app

    app = create_app(
        queue_manager=system["queue"],
        video_library=system["video_library"],
        playback_controller=system["playback"],
        config_manager=system["config"],
        user_manager=system["user"],
        history_manager=system["history"],
        favorites_manager=FavoritesManager(system["queue"].database),
        streaming_controller=system["streaming"],
    )
    return TestClient(app)


def test_singer_removing_their_playing_song_moves_on_to_the_next_singer(full_system):
    """DELETE /api/queue/{id} on the song playing now stops it and moves on.

    Before the fix the song kept playing after its row was deleted, and when
    it ended the controller could not find it in the queue to work out who was
    next, so it fell back to the top of the queue and played Alice's song,
    sung earlier in the night, again.
    """
    system = full_system
    playback = system["playback"]
    alice_id, bob_id, charlie_id = _start_second_song(system)

    client = _api_client(system)
    client.post("/api/users", json={"user_id": BOB_ID, "display_name": "Bob"})
    response = client.delete(f"/api/queue/{bob_id}")
    assert response.status_code == 200

    # Bob's song stops now, rather than playing on to the end
    assert playback.current_song_id is None
    assert playback.state == PlaybackState.TRANSITION
    assert playback._next_song_pending is not None
    assert playback._next_song_pending.id == charlie_id

    # Even if the end-of-song event for it arrives late, nobody replays
    playback.on_song_end()
    playback.play()  # skip the interstitial
    assert playback.current_song_id == charlie_id

    # A song the singer withdrew is not a performance
    assert [h.metadata.title for h in system["history"].get_user_history(BOB_ID)] == []
    assert system["queue"].get_item(bob_id) is None


def test_removing_paused_song_moves_on(full_system):
    """A paused song removed from the queue is stopped, not left paused."""
    system = full_system
    playback = system["playback"]
    _, bob_id, charlie_id = _start_second_song(system)
    playback.pause()

    assert playback.remove_song(bob_id) is True

    system["streaming"].stop_playback.assert_called()
    assert playback.state == PlaybackState.TRANSITION
    assert playback._next_song_pending.id == charlie_id


def test_removing_last_playing_song_shows_end_of_queue(full_system):
    """Removing the playing song with nobody after it goes to the end screen."""
    system = full_system
    playback = system["playback"]
    _, bob_id, charlie_id = _start_second_song(system)
    playback.on_song_end()
    playback.play()
    assert playback.current_song_id == charlie_id

    assert playback.remove_song(charlie_id) is True

    assert playback.state == PlaybackState.IDLE
    assert playback.current_song_id is None
    assert playback.get_cursor() == bob_id


def test_removing_last_sung_song_does_not_replay_the_queue(full_system):
    """Removing the song the cursor points at keeps already-sung songs sung.

    Bob's song was the last one sung (the queue ran out). If he then removes
    it, the next song added must play next, not Alice's from the top.
    """
    system = full_system
    playback = system["playback"]
    alice_id, bob_id, charlie_id = _start_second_song(system)
    # Charlie withdraws before singing; Bob finishes; queue exhausted.
    assert playback.remove_song(charlie_id) is True
    playback.on_song_end()
    assert playback.state == PlaybackState.IDLE
    assert playback.get_cursor() == bob_id

    assert playback.remove_song(bob_id) is True
    dave_id = _ready_song(system, "charlie", "fake:d", "Song D")
    playback._on_monitor_tick()

    assert playback.current_song_id == dave_id


def test_removing_the_song_on_the_interstitial_retargets_it(full_system):
    """Removing the song the "up next" screen announces announces the one after."""
    system = full_system
    playback = system["playback"]
    alice_id, bob_id, charlie_id = _start_second_song(system)
    dave_id = _ready_song(system, "alice", "fake:d", "Song D")
    playback.on_song_end()  # Bob finishes; interstitial for Charlie
    assert playback._next_song_pending.id == charlie_id

    assert playback.remove_song(charlie_id) is True

    assert playback.state == PlaybackState.TRANSITION
    assert playback._next_song_pending.id == dave_id
    playback.play()
    assert playback.current_song_id == dave_id


def _bob_stops_his_song(system):
    """Bob stops his song from his phone; Charlie is up next, then Alice again."""
    alice_id, bob_id, charlie_id = _start_second_song(system)
    alice_again_id = _ready_song(system, "alice", "fake:a2", "Song A2")
    client = _api_client(system)
    client.post("/api/users", json={"user_id": BOB_ID, "display_name": "Bob"})
    response = client.post("/api/playback/stop-song", json={"item_id": bob_id})
    assert response.json() == {"status": "stopped"}
    return client, bob_id, charlie_id, alice_again_id


def test_stopped_song_never_comes_back_by_itself(full_system):
    """Bob may be embarrassed: the track he bailed out of must not reappear."""
    system = full_system
    playback = system["playback"]
    _, bob_id, charlie_id, alice_again_id = _bob_stops_his_song(system)

    playback.play()  # skip the interstitial
    assert playback.current_song_id == charlie_id
    playback.on_song_end()
    playback.play()
    assert playback.current_song_id == alice_again_id
    assert [h.metadata.title for h in system["history"].get_user_history(BOB_ID)] == []


def test_another_go_comes_right_after_the_next_singer(full_system):
    system = full_system
    playback = system["playback"]
    client, bob_id, charlie_id, alice_again_id = _bob_stops_his_song(system)

    # While Charlie's "up next" screen is showing, Bob asks for another go
    response = client.post("/api/playback/another-go", json={"item_id": bob_id})
    assert response.status_code == 200

    playback.play()  # skip the interstitial
    assert playback.current_song_id == charlie_id
    playback.on_song_end()
    playback.play()
    assert playback.current_song_id == bob_id  # before Alice's second song

    # Only once: no further jumps
    response = client.post("/api/playback/another-go", json={"item_id": bob_id})
    assert response.status_code == 409


def test_another_go_with_a_different_song(full_system):
    """Replace the stopped item first, then ask for another go."""
    system = full_system
    playback = system["playback"]
    client, bob_id, charlie_id, _ = _bob_stops_his_song(system)

    response = client.post(
        f"/api/queue/{bob_id}/replace",
        json={"video_id": "fake:b2", "title": "Song B2", "duration_seconds": 200},
    )
    assert response.status_code == 200
    assert client.post("/api/playback/another-go", json={"item_id": bob_id}).status_code == 200
    system["queue"].update_content_status(
        bob_id, QueueManager.STATUS_READY, content_path="/path/to/Song B2.mp4"
    )

    playback.play()
    playback.on_song_end()  # Charlie finishes
    playback.play()
    assert playback.current_song_id == bob_id
    assert system["queue"].get_item(bob_id).metadata.title == "Song B2"
