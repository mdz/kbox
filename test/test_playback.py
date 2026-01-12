"""
Unit tests for PlaybackController.

Uses mocks for dependencies.
"""

from unittest.mock import Mock

import pytest

from kbox.models import QueueItem, SongMetadata, SongSettings
from kbox.playback import PlaybackController, PlaybackState
from kbox.queue import QueueManager


@pytest.fixture
def mock_queue_manager():
    """Create a mock QueueManager."""
    qm = Mock(spec=QueueManager)
    # Configure get_item to return None by default (tests override as needed)
    qm.get_item.return_value = None
    # Configure get_ready_song_at_offset to return None by default
    qm.get_ready_song_at_offset.return_value = None
    return qm


@pytest.fixture
def mock_streaming_controller():
    """Create a mock StreamingController."""
    controller = Mock()
    controller.set_pitch_shift = Mock()
    controller.load_file = Mock()
    controller.pause = Mock()
    controller.resume = Mock()
    controller.stop = Mock()
    controller.stop_playback = Mock()  # Returns pipeline to idle without destroying it
    controller.set_eos_callback = Mock()
    controller.get_position = Mock(return_value=0)
    controller.seek = Mock(return_value=True)
    controller.show_notification = Mock()
    # Static image display (for interstitials)
    controller.display_image = Mock()
    controller.server = None  # No server in tests
    return controller


@pytest.fixture
def mock_config_manager():
    """Create a mock ConfigManager."""
    config = Mock()
    # Configure get() to return None for unknown keys, or specific values
    config.get.return_value = None
    return config


def create_mock_queue_item(
    id=1,
    position=1,
    user_id="test-user",
    user_name="Test User",
    video_id="youtube:test123",
    title="Test Song",
    duration_seconds=180,
    thumbnail_url=None,
    channel=None,
    pitch_semitones=0,
    download_status="ready",
    download_path="/path/to/video.mp4",
    error_message=None,
    played_at=None,
):
    """Helper to create a mock QueueItem for testing."""
    metadata = SongMetadata(
        title=title, duration_seconds=duration_seconds, thumbnail_url=thumbnail_url, channel=channel
    )
    settings = SongSettings(pitch_semitones=pitch_semitones)
    return QueueItem(
        id=id,
        position=position,
        user_id=user_id,
        user_name=user_name,
        video_id=video_id,
        metadata=metadata,
        settings=settings,
        download_status=download_status,
        download_path=download_path,
        error_message=error_message,
        played_at=played_at,
    )


@pytest.fixture
def playback_controller(mock_queue_manager, mock_streaming_controller, mock_config_manager):
    """Create a PlaybackController instance."""
    # Mock get_queue to return empty list to avoid thread issues
    mock_queue_manager.get_queue.return_value = []
    mock_queue_manager.database = Mock()
    mock_queue_manager.database.get_connection.return_value.cursor.return_value.fetchone.return_value = None

    controller = PlaybackController(
        mock_queue_manager, mock_streaming_controller, mock_config_manager
    )
    # Stop monitor thread to avoid interference in tests
    controller._monitoring = False
    return controller


def test_initial_state(playback_controller):
    """Test initial playback state."""
    assert playback_controller.state == PlaybackState.STOPPED
    assert playback_controller.current_song_id is None


def test_play_no_ready_songs(playback_controller, mock_queue_manager):
    """Test play when no ready songs in queue."""
    mock_queue_manager.get_ready_song_at_offset.return_value = None

    result = playback_controller.play()

    assert result is False
    assert playback_controller.state == PlaybackState.IDLE


