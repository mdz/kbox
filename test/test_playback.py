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
def mock_youtube_client():
    """Create a mock YouTubeClient."""
    return Mock()


@pytest.fixture
def mock_streaming_controller():
    """Create a mock StreamingController."""
    controller = Mock()
    controller.set_pitch_shift = Mock()
    controller.load_file = Mock()
    controller.pause = Mock()
    controller.resume = Mock()
    controller.stop = Mock()
    controller.set_eos_callback = Mock()
    return controller


@pytest.fixture
def mock_config_manager():
    """Create a mock ConfigManager."""
    return Mock()


@pytest.fixture
def playback_controller(mock_queue_manager, mock_youtube_client, 
                       mock_streaming_controller, mock_config_manager):
    """Create a PlaybackController instance."""
    controller = PlaybackController(
        mock_queue_manager,
        mock_youtube_client,
        mock_streaming_controller,
        mock_config_manager
    )
    # Stop the download monitor thread
    controller._monitoring = False
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
        'pitch_semitones': 2
    }
    mock_queue_manager.get_next_song.return_value = mock_song
    
    result = playback_controller.play()
    
    assert result is True
    assert playback_controller.state == PlaybackState.PLAYING
    assert playback_controller.current_song == mock_song
    mock_streaming_controller.set_pitch_shift.assert_called_once_with(2)
    mock_streaming_controller.load_file.assert_called_once_with('/path/to/video.mp4')
    mock_queue_manager.mark_played.assert_called_once_with(1)


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
    playback_controller.current_song = {'id': 1}
    playback_controller.state = PlaybackState.PLAYING
    
    mock_next_song = {
        'id': 2,
        'title': 'Next Song',
        'user_name': 'Bob',
        'download_path': '/path/to/next.mp4',
        'pitch_semitones': 0
    }
    mock_queue_manager.get_next_song.return_value = mock_next_song
    
    result = playback_controller.skip()
    
    assert result is True
    mock_streaming_controller.stop.assert_called_once()
    mock_streaming_controller.load_file.assert_called_once_with('/path/to/next.mp4')


def test_skip_no_next_song(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test skip when no next song available."""
    playback_controller.current_song = {'id': 1}
    playback_controller.state = PlaybackState.PLAYING
    mock_queue_manager.get_next_song.return_value = None
    
    result = playback_controller.skip()
    
    assert result is False
    assert playback_controller.state == PlaybackState.IDLE


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
    playback_controller.current_song = {'id': 1, 'title': 'Song 1'}
    playback_controller.state = PlaybackState.PLAYING
    
    mock_next_song = {
        'id': 2,
        'title': 'Song 2',
        'user_name': 'Bob',
        'download_path': '/path/to/next.mp4',
        'pitch_semitones': 0
    }
    mock_queue_manager.get_next_song.return_value = mock_next_song
    
    playback_controller.on_song_end()
    
    # Should reset pitch
    mock_streaming_controller.set_pitch_shift.assert_any_call(0)
    # Should load next song
    mock_streaming_controller.load_file.assert_called_once_with('/path/to/next.mp4')
    assert playback_controller.current_song == mock_next_song


def test_on_song_end_no_next(playback_controller, mock_queue_manager, mock_streaming_controller):
    """Test end of song when no next song."""
    playback_controller.current_song = {'id': 1, 'title': 'Song 1'}
    playback_controller.state = PlaybackState.PLAYING
    mock_queue_manager.get_next_song.return_value = None
    
    playback_controller.on_song_end()
    
    assert playback_controller.current_song is None
    assert playback_controller.state == PlaybackState.IDLE
    mock_streaming_controller.set_pitch_shift.assert_called_once_with(0)


def test_get_status(playback_controller):
    """Test getting playback status."""
    playback_controller.current_song = {'id': 1, 'title': 'Test Song'}
    playback_controller.state = PlaybackState.PLAYING
    
    status = playback_controller.get_status()
    
    assert status['state'] == 'playing'
    assert status['current_song'] == {'id': 1, 'title': 'Test Song'}


def test_download_status_callback(playback_controller, mock_queue_manager):
    """Test download status callback."""
    item_id = 1
    download_path = '/path/to/video.mp4'
    
    # Simulate download completion
    playback_controller._on_download_status(item_id, 'ready', download_path, None)
    
    mock_queue_manager.update_download_status.assert_called_once_with(
        item_id,
        QueueManager.STATUS_READY,
        download_path=download_path
    )


def test_download_status_error(playback_controller, mock_queue_manager):
    """Test download status callback with error."""
    item_id = 1
    error_message = 'Download failed'
    
    playback_controller._on_download_status(item_id, 'error', None, error_message)
    
    mock_queue_manager.update_download_status.assert_called_once_with(
        item_id,
        QueueManager.STATUS_ERROR,
        error_message=error_message
    )


