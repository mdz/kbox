"""
Unit tests for PlaybackController.

Uses mocks for dependencies.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from kbox.playback import PlaybackController, PlaybackState
from kbox.queue import QueueManager


@pytest.fixture
def mock_queue_manager():
    """Create a mock QueueManager."""
    return Mock(spec=QueueManager)


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


@pytest.fixture
def playback_controller(mock_queue_manager, mock_streaming_controller, mock_config_manager):
    """Create a PlaybackController instance."""
    # Mock get_queue to return empty list to avoid thread issues
    mock_queue_manager.get_queue.return_value = []
    mock_queue_manager.database = Mock()
    mock_queue_manager.database.get_connection.return_value.cursor.return_value.fetchone.return_value = None
    
    controller = PlaybackController(
        mock_queue_manager,
        mock_streaming_controller,
        mock_config_manager
    )
    # Stop position tracking thread
    controller._tracking_position = False
    # Wait a moment for threads to stop
    import time
    time.sleep(0.1)
    return controller


def test_initial_state(playback_controller):
    """Test initial playback state."""
    assert playback_controller.state == PlaybackState.IDLE
    assert playback_controller.current_song is None


def test_play_no_ready_songs(playback_controller, mock_queue_manager):
    """Test play when no ready songs in queue."""
    mock_queue_manager.get_next_song.return_value = None
    
    result = playback_controller.play()
    
    assert result is False
    assert playback_controller.state == PlaybackState.IDLE


def test_play_with_ready_song(playback_controller, mock_queue_manager, 
                              mock_streaming_controller):
    """Test playing a ready song."""
    mock_song = {
        'id': 1,
        'title': 'Test Song',
        'user_name': 'Alice',
        'download_path': '/path/to/video.mp4',
        'pitch_semitones': 2,
        'download_status': QueueManager.STATUS_READY,
        'played_at': None,
        'playback_position_seconds': 0
    }
    # Mock get_queue to return the song
    mock_queue_manager.get_queue.return_value = [mock_song]
    
    result = playback_controller.play()
    
    assert result is True
    assert playback_controller.state == PlaybackState.PLAYING
    assert playback_controller.current_song == mock_song
    mock_streaming_controller.set_pitch_shift.assert_called_once_with(2)
    mock_streaming_controller.load_file.assert_called_once_with('/path/to/video.mp4')
    # Song should NOT be marked as played when it starts - only when it finishes
    mock_queue_manager.mark_played.assert_not_called()


def test_play_no_download_path(playback_controller, mock_queue_manager):
    """Test play when song has no download path."""
    mock_song = {
        'id': 1,
        'title': 'Test Song',
        'user_name': 'Alice',
        'download_path': None
    }
    mock_queue_manager.get_next_song.return_value = mock_song
    
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
    playback_controller.current_song = {
        'id': 1,
        'title': 'Current Song',
        'user_name': 'Alice',
        'youtube_video_id': 'abc123',
        'pitch_semitones': 0,
        'playback_position_seconds': 0
    }
    playback_controller.state = PlaybackState.PLAYING
    
    mock_next_song = {
        'id': 2,
        'title': 'Next Song',
        'user_name': 'Bob',
        'download_path': '/path/to/next.mp4',
        'pitch_semitones': 0,
        'download_status': QueueManager.STATUS_READY,
        'played_at': None,
        'playback_position_seconds': 0
    }
    # Mock get_next_song_after to return the next song
    mock_queue_manager.get_next_song_after.return_value = mock_next_song
    
    result = playback_controller.skip()
    
    assert result is True
    # IMPORTANT: skip() must call stop_playback() (returns to idle) NOT stop() (destroys pipeline)
    mock_streaming_controller.stop_playback.assert_called_once()
    mock_streaming_controller.stop.assert_not_called()
    mock_streaming_controller.load_file.assert_called_once_with('/path/to/next.mp4')


def test_skip_no_next_song(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test skip when no next song available.
    
    When there's no next song, skip() should return False without stopping
    the current song, so playback continues.
    """
    current_song = {
        'id': 1,
        'title': 'Current Song',
        'user_name': 'Alice',
        'youtube_video_id': 'abc123',
        'pitch_semitones': 0,
        'playback_position_seconds': 0,
        'download_status': QueueManager.STATUS_READY,
        'played_at': None
    }
    playback_controller.current_song = current_song
    playback_controller.state = PlaybackState.PLAYING
    # Mock get_next_song_after to return None (no next song)
    mock_queue_manager.get_next_song_after.return_value = None
    
    result = playback_controller.skip()
    
    assert result is False
    # State should remain PLAYING - we don't stop current song if there's no next
    assert playback_controller.state == PlaybackState.PLAYING
    # Should not have stopped playback
    mock_streaming_controller.stop_playback.assert_not_called()