def test_play_with_ready_song(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test playing a ready song."""
    mock_song = create_mock_queue_item(
        id=1,
        title="Test Song",
        user_name="Alice",
        download_path="/path/to/video.mp4",
        pitch_semitones=2,
        download_status=QueueManager.STATUS_READY,
    )
    # Mock get_ready_song_at_offset to return the song (used by _load_and_play_next)
    mock_queue_manager.get_ready_song_at_offset.return_value = mock_song

    result = playback_controller.play()

    assert result is True
    assert playback_controller.state == PlaybackState.PLAYING
    assert playback_controller.current_song_id == 1
    mock_streaming_controller.set_pitch_shift.assert_called_once_with(2)
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/video.mp4")
    # Song should NOT be marked as played when it starts - only when it finishes
    mock_queue_manager.mark_played.assert_not_called()


def test_play_no_download_path(playback_controller, mock_queue_manager):
    """Test play when song has no download path."""
    mock_song = create_mock_queue_item(
        id=1, title="Test Song", user_name="Alice", download_path=None
    )
    mock_queue_manager.get_ready_song_at_offset.return_value = mock_song

    result = playback_controller.play()

    assert result is False
    assert playback_controller.state == PlaybackState.IDLE


def test_pause(playback_controller, mock_streaming_controller):
    """Test pausing playback."""
    playback_controller.state = PlaybackState.PLAYING

    result = playback_controller.pause()

    assert result is True
    assert playback_controller.state == PlaybackState.PAUSED
    mock_streaming_controller.pause.assert_called_once()


def test_pause_not_playing(playback_controller):
    """Test pause when not playing."""
    playback_controller.state = PlaybackState.IDLE

    result = playback_controller.pause()

    assert result is False


def test_resume(playback_controller, mock_streaming_controller):
    """Test resuming playback."""
    playback_controller.state = PlaybackState.PAUSED

    result = playback_controller.play()  # play() handles resume

    assert result is True
    assert playback_controller.state == PlaybackState.PLAYING
    mock_streaming_controller.resume.assert_called_once()


def test_skip(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test skipping to next song."""
    current_song = create_mock_queue_item(
        id=1,
        title="Current Song",
        user_name="Alice",
        video_id="youtube:abc123",
        duration_seconds=180,
        pitch_semitones=0,
    )
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    # Mock get_item to return current song data
    mock_queue_manager.get_item.return_value = current_song

    mock_next_song = create_mock_queue_item(
        id=2,
        title="Next Song",
        user_name="Bob",
        download_path="/path/to/next.mp4",
        pitch_semitones=0,
        download_status=QueueManager.STATUS_READY,
    )
    # Mock get_ready_song_at_offset to return the next song
    mock_queue_manager.get_ready_song_at_offset.return_value = mock_next_song

    result = playback_controller.skip()

    assert result is True
    # IMPORTANT: skip() must call stop_playback() (returns to idle) NOT stop() (destroys pipeline)
    mock_streaming_controller.stop_playback.assert_called_once()
    mock_streaming_controller.stop.assert_not_called()
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/next.mp4")


def test_skip_no_next_song(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test skip when no next song available.

    When there's no next song, skip() should return False without stopping
    the current song, so playback continues.
    """
    current_song = create_mock_queue_item(
        id=1,
        title="Current Song",
        user_name="Alice",
        video_id="youtube:abc123",
        duration_seconds=180,
        pitch_semitones=0,
        download_status=QueueManager.STATUS_READY,
    )
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    # Mock get_item to return current song data
    mock_queue_manager.get_item.return_value = current_song
    # Mock get_ready_song_at_offset to return None (no next song)
    mock_queue_manager.get_ready_song_at_offset.return_value = None

    result = playback_controller.skip()

    assert result is False
    # State should remain PLAYING - we don't stop current song if there's no next
    assert playback_controller.state == PlaybackState.PLAYING
    # Should not have stopped playback
    mock_streaming_controller.stop_playback.assert_not_called()


def test_set_pitch(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test setting pitch for current song."""
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    result = playback_controller.set_pitch(3)

    assert result is True
    mock_queue_manager.update_pitch.assert_called_once_with(1, 3)
    mock_streaming_controller.set_pitch_shift.assert_called_once_with(3)


def test_set_pitch_no_current_song(playback_controller):
    """Test setting pitch when no current song."""
    playback_controller.current_song_id = None

    result = playback_controller.set_pitch(3)

    assert result is False


def test_on_song_end(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test handling end of song."""
    current_song = create_mock_queue_item(
        id=1,
        title="Song 1",
        user_name="Alice",
        video_id="youtube:abc123",
        duration_seconds=180,
        pitch_semitones=0,
    )
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    # Mock get_item to return current song data
    mock_queue_manager.get_item.return_value = current_song

    mock_next_song = create_mock_queue_item(
        id=2,
        title="Song 2",
        user_name="Bob",
        download_path="/path/to/next.mp4",
        pitch_semitones=0,
        download_status=QueueManager.STATUS_READY,
    )
    # Mock get_ready_song_at_offset to return the next song
    mock_queue_manager.get_ready_song_at_offset.return_value = mock_next_song

    playback_controller.on_song_end()

    # Should mark current song as played
    mock_queue_manager.mark_played.assert_called_once_with(1)
    # Should call get_ready_song_at_offset with the finished song's ID and offset +1
    mock_queue_manager.get_ready_song_at_offset.assert_called_once_with(1, 1)
    # Should reset pitch
    mock_streaming_controller.set_pitch_shift.assert_any_call(0)
    # Should display transition interstitial image
    mock_streaming_controller.display_image.assert_called_once()
    # State should be TRANSITION (waiting for timer)
    assert playback_controller.state == PlaybackState.TRANSITION
    # Next song should be pending
    assert playback_controller._next_song_pending == mock_next_song


def test_on_song_end_no_next(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test end of song when no next song."""
    current_song = create_mock_queue_item(
        id=1,
        title="Song 1",
        user_name="Alice",
        video_id="youtube:abc123",
        duration_seconds=180,
        pitch_semitones=0,
    )
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    # Mock get_item to return current song data
    mock_queue_manager.get_item.return_value = current_song
    # Mock get_ready_song_at_offset to return None (no next song)
    mock_queue_manager.get_ready_song_at_offset.return_value = None

    playback_controller.on_song_end()

    # Should mark current song as played
    mock_queue_manager.mark_played.assert_called_once_with(1)
    # Should call get_ready_song_at_offset with the finished song's ID and offset +1
    mock_queue_manager.get_ready_song_at_offset.assert_called_once_with(1, 1)
    assert playback_controller.current_song_id is None
    assert playback_controller.state == PlaybackState.IDLE
    mock_streaming_controller.set_pitch_shift.assert_called_once_with(0)
    # Should display end-of-queue interstitial image
    mock_streaming_controller.display_image.assert_called_once()


def test_get_status(playback_controller, mock_queue_manager):
    """Test getting playback status returns properly serialized dict."""
    song = create_mock_queue_item(id=1, title="Test Song")
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    # Mock get_item to return song data
    mock_queue_manager.get_item.return_value = song

    status = playback_controller.get_status()

    assert status["state"] == "playing"
    # current_song should be a dict (for JSON serialization), not a QueueItem object
    assert isinstance(status["current_song"], dict)
    assert status["current_song"]["id"] == 1
    assert status["current_song"]["title"] == "Test Song"


def test_jump_to_song_while_playing(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test jumping to a song while another song is playing.

    This is a regression test for the bug where jump_to_song called stop()
    instead of stop_playback(), which destroyed the pipeline.
    """
    # Set up current song playing
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    mock_song = create_mock_queue_item(
        id=2,
        title="New Song",
        user_name="Bob",
        download_path="/path/to/new.mp4",
        pitch_semitones=0,
        download_status=QueueManager.STATUS_READY,
    )
    mock_queue_manager.get_item.return_value = mock_song

    result = playback_controller.jump_to_song(2)

    assert result is True
    # IMPORTANT: jump_to_song() must call stop_playback() (returns to idle) NOT stop() (destroys pipeline)
    mock_streaming_controller.stop_playback.assert_called_once()
    mock_streaming_controller.stop.assert_not_called()
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/new.mp4")


def test_jump_to_song_when_idle(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test jump_to_song plays song at current position when nothing is playing."""
    # No current song
    playback_controller.current_song_id = None
    playback_controller.state = PlaybackState.IDLE

    # Song to jump to is at position 10
    target_song = create_mock_queue_item(
        id=3,
        position=10,
        title="Jump To Song",
        user_name="Bob",
        download_path="/path/to/jumpto.mp4",
        pitch_semitones=0,
        download_status=QueueManager.STATUS_READY,
    )

    # Mock get_item to return the song
    mock_queue_manager.get_item.return_value = target_song

    result = playback_controller.jump_to_song(3)

    assert result is True
    # Should NOT reorder - jump_to_song never reorders
    mock_queue_manager.reorder_song.assert_not_called()
    # Should not stop playback (nothing playing)
    mock_streaming_controller.stop_playback.assert_not_called()
    # Should load and play the song at its current position (10)
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/jumpto.mp4")
    assert playback_controller.current_song_id == 3


def test_jump_to_song_does_not_reorder(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that jump_to_song does NOT reorder the queue - it just plays at current position."""
    # Set up current song playing at position 5
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    # Song to jump to is at position 10
    target_song = create_mock_queue_item(
        id=3,
        position=10,
        title="Jump To Song",
        user_name="Bob",
        download_path="/path/to/jumpto.mp4",
        pitch_semitones=0,
        download_status=QueueManager.STATUS_READY,
    )

    mock_queue_manager.get_item.return_value = target_song

    result = playback_controller.jump_to_song(3)

    assert result is True
    # Should NOT reorder - jump_to_song never reorders
    mock_queue_manager.reorder_song.assert_not_called()
    # Should stop current playback
    mock_streaming_controller.stop_playback.assert_called_once()
    # Should load and play the song at its current position (still 10)
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/jumpto.mp4")
    assert playback_controller.current_song_id == 3


def test_jump_to_song_not_ready(playback_controller, mock_queue_manager):
    """Test jump_to_song fails when song is not ready."""
    target_song = create_mock_queue_item(
        id=3, position=10, title="Not Ready Song", download_status=QueueManager.STATUS_PENDING
    )
    mock_queue_manager.get_item.return_value = target_song

    result = playback_controller.jump_to_song(3)

    assert result is False
    # Should not attempt to reorder
    mock_queue_manager.reorder_song.assert_not_called()


def test_jump_to_song_logs_position(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that jump_to_song correctly logs the song position using QueueItem attributes.

    This is a regression test to ensure we're using attribute access (song.position)
    not dict access (song.get('position')) which would fail with QueueItem objects.
    """
    target_song = create_mock_queue_item(
        id=5,
        position=7,
        title="Test Song",
        download_path="/path/to/song.mp4",
        download_status=QueueManager.STATUS_READY,
    )
    mock_queue_manager.get_item.return_value = target_song

    # This should not raise AttributeError
    result = playback_controller.jump_to_song(5)

    assert result is True
    # Verify the song was played (which means the logger line executed successfully)
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/song.mp4")


def test_move_to_next_with_stale_position_cache(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that move_to_next uses fresh position data, not stale cache.

    Regression test: After queue reordering operations, the cached position
    in self.current_song['position'] becomes stale. move_to_next should query
    fresh position from database, not use the cached value.
    """
    # Currently playing song
    playback_controller.current_song_id = 10
    playback_controller.state = PlaybackState.PLAYING

    # In the database, current song is at position 5 (fresh data)
    current_song_fresh = create_mock_queue_item(
        id=10,
        position=5,
        title="Currently Playing",
        user_name="Alice",
        download_path="/path/to/current.mp4",
        download_status=QueueManager.STATUS_READY,
        pitch_semitones=0,
    )

    # Song to move to "play next"
    song_to_move = create_mock_queue_item(
        id=20, position=8, title="Move This Next", download_status=QueueManager.STATUS_READY
    )

    # Mock: get_item returns fresh data with correct position
    def get_item_side_effect(item_id):
        if item_id == 10:
            return current_song_fresh  # Fresh position = 5
        elif item_id == 20:
            return song_to_move
        return None

    mock_queue_manager.get_item.side_effect = get_item_side_effect
    mock_queue_manager.reorder_song.return_value = True

    # Call move_to_next
    result = playback_controller.move_to_next(20)

    assert result is True

    # BUG: With stale cache, it calculates: 3 + 1 = 4 (WRONG!)
    # CORRECT: Should query fresh position: 5 + 1 = 6
    # This assertion will FAIL, demonstrating the bug
    mock_queue_manager.reorder_song.assert_called_once_with(20, 6)


def test_on_song_end_plays_next_in_queue_order(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that on_song_end plays the next song by position, not first unplayed.

    This is a regression test for the bug where after a song ends, the system
    plays the first unplayed ready song instead of the next song by position.
    """
    import time

    # Song at position 2 is currently playing
    current_song = create_mock_queue_item(
        id=10,
        position=2,
        title="Song at Position 2",
        user_name="TestUser",
        video_id="youtube:vid10",
        duration_seconds=180,
        pitch_semitones=0,
        download_status=QueueManager.STATUS_READY,
        download_path="/path/to/song10.mp4",
    )
    playback_controller.current_song_id = 10
    playback_controller.state = PlaybackState.PLAYING

    # Mock get_item to return current song data
    mock_queue_manager.get_item.return_value = current_song

    # Queue has songs in this order (positions matter!):
    # Position 1: Song ID 5 (already played, not in unplayed list)
    # Position 2: Song ID 10 (currently playing)
    # Position 3: Song ID 15 (SHOULD play next)
    # Position 4: Song ID 20
    # Position 5: Song ID 25

    song_at_position_3 = create_mock_queue_item(
        id=15,
        position=3,
        title="Song at Position 3",
        user_name="TestUser",
        video_id="youtube:vid15",
        download_path="/path/to/song15.mp4",
        pitch_semitones=0,
        download_status=QueueManager.STATUS_READY,
    )

    song_at_position_4 = create_mock_queue_item(
        id=20, position=4, title="Song at Position 4", download_status=QueueManager.STATUS_READY
    )

    song_at_position_5 = create_mock_queue_item(
        id=25, position=5, title="Song at Position 5", download_status=QueueManager.STATUS_READY
    )

    # Mock get_queue to return unplayed songs (excludes the one that just finished)
    mock_queue_manager.get_queue.return_value = [
        song_at_position_3,
        song_at_position_4,
        song_at_position_5,
    ]

    # Mock get_ready_song_at_offset to return the song at position 3
    mock_queue_manager.get_ready_song_at_offset.return_value = song_at_position_3

    mock_queue_manager.mark_played.return_value = True
    mock_streaming_controller.get_position.return_value = 150

    # Simulate song ending
    playback_controller.on_song_end()

    # Should have marked current song as played
    mock_queue_manager.mark_played.assert_called_once_with(10)

    # Wait for transition timer (set to 0 in fixture)
    time.sleep(0.2)

    # The key assertion: should call get_ready_song_at_offset with the ID of the song that just ended and offset +1
    mock_queue_manager.get_ready_song_at_offset.assert_called_once_with(10, 1)

    # Should have started playing the next song
    assert playback_controller._next_song_pending == song_at_position_3


def test_auto_start_when_idle_with_ready_songs(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that _check_auto_start_when_idle starts playback when idle with ready songs.

    This is the "that's all" screen behavior - if someone adds a song
    when the queue is empty, it should start playing automatically once
    the download completes, without requiring the operator to press play.
    """
    # Set state to IDLE (simulating "that's all" screen)
    playback_controller.state = PlaybackState.IDLE
    assert playback_controller.current_song_id is None

    # Create a ready song
    ready_song = create_mock_queue_item(
        id=42,
        position=1,
        title="Auto-play Song",
        user_name="TestUser",
        download_status=QueueManager.STATUS_READY,
        download_path="/path/to/song.mp4",
    )

    # Mock get_ready_song_at_offset to return the ready song
    mock_queue_manager.get_ready_song_at_offset.return_value = ready_song

    # Call the check method (this is called by the monitor thread)
    playback_controller._check_auto_start_when_idle()

    # Should have started playback automatically
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/song.mp4")
    assert playback_controller.current_song_id == 42
    assert playback_controller.state == PlaybackState.PLAYING


def test_auto_start_when_idle_no_ready_songs(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that _check_auto_start_when_idle does nothing when no ready songs."""
    # Set state to IDLE
    playback_controller.state = PlaybackState.IDLE

    # Mock queue to return no ready songs (only pending)
    pending_song = create_mock_queue_item(
        id=42,
        position=1,
        title="Pending Song",
        download_status=QueueManager.STATUS_PENDING,
        download_path=None,
    )
    mock_queue_manager.get_queue.return_value = [pending_song]

    # Call the check method
    playback_controller._check_auto_start_when_idle()

    # Should NOT have started playback
    mock_streaming_controller.load_file.assert_not_called()
    assert playback_controller.state == PlaybackState.IDLE


def test_notification_restores_base_overlay(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that show_notification() restores the base overlay after the notification expires.

    This is a regression test for the bug where the overlay would go blank
    after a notification instead of returning to showing the current singer.
    """
    import time

    # Set up a base overlay (simulating "Now singing: Alice")
    playback_controller._set_base_overlay("Now singing: Alice")

    # Verify base overlay was set
    mock_streaming_controller.set_overlay_text.assert_called_with("Now singing: Alice")
    mock_streaming_controller.reset_mock()

    # Show a notification with a short duration
    playback_controller.show_notification("Bob added a song", duration_seconds=0.1)

    # Notification should be shown
    mock_streaming_controller.set_overlay_text.assert_called_with("Bob added a song")
    mock_streaming_controller.reset_mock()

    # Wait for notification to expire
    time.sleep(0.2)

    # Base overlay should be restored
    mock_streaming_controller.set_overlay_text.assert_called_with("Now singing: Alice")


def test_single_song_does_not_loop_after_playing(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that a single song stops after playing, rather than looping forever.

    This is a regression test for the bug where with a single song in the queue,
    when playback reached the end, the song would restart and loop infinitely
    instead of showing the end-of-queue interstitial.

    The root cause was that get_ready_song_at_offset() didn't filter out played
    songs, so after the song was marked as played, _check_auto_start_when_idle()
    would see the played song as "ready" and restart it.
    """
    # Single song in queue, ready to play
    song = create_mock_queue_item(
        id=1,
        title="Only Song",
        user_name="Alice",
        video_id="youtube:only_song",
        duration_seconds=180,
        download_path="/path/to/song.mp4",
        download_status=QueueManager.STATUS_READY,
    )

    # Initially, the song is in the queue and ready
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING
    mock_queue_manager.get_item.return_value = song

    # When the song ends and is marked as played, get_ready_song_at_offset
    # should return None (no next song, and the played song shouldn't be returned)
    mock_queue_manager.get_ready_song_at_offset.return_value = None

    # Simulate song ending
    playback_controller.on_song_end()

    # Song should be marked as played
    mock_queue_manager.mark_played.assert_called_once_with(1)

    # State should be IDLE (not PLAYING or TRANSITION)
    assert playback_controller.state == PlaybackState.IDLE

    # Current song should be cleared
    assert playback_controller.current_song_id is None

    # End-of-queue interstitial should be displayed
    mock_streaming_controller.display_image.assert_called_once()

    # Critical: get_ready_song_at_offset was called with the finished song's ID
    # to find the next song, and it returned None (no unplayed songs)
    mock_queue_manager.get_ready_song_at_offset.assert_called_once_with(1, 1)


def test_auto_start_does_not_restart_played_songs(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that auto-start doesn't restart songs that have already been played.

    This is a regression test for the single-song loop bug. Even if a played
    song is still in the queue with download_status='ready', the auto-start
    mechanism should not restart it.
    """
    # Set state to IDLE (simulating "that's all" screen after song finished)
    playback_controller.state = PlaybackState.IDLE
    playback_controller.current_song_id = None

    # The queue has a song that was already played (get_ready_song_at_offset
    # should return None because the song has played_at set)
    mock_queue_manager.get_ready_song_at_offset.return_value = None

    # Call the auto-start check (this is called by the monitor thread)
    playback_controller._check_auto_start_when_idle()

    # Should NOT have started playback
    mock_streaming_controller.load_file.assert_not_called()

    # State should still be IDLE
    assert playback_controller.state == PlaybackState.IDLE
