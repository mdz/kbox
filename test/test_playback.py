"""
Unit tests for PlaybackController.

Uses mocks for dependencies.
"""

import threading
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
    # Configure navigation to return None by default
    qm.get_song_at_offset.return_value = None
    # Provide a mock database for ConfigRepository initialization
    qm.database = Mock()
    qm.database.get_connection.return_value.cursor.return_value.fetchone.return_value = None
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
def mock_config_manager(tmp_path):
    """Create a mock ConfigManager."""
    config = Mock()
    # Configure get() to return None for unknown keys, or specific values
    config.get.return_value = None
    config.get_cache_directory.return_value = tmp_path
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
    content_status="ready",
    content_path="/path/to/video.mp4",
    error_message=None,
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
        content_status=content_status,
        content_path=content_path,
        error_message=error_message,
    )


@pytest.fixture
def playback_controller(mock_queue_manager, mock_streaming_controller, mock_config_manager):
    """Create a PlaybackController instance."""
    # Mock get_queue to return empty list to avoid thread issues
    mock_queue_manager.get_queue.return_value = []

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


def test_play_no_songs_in_queue(playback_controller, mock_queue_manager):
    """Test play when no songs in queue."""
    mock_queue_manager.get_song_at_offset.return_value = None

    result = playback_controller.play()

    assert result is False
    assert playback_controller.state == PlaybackState.IDLE