def test_set_pitch(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test setting pitch for current song."""
    playback_controller.current_song = {'id': 1, 'pitch_semitones': 0}
    playback_controller.state = PlaybackState.PLAYING
    
    result = playback_controller.set_pitch(3)
    
    assert result is True
    assert playback_controller.current_song['pitch_semitones'] == 3
    mock_queue_manager.update_pitch.assert_called_once_with(1, 3)
    mock_streaming_controller.set_pitch_shift.assert_called_once_with(3)


def test_set_pitch_no_current_song(playback_controller):
    """Test setting pitch when no current song."""
    playback_controller.current_song = None
    
    result = playback_controller.set_pitch(3)
    
    assert result is False


def test_on_song_end(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test handling end of song."""
    playback_controller.current_song = {
        'id': 1,
        'title': 'Song 1',
        'user_name': 'Alice',
        'youtube_video_id': 'abc123',
        'pitch_semitones': 0,
        'playback_position_seconds': 0
    }
    playback_controller.state = PlaybackState.PLAYING
    
    mock_next_song = {
        'id': 2,
        'title': 'Song 2',
        'user_name': 'Bob',
        'download_path': '/path/to/next.mp4',
        'pitch_semitones': 0,
        'download_status': QueueManager.STATUS_READY,
        'played_at': None,
        'playback_position_seconds': 0
    }
    # Mock get_queue to return the next song
    mock_queue_manager.get_queue.return_value = [mock_next_song]
    
    playback_controller.on_song_end()
    
    # Should mark current song as played
    mock_queue_manager.mark_played.assert_called_once_with(1)
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
    playback_controller.current_song = {
        'id': 1,
        'title': 'Song 1',
        'user_name': 'Alice',
        'youtube_video_id': 'abc123',
        'pitch_semitones': 0,
        'playback_position_seconds': 0
    }
    playback_controller.state = PlaybackState.PLAYING
    mock_queue_manager.get_queue.return_value = []
    
    playback_controller.on_song_end()
    
    # Should mark current song as played
    mock_queue_manager.mark_played.assert_called_once_with(1)
    assert playback_controller.current_song is None
    assert playback_controller.state == PlaybackState.IDLE
    mock_streaming_controller.set_pitch_shift.assert_called_once_with(0)
    # Should display end-of-queue interstitial image
    mock_streaming_controller.display_image.assert_called_once()


def test_get_status(playback_controller):
    """Test getting playback status."""
    playback_controller.current_song = {'id': 1, 'title': 'Test Song'}
    playback_controller.state = PlaybackState.PLAYING
    
    status = playback_controller.get_status()
    
    assert status['state'] == 'playing'
    assert status['current_song'] == {'id': 1, 'title': 'Test Song'}


def test_jump_to_song_while_playing(playback_controller, mock_queue_manager,
                                    mock_streaming_controller):
    """Test jumping to a song while another song is playing.
    
    This is a regression test for the bug where jump_to_song called stop()
    instead of stop_playback(), which destroyed the pipeline.
    """
    # Set up current song playing
    playback_controller.current_song = {'id': 1, 'title': 'Current Song'}
    playback_controller.state = PlaybackState.PLAYING
    
    mock_song = {
        'id': 2,
        'title': 'New Song',
        'user_name': 'Bob',
        'download_path': '/path/to/new.mp4',
        'pitch_semitones': 0,
        'download_status': QueueManager.STATUS_READY,
        'played_at': None,
        'playback_position_seconds': 0
    }
    mock_queue_manager.get_item.return_value = mock_song
    
    result = playback_controller.jump_to_song(2)
    
    assert result is True
    # IMPORTANT: jump_to_song() must call stop_playback() (returns to idle) NOT stop() (destroys pipeline)
    mock_streaming_controller.stop_playback.assert_called_once()
    mock_streaming_controller.stop.assert_not_called()
    mock_streaming_controller.load_file.assert_called_once_with('/path/to/new.mp4')