def test_play_with_ready_song(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test playing a ready song."""
    mock_song = create_mock_queue_item(
        id=1,
        title="Test Song",
        user_name="Alice",
        content_path="/path/to/video.mp4",
        pitch_semitones=2,
        content_status=QueueManager.STATUS_READY,
    )
    # Mock get_song_at_offset to return the song (used by _load_and_play_next)
    mock_queue_manager.get_song_at_offset.return_value = mock_song

    result = playback_controller.play()

    assert result is True
    assert playback_controller.state == PlaybackState.PLAYING
    assert playback_controller.current_song_id == 1
    mock_streaming_controller.set_pitch_shift.assert_called_once_with(2)
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/video.mp4")
    # Cursor should be set to the song that just started playing
    assert playback_controller.get_cursor() == 1


def test_play_no_content_path(playback_controller, mock_queue_manager):
    """Test play when song has no download path."""
    mock_song = create_mock_queue_item(
        id=1, title="Test Song", user_name="Alice", content_path=None
    )
    mock_queue_manager.get_song_at_offset.return_value = mock_song

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
    """Test skipping to next song stops current playback and enters transition."""
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

    mock_queue_manager.get_item.return_value = current_song

    mock_next_song = create_mock_queue_item(
        id=2,
        title="Next Song",
        user_name="Bob",
        content_path="/path/to/next.mp4",
        pitch_semitones=0,
        content_status=QueueManager.STATUS_READY,
    )
    mock_queue_manager.get_song_at_offset.return_value = mock_next_song

    result = playback_controller.skip()

    assert result is True
    # IMPORTANT: skip() must call stop_playback() (returns to idle) NOT stop() (destroys pipeline)
    mock_streaming_controller.stop_playback.assert_called_once()
    mock_streaming_controller.stop.assert_not_called()
    # Skip goes to TRANSITION (interstitial), not directly to playing
    assert playback_controller.state == PlaybackState.TRANSITION


def test_skip_shows_interstitial_before_playing(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that skip shows the next-singer interstitial before starting the next song.

    When an operator skips a song (e.g. long outro), the transition screen should
    announce the next singer — giving them time to come up — before the song starts.
    This is the same flow as a natural end-of-song.
    """
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

    mock_queue_manager.get_item.return_value = current_song

    mock_next_song = create_mock_queue_item(
        id=2,
        title="Next Song",
        user_name="Bob",
        content_path="/path/to/next.mp4",
        pitch_semitones=0,
        content_status=QueueManager.STATUS_READY,
    )
    mock_queue_manager.get_song_at_offset.return_value = mock_next_song

    playback_controller.skip()

    # Immediately after skip: should be in TRANSITION, not yet playing
    assert playback_controller.state == PlaybackState.TRANSITION
    # Next song is staged but not yet started
    assert playback_controller._next_song_pending == mock_next_song
    mock_streaming_controller.load_file.assert_not_called()


def test_skip_clears_base_overlay(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that skipping clears the "Now singing" overlay.

    The overlay describes who is performing *now*, so it must not survive
    into the transition interstitial or the start of the next song. It
    should come back on its own once the next song's monitor loop re-sets
    it (not exercised here since the monitor thread is disabled in tests).
    """
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
    playback_controller._set_base_overlay("Now singing: Alice")
    mock_streaming_controller.set_overlay_text.reset_mock()

    mock_queue_manager.get_item.return_value = current_song

    mock_next_song = create_mock_queue_item(
        id=2,
        title="Next Song",
        user_name="Bob",
        content_path="/path/to/next.mp4",
        pitch_semitones=0,
        content_status=QueueManager.STATUS_READY,
    )
    mock_queue_manager.get_song_at_offset.return_value = mock_next_song

    playback_controller.skip()

    mock_streaming_controller.set_overlay_text.assert_called_with("")
    assert playback_controller._base_overlay_text == ""


def test_natural_end_of_song_clears_base_overlay(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that a song finishing naturally also clears the "Now singing" overlay."""
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
    playback_controller._set_base_overlay("Now singing: Alice")
    mock_streaming_controller.set_overlay_text.reset_mock()

    mock_queue_manager.get_item.return_value = current_song
    # No next song - queue exhausted after this one
    mock_queue_manager.get_song_at_offset.return_value = None

    playback_controller.on_song_end()

    mock_streaming_controller.set_overlay_text.assert_called_with("")
    assert playback_controller._base_overlay_text == ""


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
        content_status=QueueManager.STATUS_READY,
    )
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    # Mock get_item to return current song data
    mock_queue_manager.get_item.return_value = current_song
    # Mock get_song_at_offset to return None (no next song)
    mock_queue_manager.get_song_at_offset.return_value = None

    result = playback_controller.skip()

    assert result is False
    # State should remain PLAYING - we don't stop current song if there's no next
    assert playback_controller.state == PlaybackState.PLAYING
    # Should not have stopped playback
    mock_streaming_controller.stop_playback.assert_not_called()


def test_previous_shows_interstitial(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that previous() shows the "up next" interstitial before playing,
    mirroring skip()'s behavior in the backward direction (issue: skipping
    backwards used to jump straight into the previous song with no
    interstitial, giving that singer no heads-up).
    """
    current_song = create_mock_queue_item(
        id=2,
        title="Current Song",
        user_name="Bob",
        video_id="youtube:def456",
        duration_seconds=180,
        pitch_semitones=0,
    )
    playback_controller.current_song_id = 2
    playback_controller.state = PlaybackState.PLAYING

    mock_queue_manager.get_item.return_value = current_song

    mock_prev_song = create_mock_queue_item(
        id=1,
        title="Prev Song",
        user_name="Alice",
        content_path="/path/to/prev.mp4",
        pitch_semitones=0,
        content_status=QueueManager.STATUS_READY,
    )
    mock_queue_manager.get_song_at_offset.return_value = mock_prev_song

    result = playback_controller.previous()

    assert result is True
    mock_streaming_controller.stop_playback.assert_called_once()
    # Same as skip(): goes to TRANSITION and stages the target, doesn't jump
    # straight into playing it.
    assert playback_controller.state == PlaybackState.TRANSITION
    assert playback_controller._next_song_pending == mock_prev_song
    mock_streaming_controller.load_file.assert_not_called()


def test_skip_while_in_transition_retargets_pending(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that skip() works while the transition interstitial is already
    showing, re-targeting the pending song and cancelling the old timer
    (issue: skip/previous were inert during TRANSITION, forcing the
    operator to wait for the pending song to actually start before they
    could navigate away from it).
    """
    playback_controller.state = PlaybackState.TRANSITION
    playback_controller.current_song_id = None  # cleared by _complete_current_song
    playback_controller._cursor_song_id = 1  # song that just finished/was skipped

    pending_song = create_mock_queue_item(id=2, title="Pending Song", user_name="Bob")
    playback_controller._next_song_pending = pending_song

    old_timer = Mock()
    playback_controller._transition_timer = old_timer

    new_target = create_mock_queue_item(
        id=3,
        title="Later Song",
        user_name="Carol",
        content_path="/path/to/later.mp4",
        content_status=QueueManager.STATUS_READY,
    )
    # Navigation while in TRANSITION is relative to the pending song (id=2), +1
    mock_queue_manager.get_song_at_offset.return_value = new_target

    result = playback_controller.skip()

    assert result is True
    mock_queue_manager.get_song_at_offset.assert_called_once_with(2, 1)
    # The stale timer must be cancelled so the old pending song can't also start
    old_timer.cancel.assert_called_once()
    assert playback_controller.state == PlaybackState.TRANSITION
    assert playback_controller._next_song_pending == new_target
    mock_streaming_controller.load_file.assert_not_called()


def test_previous_while_in_transition_retargets_pending(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that previous() also works while the transition interstitial is
    showing, navigating backward relative to the pending song.
    """
    playback_controller.state = PlaybackState.TRANSITION
    playback_controller.current_song_id = None
    playback_controller._cursor_song_id = 1

    pending_song = create_mock_queue_item(id=2, title="Pending Song", user_name="Bob")
    playback_controller._next_song_pending = pending_song

    old_timer = Mock()
    playback_controller._transition_timer = old_timer

    earlier_target = create_mock_queue_item(
        id=1,
        title="Earlier Song",
        user_name="Alice",
        content_path="/path/to/earlier.mp4",
        content_status=QueueManager.STATUS_READY,
    )
    mock_queue_manager.get_song_at_offset.return_value = earlier_target

    result = playback_controller.previous()

    assert result is True
    mock_queue_manager.get_song_at_offset.assert_called_once_with(2, -1)
    old_timer.cancel.assert_called_once()
    assert playback_controller.state == PlaybackState.TRANSITION
    assert playback_controller._next_song_pending == earlier_target


def test_navigate_during_transition_no_target_available(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Navigating past the end/start of the queue during TRANSITION should
    fail cleanly and leave the existing pending transition untouched."""
    playback_controller.state = PlaybackState.TRANSITION
    playback_controller.current_song_id = None
    playback_controller._cursor_song_id = 1

    pending_song = create_mock_queue_item(id=2, title="Pending Song", user_name="Bob")
    playback_controller._next_song_pending = pending_song

    timer = Mock()
    playback_controller._transition_timer = timer

    mock_queue_manager.get_song_at_offset.return_value = None

    result = playback_controller.skip()

    assert result is False
    # Nothing should have been torn down since navigation didn't happen
    timer.cancel.assert_not_called()
    assert playback_controller._next_song_pending == pending_song
    assert playback_controller.state == PlaybackState.TRANSITION


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
        content_path="/path/to/next.mp4",
        pitch_semitones=0,
        content_status=QueueManager.STATUS_READY,
    )
    # Mock get_song_at_offset to return the next song (respects queue order)
    mock_queue_manager.get_song_at_offset.return_value = mock_next_song

    playback_controller.on_song_end()

    # Should call get_song_at_offset with the finished song's ID and offset +1
    mock_queue_manager.get_song_at_offset.assert_called_once_with(1, 1)
    # Should reset pitch and volume gain
    mock_streaming_controller.set_pitch_shift.assert_any_call(0)
    mock_streaming_controller.set_volume_gain_db.assert_any_call(0.0)
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
    # Mock get_song_at_offset to return None (no next song)
    mock_queue_manager.get_song_at_offset.return_value = None

    playback_controller.on_song_end()

    # Should call get_song_at_offset with the finished song's ID and offset +1
    mock_queue_manager.get_song_at_offset.assert_called_once_with(1, 1)
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
        content_path="/path/to/new.mp4",
        pitch_semitones=0,
        content_status=QueueManager.STATUS_READY,
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
        content_path="/path/to/jumpto.mp4",
        pitch_semitones=0,
        content_status=QueueManager.STATUS_READY,
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
        content_path="/path/to/jumpto.mp4",
        pitch_semitones=0,
        content_status=QueueManager.STATUS_READY,
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
        id=3, position=10, title="Not Ready Song", content_status=QueueManager.STATUS_PENDING
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
        content_path="/path/to/song.mp4",
        content_status=QueueManager.STATUS_READY,
    )
    mock_queue_manager.get_item.return_value = target_song

    # This should not raise AttributeError
    result = playback_controller.jump_to_song(5)

    assert result is True
    # Verify the song was played (which means the logger line executed successfully)
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/song.mp4")


def test_replace_song_not_currently_playing(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Replacing a song that isn't playing has no playback side effects."""
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    target_item = create_mock_queue_item(id=2, user_name="Bob")
    mock_queue_manager.get_item.return_value = target_item
    mock_queue_manager.replace_song.return_value = True

    result = playback_controller.replace_song(2, video_id="youtube:new", title="Correct Song")

    assert result is True
    mock_streaming_controller.stop_playback.assert_not_called()
    assert playback_controller.state == PlaybackState.PLAYING
    assert playback_controller.current_song_id == 1
    mock_queue_manager.replace_song.assert_called_once_with(
        2, "youtube:new", "Correct Song", None, None, None
    )


def test_replace_song_not_found(playback_controller, mock_queue_manager):
    """Replacing a queue item that no longer exists fails cleanly."""
    mock_queue_manager.get_item.return_value = None

    result = playback_controller.replace_song(99, video_id="youtube:new", title="X")

    assert result is False
    mock_queue_manager.replace_song.assert_not_called()


def test_replace_currently_playing_song_stops_playback(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Replacing the currently playing song stops it immediately and arms auto-resume."""
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    current_item = create_mock_queue_item(id=1, user_name="Alice")
    mock_queue_manager.get_item.return_value = current_item
    mock_queue_manager.replace_song.return_value = True

    result = playback_controller.replace_song(1, video_id="youtube:fixed", title="Fixed Song")

    assert result is True
    # display_image() (interstitial) drives the pipeline back to idle itself -
    # no separate stop_playback() call, and no blank-screen text overlay.
    mock_streaming_controller.stop_playback.assert_not_called()
    mock_streaming_controller.display_image.assert_called_once()
    assert playback_controller.state == PlaybackState.STOPPED
    # Cursor/current_song_id are untouched - same item, position is unchanged
    assert playback_controller.current_song_id == 1
    assert playback_controller._awaiting_replace_item_id == 1


def test_replace_currently_playing_song_auto_resumes_when_ready(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Once the replacement finishes downloading, playback resumes without operator action."""
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING

    current_item = create_mock_queue_item(
        id=1, user_name="Alice", content_status=QueueManager.STATUS_PENDING
    )
    mock_queue_manager.get_item.return_value = current_item
    mock_queue_manager.replace_song.return_value = True

    playback_controller.replace_song(1, video_id="youtube:fixed", title="Fixed Song")
    assert playback_controller.state == PlaybackState.STOPPED

    # Content isn't ready yet - the monitor's check should be a no-op
    playback_controller._check_auto_resume_after_replace()
    mock_streaming_controller.load_file.assert_not_called()
    assert playback_controller._awaiting_replace_item_id == 1

    # Content finishes downloading
    ready_item = create_mock_queue_item(
        id=1,
        user_name="Alice",
        content_status=QueueManager.STATUS_READY,
        content_path="/path/to/fixed.mp4",
    )
    mock_queue_manager.get_item.return_value = ready_item

    playback_controller._check_auto_resume_after_replace()

    assert playback_controller._awaiting_replace_item_id is None
    assert playback_controller.state == PlaybackState.PLAYING
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/fixed.mp4")


def test_manual_stop_clears_pending_replace_auto_resume(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """An explicit operator stop overrides a pending replace auto-resume."""
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING
    playback_controller._awaiting_replace_item_id = 1

    playback_controller.stop_playback()

    assert playback_controller._awaiting_replace_item_id is None


def test_navigating_away_clears_pending_replace_auto_resume(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Navigating to a different song (e.g. Previous) clears a stale pending replace auto-resume."""
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.STOPPED
    playback_controller._awaiting_replace_item_id = 1

    prev_song = create_mock_queue_item(
        id=2, title="Prev", content_status=QueueManager.STATUS_READY, content_path="/prev.mp4"
    )
    mock_queue_manager.get_song_at_offset.return_value = prev_song

    playback_controller.previous()

    assert playback_controller._awaiting_replace_item_id is None
    assert playback_controller.current_song_id == 2


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
        content_path="/path/to/current.mp4",
        content_status=QueueManager.STATUS_READY,
        pitch_semitones=0,
    )

    # Song to move to "play next"
    song_to_move = create_mock_queue_item(
        id=20, position=8, title="Move This Next", content_status=QueueManager.STATUS_READY
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


def test_move_to_next_when_current_song_is_last_in_queue(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that move_to_next clamps to the queue's max position instead of failing.

    Regression test for https://github.com/mdz/kbox/issues/89: when the
    currently playing song is last in the queue, current_song.position + 1
    exceeds the max position, so reorder() would reject it as an "invalid
    position" - which the API layer misreports as "Queue item not found".
    "Next" after the last song should simply mean the end of the queue.
    """
    playback_controller.current_song_id = 10
    playback_controller.state = PlaybackState.PLAYING

    # Current song is last in the queue, at position 2
    current_song = create_mock_queue_item(
        id=10, position=2, title="Currently Playing", content_status=QueueManager.STATUS_READY
    )
    song_to_move = create_mock_queue_item(
        id=20, position=1, title="Move This Next", content_status=QueueManager.STATUS_READY
    )

    def get_item_side_effect(item_id):
        if item_id == 10:
            return current_song
        elif item_id == 20:
            return song_to_move
        return None

    mock_queue_manager.get_item.side_effect = get_item_side_effect
    mock_queue_manager.get_queue.return_value = [song_to_move, current_song]
    mock_queue_manager.reorder_song.return_value = True

    result = playback_controller.move_to_next(20)

    assert result is True
    # Clamped to max position (2), not 3 (which would be rejected as invalid)
    mock_queue_manager.reorder_song.assert_called_once_with(20, 2)


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
        content_status=QueueManager.STATUS_READY,
        content_path="/path/to/song10.mp4",
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
        content_path="/path/to/song15.mp4",
        pitch_semitones=0,
        content_status=QueueManager.STATUS_READY,
    )

    song_at_position_4 = create_mock_queue_item(
        id=20, position=4, title="Song at Position 4", content_status=QueueManager.STATUS_READY
    )

    song_at_position_5 = create_mock_queue_item(
        id=25, position=5, title="Song at Position 5", content_status=QueueManager.STATUS_READY
    )

    # Mock get_queue to return unplayed songs (excludes the one that just finished)
    mock_queue_manager.get_queue.return_value = [
        song_at_position_3,
        song_at_position_4,
        song_at_position_5,
    ]

    # Mock get_song_at_offset to return the song at position 3
    mock_queue_manager.get_song_at_offset.return_value = song_at_position_3

    mock_streaming_controller.get_position.return_value = 150

    # Simulate song ending
    playback_controller.on_song_end()

    # Wait for transition timer (set to 0 in fixture)
    time.sleep(0.2)

    # The key assertion: should call get_song_at_offset with the ID of the song that just ended and offset +1
    mock_queue_manager.get_song_at_offset.assert_called_once_with(10, 1)

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
        position=2,
        title="Auto-play Song",
        user_name="TestUser",
        content_status=QueueManager.STATUS_READY,
        content_path="/path/to/song.mp4",
    )

    # Cursor is on song 1 (already played); song 42 at position 2 is next
    playback_controller._cursor_song_id = 1
    # get_song_at_offset(1, +1) returns the new ready song
    mock_queue_manager.get_song_at_offset.return_value = ready_song

    # Call the check method (this is called by the monitor thread)
    playback_controller._check_auto_start_when_idle()

    # Should have checked for songs after the cursor
    mock_queue_manager.get_song_at_offset.assert_any_call(1, 1)

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
        content_status=QueueManager.STATUS_PENDING,
        content_path=None,
    )
    mock_queue_manager.get_queue.return_value = [pending_song]

    # Call the check method
    playback_controller._check_auto_start_when_idle()

    # Should NOT have started playback
    mock_streaming_controller.load_file.assert_not_called()
    assert playback_controller.state == PlaybackState.IDLE


def test_auto_start_waits_for_not_ready_song(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that auto-start waits for a not-ready song instead of skipping it.

    If the next song in queue order is still downloading, we should wait
    rather than jumping ahead to a later song that finished downloading first.
    This respects the queue order that users established.
    """
    # Set state to IDLE
    playback_controller.state = PlaybackState.IDLE
    playback_controller._cursor_song_id = 1

    # Next song in queue order is still downloading
    downloading_song = create_mock_queue_item(
        id=2,
        position=2,
        title="Still Downloading",
        content_status=QueueManager.STATUS_PREPARING,
        content_path=None,
    )
    mock_queue_manager.get_song_at_offset.return_value = downloading_song

    # Call the auto-start check
    playback_controller._check_auto_start_when_idle()

    # Should NOT have started playback - wait for this song to be ready
    mock_streaming_controller.load_file.assert_not_called()
    assert playback_controller.state == PlaybackState.IDLE


def test_load_and_play_next_waits_for_not_ready_song(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that _load_and_play_next goes idle when next song isn't ready yet."""
    # Next song in queue order is pending
    pending_song = create_mock_queue_item(
        id=2,
        position=2,
        title="Pending Song",
        content_status=QueueManager.STATUS_PENDING,
        content_path=None,
    )
    mock_queue_manager.get_song_at_offset.return_value = pending_song

    with playback_controller._locked():
        result = playback_controller._load_and_play_next()

    assert result is False
    assert playback_controller.state == PlaybackState.IDLE
    mock_streaming_controller.load_file.assert_not_called()


def test_transition_waits_for_not_ready_next_song(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that after a song ends, if next song isn't ready, we go idle.

    The auto-start monitor will pick it up when it becomes ready,
    rather than skipping over it to a later song.
    """
    # Next song in queue order is still downloading
    downloading_song = create_mock_queue_item(
        id=2,
        position=2,
        title="Still Downloading",
        content_status=QueueManager.STATUS_PREPARING,
        content_path=None,
    )
    mock_queue_manager.get_song_at_offset.return_value = downloading_song

    with playback_controller._locked():
        playback_controller._show_transition_or_end(finished_song_id=1)

    # Should go idle, not transition
    assert playback_controller.state == PlaybackState.IDLE
    assert playback_controller._next_song_pending is None
    # Should show end-of-queue screen (auto-start will handle when ready)
    mock_streaming_controller.display_image.assert_called_once()


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

    With the cursor model, when a song finishes:
    1. on_song_end() looks for next song via get_song_at_offset(finished_id, +1)
    2. No next song -> goes to IDLE, shows end-of-queue screen
    3. _check_auto_start_when_idle() uses get_song_at_offset(cursor, +1)
    4. Cursor points to the finished song, no songs after it -> stays IDLE

    This prevents the infinite loop because auto-start only looks forward from cursor.
    """
    # Single song in queue, ready to play
    song = create_mock_queue_item(
        id=1,
        title="Only Song",
        user_name="Alice",
        video_id="youtube:only_song",
        duration_seconds=180,
        content_path="/path/to/song.mp4",
        content_status=QueueManager.STATUS_READY,
    )

    # Initially, the song is in the queue and ready
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING
    mock_queue_manager.get_item.return_value = song

    # When the song ends, get_song_at_offset returns None (no next song)
    mock_queue_manager.get_song_at_offset.return_value = None

    # Simulate song ending
    playback_controller.on_song_end()

    # State should be IDLE (not PLAYING or TRANSITION)
    assert playback_controller.state == PlaybackState.IDLE

    # Current song should be cleared
    assert playback_controller.current_song_id is None

    # End-of-queue interstitial should be displayed
    mock_streaming_controller.display_image.assert_called_once()

    # Critical: get_song_at_offset was called with the finished song's ID
    # to find the next song, and it returned None
    mock_queue_manager.get_song_at_offset.assert_called_once_with(1, 1)


def test_auto_start_does_not_restart_played_songs(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that auto-start doesn't restart songs that have already been played.

    With the cursor model, auto-start uses get_song_at_offset(cursor, +1)
    to only look forward from the cursor. If the cursor is on the last song,
    there's nothing after it, so auto-start correctly stays idle.
    """
    # Set state to IDLE (simulating "that's all" screen after song finished)
    playback_controller.state = PlaybackState.IDLE
    playback_controller.current_song_id = None

    # Cursor is set to the song that just finished (song ID 1)
    playback_controller._cursor_song_id = 1

    # No songs after the cursor
    mock_queue_manager.get_song_at_offset.return_value = None

    # Call the auto-start check (this is called by the monitor thread)
    playback_controller._check_auto_start_when_idle()

    # Should have checked for songs after the cursor
    mock_queue_manager.get_song_at_offset.assert_called_once_with(1, 1)

    # Should NOT have started playback
    mock_streaming_controller.load_file.assert_not_called()

    # State should still be IDLE
    assert playback_controller.state == PlaybackState.IDLE


def test_stop_remembers_current_song(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that stop_playback() remembers the current song for resume."""
    # Set up: song is playing
    current_song = create_mock_queue_item(id=5, title="Current Song")
    mock_queue_manager.get_song_at_offset.return_value = current_song
    mock_queue_manager.get_item.return_value = current_song

    # Start playing
    playback_controller.play()
    assert playback_controller.current_song_id == 5
    assert playback_controller.state == PlaybackState.PLAYING

    # Stop playback
    result = playback_controller.stop_playback()

    assert result is True
    assert playback_controller.state == PlaybackState.STOPPED
    # KEY: current_song_id should be preserved, not cleared
    assert playback_controller.current_song_id == 5


def test_play_after_stop_resumes_same_song(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that play() after stop_playback() resumes the same song."""
    # Set up: song is stopped (simulating after stop_playback)
    stopped_song = create_mock_queue_item(
        id=5, title="Stopped Song", content_path="/path/to/stopped.mp4"
    )
    playback_controller.current_song_id = 5
    playback_controller.state = PlaybackState.STOPPED
    mock_queue_manager.get_item.return_value = stopped_song

    # Play should resume the stopped song
    mock_streaming_controller.load_file.reset_mock()
    result = playback_controller.play()

    assert result is True
    assert playback_controller.state == PlaybackState.PLAYING
    assert playback_controller.current_song_id == 5
    # Should load the same song, not get a new one from the queue
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/stopped.mp4")


def test_play_after_stop_song_deleted_falls_back(
    playback_controller, mock_queue_manager, mock_streaming_controller
):
    """Test that play() after stop falls back to first ready song if stopped song was deleted."""
    # Set up: stopped state with a song ID that no longer exists
    playback_controller.current_song_id = 5
    playback_controller.state = PlaybackState.STOPPED
    mock_queue_manager.get_item.return_value = None  # Song was deleted

    # First song in queue (ready)
    fallback_song = create_mock_queue_item(
        id=10, title="Fallback Song", content_path="/path/to/fallback.mp4"
    )
    mock_queue_manager.get_song_at_offset.return_value = fallback_song

    # Play should fall back to first song in queue
    result = playback_controller.play()

    assert result is True
    assert playback_controller.state == PlaybackState.PLAYING
    assert playback_controller.current_song_id == 10
    mock_streaming_controller.load_file.assert_called_once_with("/path/to/fallback.mp4")


class TestSeekRelative:
    """Tests for seek_relative()."""

    def test_seek_forward(self, mock_queue_manager, mock_streaming_controller, mock_config_manager):
        mock_history = Mock()
        pc = PlaybackController(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, mock_history
        )

        song = create_mock_queue_item(id=1, content_status="ready", duration_seconds=300)
        mock_queue_manager.get_song_at_offset.return_value = song
        mock_queue_manager.get_item.return_value = song

        pc.play()
        mock_streaming_controller.get_position.return_value = 60

        result = pc.seek_relative(30)
        assert result["status"] == "seeked"
        mock_streaming_controller.seek.assert_called_with(90)

    def test_seek_backward(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        mock_history = Mock()
        pc = PlaybackController(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, mock_history
        )

        song = create_mock_queue_item(id=1, content_status="ready", duration_seconds=300)
        mock_queue_manager.get_song_at_offset.return_value = song
        mock_queue_manager.get_item.return_value = song

        pc.play()
        mock_streaming_controller.get_position.return_value = 60

        result = pc.seek_relative(-30)
        assert result["status"] == "seeked"
        mock_streaming_controller.seek.assert_called_with(30)

    def test_seek_backward_clamps_to_zero(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        mock_history = Mock()
        pc = PlaybackController(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, mock_history
        )

        song = create_mock_queue_item(id=1, content_status="ready", duration_seconds=300)
        mock_queue_manager.get_song_at_offset.return_value = song
        mock_queue_manager.get_item.return_value = song

        pc.play()
        mock_streaming_controller.get_position.return_value = 10

        result = pc.seek_relative(-50)
        assert result["status"] == "seeked"
        mock_streaming_controller.seek.assert_called_with(0)

    def test_seek_forward_clamps_to_duration(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        mock_history = Mock()
        pc = PlaybackController(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, mock_history
        )

        song = create_mock_queue_item(id=1, content_status="ready", duration_seconds=180)
        mock_queue_manager.get_song_at_offset.return_value = song
        mock_queue_manager.get_item.return_value = song

        pc.play()
        mock_streaming_controller.get_position.return_value = 170

        result = pc.seek_relative(30)
        assert result["status"] == "seeked"
        # Should clamp to duration - 1 = 179
        mock_streaming_controller.seek.assert_called_with(179)

    def test_seek_when_not_playing_is_nothing_playing(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        mock_history = Mock()
        pc = PlaybackController(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, mock_history
        )
        # State is IDLE, no song playing
        assert pc.seek_relative(10)["status"] == "nothing_playing"

    def test_seek_when_no_current_song(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        mock_history = Mock()
        pc = PlaybackController(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, mock_history
        )
        assert pc.seek_relative(10)["status"] == "nothing_playing"


class TestShutdown:
    """Tests for shutdown()."""

    def test_shutdown_stops_streaming(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        mock_history = Mock()
        pc = PlaybackController(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, mock_history
        )
        pc.shutdown()
        mock_streaming_controller.stop.assert_called_once()

    def test_shutdown_sets_monitoring_false(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        mock_history = Mock()
        pc = PlaybackController(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, mock_history
        )
        pc.shutdown()
        assert pc._monitoring is False


class TestTrailingSilenceSkip:
    """Tests for the trailing-silence early-skip feature."""

    def _make_controller(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager, enabled=True
    ):
        mock_queue_manager.get_queue.return_value = []
        mock_silence_analyzer = Mock()
        mock_config_manager.get_bool.return_value = enabled
        pc = PlaybackController(
            mock_queue_manager,
            mock_streaming_controller,
            mock_config_manager,
            silence_analyzer=mock_silence_analyzer,
        )
        pc._monitoring = False
        return pc, mock_silence_analyzer

    def test_lookup_trim_point_no_analyzer(self, playback_controller):
        """No analyzer configured -- always None, feature is a no-op."""
        assert playback_controller.silence_analyzer is None
        assert playback_controller._lookup_trim_point("youtube:abc") is None

    def test_lookup_trim_point_disabled_by_config(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        pc, mock_analyzer = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, enabled=False
        )
        mock_analyzer.get_cached_trim_point.return_value = 120

        assert pc._lookup_trim_point("youtube:abc") is None
        mock_analyzer.get_cached_trim_point.assert_not_called()

    def test_lookup_trim_point_returns_cached_value(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        pc, mock_analyzer = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, enabled=True
        )
        mock_analyzer.get_cached_trim_point.return_value = 120

        assert pc._lookup_trim_point("youtube:abc") == 120
        mock_analyzer.get_cached_trim_point.assert_called_once_with("youtube:abc")

    def test_lookup_trim_point_swallows_analyzer_errors(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        pc, mock_analyzer = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, enabled=True
        )
        mock_analyzer.get_cached_trim_point.side_effect = Exception("db error")

        assert pc._lookup_trim_point("youtube:abc") is None

    def test_play_song_sets_trim_point(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        pc, mock_analyzer = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, enabled=True
        )
        mock_analyzer.get_cached_trim_point.return_value = 150

        song = create_mock_queue_item(id=1, video_id="youtube:abc", content_status="ready")
        mock_queue_manager.get_song_at_offset.return_value = song

        pc.play()

        assert pc._current_trim_point == 150

    def test_check_trailing_silence_skip_before_trim_point_noop(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        pc, _ = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager
        )
        pc.state = PlaybackState.PLAYING
        pc.current_song_id = 1
        pc._current_trim_point = 100

        pc._check_trailing_silence_skip(50)

        mock_streaming_controller.stop_playback.assert_not_called()
        assert pc.current_song_id == 1

    def test_check_trailing_silence_skip_no_trim_point_noop(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        pc, _ = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager
        )
        pc.state = PlaybackState.PLAYING
        pc.current_song_id = 1
        pc._current_trim_point = None

        pc._check_trailing_silence_skip(999)

        mock_streaming_controller.stop_playback.assert_not_called()

    def test_check_trailing_silence_skip_triggers_completion(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        pc, _ = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager
        )
        current_song = create_mock_queue_item(id=1, video_id="youtube:abc123", duration_seconds=180)
        pc.state = PlaybackState.PLAYING
        pc.current_song_id = 1
        pc._current_trim_point = 170
        mock_queue_manager.get_item.return_value = current_song
        mock_queue_manager.get_song_at_offset.return_value = None  # no next song

        pc._check_trailing_silence_skip(170)

        # Should stop playback itself (GStreamer won't reach EOS on its own)
        mock_streaming_controller.stop_playback.assert_called_once()
        # Should finish the song and go idle (no next song)
        assert pc.current_song_id is None
        assert pc.state == PlaybackState.IDLE
        # Trim point cleared along with the finished song
        assert pc._current_trim_point is None

    def test_check_trailing_silence_skip_records_full_duration_for_history(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        """History/completion stats should use the real song duration, not
        the truncated stop position, so trimmed songs count as fully played."""
        mock_history = Mock()
        mock_queue_manager.get_queue.return_value = []
        mock_silence_analyzer = Mock()
        mock_config_manager.get_bool.return_value = True
        pc = PlaybackController(
            mock_queue_manager,
            mock_streaming_controller,
            mock_config_manager,
            history_manager=mock_history,
            silence_analyzer=mock_silence_analyzer,
        )
        pc._monitoring = False

        current_song = create_mock_queue_item(id=1, video_id="youtube:abc123", duration_seconds=180)
        pc.state = PlaybackState.PLAYING
        pc.current_song_id = 1
        pc._current_trim_point = 170
        mock_queue_manager.get_item.return_value = current_song
        mock_queue_manager.get_song_at_offset.return_value = None

        pc._check_trailing_silence_skip(170)

        assert mock_history.record_performance.called
        _, kwargs = mock_history.record_performance.call_args
        assert kwargs["played_duration_seconds"] == 180
        assert kwargs["completion_percentage"] == 100.0

    def test_check_trailing_silence_skip_ignored_when_not_playing(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        """Guards against a stale check firing after an operator action
        (skip/stop) already changed state."""
        pc, _ = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager
        )
        pc.state = PlaybackState.STOPPED
        pc.current_song_id = 1
        pc._current_trim_point = 100

        pc._check_trailing_silence_skip(150)

        mock_streaming_controller.stop_playback.assert_not_called()


class TestVolumeNormalization:
    """Tests for the loudness-normalization feature."""

    def _make_controller(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager, enabled=True
    ):
        from kbox.loudness import DEFAULT_TARGET_LUFS

        mock_queue_manager.get_queue.return_value = []
        mock_loudness_analyzer = Mock()
        mock_config_manager.get_bool.return_value = enabled
        mock_config_manager.get_float.return_value = DEFAULT_TARGET_LUFS
        pc = PlaybackController(
            mock_queue_manager,
            mock_streaming_controller,
            mock_config_manager,
            loudness_analyzer=mock_loudness_analyzer,
        )
        pc._monitoring = False
        return pc, mock_loudness_analyzer

    def test_lookup_volume_gain_no_analyzer(self, playback_controller):
        """No analyzer configured -- always 0.0, feature is a no-op."""
        assert playback_controller.loudness_analyzer is None
        assert playback_controller._lookup_volume_gain_db("youtube:abc") == 0.0

    def test_lookup_volume_gain_disabled_by_config(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        from kbox.loudness import LoudnessInfo

        pc, mock_analyzer = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, enabled=False
        )
        mock_analyzer.get_cached_loudness.return_value = LoudnessInfo(
            integrated_lufs=-10.0, true_peak_dbtp=-3.0
        )

        assert pc._lookup_volume_gain_db("youtube:abc") == 0.0
        mock_analyzer.get_cached_loudness.assert_not_called()

    def test_lookup_volume_gain_unmeasured_video(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        """A freshly-downloaded video without a measurement yet plays unadjusted."""
        pc, mock_analyzer = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, enabled=True
        )
        mock_analyzer.get_cached_loudness.return_value = None

        assert pc._lookup_volume_gain_db("youtube:abc") == 0.0

    def test_lookup_volume_gain_computes_gain_from_measurement(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        from kbox.loudness import LoudnessInfo

        pc, mock_analyzer = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, enabled=True
        )
        mock_analyzer.get_cached_loudness.return_value = LoudnessInfo(
            integrated_lufs=-10.0, true_peak_dbtp=-3.0
        )

        gain = pc._lookup_volume_gain_db("youtube:abc")

        assert gain == pytest.approx(-6.0, abs=0.01)
        mock_analyzer.get_cached_loudness.assert_called_once_with("youtube:abc")

    def test_lookup_volume_gain_swallows_analyzer_errors(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        pc, mock_analyzer = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, enabled=True
        )
        mock_analyzer.get_cached_loudness.side_effect = Exception("db error")

        assert pc._lookup_volume_gain_db("youtube:abc") == 0.0

    def test_play_song_applies_volume_gain(
        self, mock_queue_manager, mock_streaming_controller, mock_config_manager
    ):
        from kbox.loudness import LoudnessInfo

        pc, mock_analyzer = self._make_controller(
            mock_queue_manager, mock_streaming_controller, mock_config_manager, enabled=True
        )
        mock_analyzer.get_cached_loudness.return_value = LoudnessInfo(
            integrated_lufs=-10.0, true_peak_dbtp=-3.0
        )

        song = create_mock_queue_item(id=1, video_id="youtube:abc", content_status="ready")
        mock_queue_manager.get_song_at_offset.return_value = song

        pc.play()

        mock_streaming_controller.set_volume_gain_db.assert_called_once()
        (gain_db,), _ = mock_streaming_controller.set_volume_gain_db.call_args
        assert gain_db == pytest.approx(-6.0, abs=0.01)


# =============================================================================
# Locking contract (_locked / _require_locked)
# =============================================================================


class TestLockingContract:
    """Tests for the explicit playback-lock contract.

    Several PlaybackController methods document "assumes lock is already
    held". _require_locked() turns that convention into a runtime check, and
    _locked() records the owning thread so the check can tell "this thread
    holds it" from "somebody holds it".
    """

    def test_internal_method_raises_without_lock(self, playback_controller):
        """Calling a lock-requiring internal without the lock raises."""
        with pytest.raises(AssertionError, match="requires self.lock"):
            playback_controller._stop_internal()

    def test_internal_method_allowed_under_locked(
        self, playback_controller, mock_streaming_controller
    ):
        """The same call succeeds inside _locked()."""
        with playback_controller._locked():
            playback_controller._stop_internal()

        assert playback_controller.state == PlaybackState.STOPPED
        mock_streaming_controller.stop_playback.assert_called_once()

    def test_raises_when_a_different_thread_holds_the_lock(self, playback_controller):
        """The lock being held by *another* thread does not satisfy the check.

        This is the case a bare `self.lock.locked()` assertion would miss:
        the lock is held, just not by us. Sequenced with events, no sleeping.
        """
        lock_acquired = threading.Event()
        release_lock = threading.Event()

        def holder():
            with playback_controller._locked():
                lock_acquired.set()
                release_lock.wait(timeout=10)

        thread = threading.Thread(target=holder, name="LockHolder", daemon=True)
        thread.start()
        try:
            assert lock_acquired.wait(timeout=10), "holder thread never acquired the lock"
            # The lock is genuinely held - just not by this thread.
            assert playback_controller.lock.locked()
            assert playback_controller._lock_owner == thread.ident

            with pytest.raises(AssertionError, match="requires self.lock"):
                playback_controller._require_locked()
        finally:
            release_lock.set()
            thread.join(timeout=10)

        assert not thread.is_alive()
        # Owner is cleared on exit, so the check fails again for everyone.
        assert playback_controller._lock_owner is None
        with pytest.raises(AssertionError, match="requires self.lock"):
            playback_controller._require_locked()

    def test_locked_clears_owner_on_exception(self, playback_controller):
        """_locked() releases the lock and clears the owner even on error."""
        with pytest.raises(RuntimeError):
            with playback_controller._locked():
                raise RuntimeError("boom")

        assert playback_controller._lock_owner is None
        assert not playback_controller.lock.locked()
