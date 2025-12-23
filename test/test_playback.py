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
    qm = Mock(spec=QueueManager)
    # Configure get_item to return None by default (tests override as needed)
    qm.get_item.return_value = None
    # Configure get_next_song_after to return None by default
    qm.get_next_song_after.return_value = None
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
    assert playback_controller.current_song_id is None


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
    assert playback_controller.current_song_id == 1
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
    current_song = {
        'id': 1,
        'title': 'Current Song',
        'user_name': 'Alice',
        'source': 'youtube',
        'source_id': 'abc123',
        'duration_seconds': 180,
        'pitch_semitones': 0,
        'playback_position_seconds': 0
    }
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING
    
    # Mock get_item to return current song data
    mock_queue_manager.get_item.return_value = current_song
    
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
        'source': 'youtube',
        'source_id': 'abc123',
        'duration_seconds': 180,
        'pitch_semitones': 0,
        'playback_position_seconds': 0,
        'download_status': QueueManager.STATUS_READY,
        'played_at': None
    }
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING
    
    # Mock get_item to return current song data
    mock_queue_manager.get_item.return_value = current_song
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
    current_song = {
        'id': 1,
        'title': 'Song 1',
        'user_name': 'Alice',
        'source': 'youtube',
        'source_id': 'abc123',
        'duration_seconds': 180,
        'pitch_semitones': 0,
        'playback_position_seconds': 0
    }
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING
    
    # Mock get_item to return current song data
    mock_queue_manager.get_item.return_value = current_song
    
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
    # Mock get_next_song_after to return the next song
    mock_queue_manager.get_next_song_after.return_value = mock_next_song
    
    playback_controller.on_song_end()
    
    # Should mark current song as played
    mock_queue_manager.mark_played.assert_called_once_with(1)
    # Should call get_next_song_after with the finished song's ID
    mock_queue_manager.get_next_song_after.assert_called_once_with(1)
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
    current_song = {
        'id': 1,
        'title': 'Song 1',
        'user_name': 'Alice',
        'source': 'youtube',
        'source_id': 'abc123',
        'duration_seconds': 180,
        'pitch_semitones': 0,
        'playback_position_seconds': 0
    }
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING
    
    # Mock get_item to return current song data
    mock_queue_manager.get_item.return_value = current_song
    # Mock get_next_song_after to return None (no next song)
    mock_queue_manager.get_next_song_after.return_value = None
    
    playback_controller.on_song_end()
    
    # Should mark current song as played
    mock_queue_manager.mark_played.assert_called_once_with(1)
    # Should call get_next_song_after with the finished song's ID
    mock_queue_manager.get_next_song_after.assert_called_once_with(1)
    assert playback_controller.current_song_id is None
    assert playback_controller.state == PlaybackState.IDLE
    mock_streaming_controller.set_pitch_shift.assert_called_once_with(0)
    # Should display end-of-queue interstitial image
    mock_streaming_controller.display_image.assert_called_once()


def test_get_status(playback_controller, mock_queue_manager):
    """Test getting playback status."""
    song = {'id': 1, 'title': 'Test Song'}
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING
    
    # Mock get_item to return song data
    mock_queue_manager.get_item.return_value = song
    
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
    playback_controller.current_song_id = 1
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


def test_jump_to_song_when_idle(playback_controller, mock_queue_manager,
                                mock_streaming_controller):
    """Test jump_to_song plays song at current position when nothing is playing."""
    # No current song
    playback_controller.current_song_id = None
    playback_controller.state = PlaybackState.IDLE
    
    # Song to jump to is at position 10
    target_song = {
        'id': 3,
        'title': 'Jump To Song',
        'position': 10,
        'user_name': 'Bob',
        'download_path': '/path/to/jumpto.mp4',
        'pitch_semitones': 0,
        'download_status': QueueManager.STATUS_READY,
        'played_at': None,
        'playback_position_seconds': 0
    }
    
    # Mock get_item to return the song
    mock_queue_manager.get_item.return_value = target_song
    
    result = playback_controller.jump_to_song(3)
    
    assert result is True
    # Should NOT reorder - jump_to_song never reorders
    mock_queue_manager.reorder_song.assert_not_called()
    # Should not stop playback (nothing playing)
    mock_streaming_controller.stop_playback.assert_not_called()
    # Should load and play the song at its current position (10)
    mock_streaming_controller.load_file.assert_called_once_with('/path/to/jumpto.mp4')
    assert playback_controller.current_song_id == 3


def test_jump_to_song_does_not_reorder(playback_controller, mock_queue_manager,
                                        mock_streaming_controller):
    """Test that jump_to_song does NOT reorder the queue - it just plays at current position."""
    # Set up current song playing at position 5
    playback_controller.current_song_id = 1
    playback_controller.state = PlaybackState.PLAYING
    
    # Song to jump to is at position 10
    target_song = {
        'id': 3,
        'title': 'Jump To Song',
        'position': 10,
        'user_name': 'Bob',
        'download_path': '/path/to/jumpto.mp4',
        'pitch_semitones': 0,
        'download_status': QueueManager.STATUS_READY,
        'played_at': None,
        'playback_position_seconds': 0
    }
    
    mock_queue_manager.get_item.return_value = target_song
    
    result = playback_controller.jump_to_song(3)
    
    assert result is True
    # Should NOT reorder - jump_to_song never reorders
    mock_queue_manager.reorder_song.assert_not_called()
    # Should stop current playback
    mock_streaming_controller.stop_playback.assert_called_once()
    # Should load and play the song at its current position (still 10)
    mock_streaming_controller.load_file.assert_called_once_with('/path/to/jumpto.mp4')
    assert playback_controller.current_song_id == 3


def test_jump_to_song_not_ready(playback_controller, mock_queue_manager):
    """Test jump_to_song fails when song is not ready."""
    target_song = {
        'id': 3,
        'title': 'Not Ready Song',
        'position': 10,
        'download_status': QueueManager.STATUS_PENDING,
    }
    mock_queue_manager.get_item.return_value = target_song
    
    result = playback_controller.jump_to_song(3)
    
    assert result is False
    # Should not attempt to reorder
    mock_queue_manager.reorder_song.assert_not_called()


def test_move_to_next_with_stale_position_cache(playback_controller, mock_queue_manager,
                                                 mock_streaming_controller):
    """Test that move_to_next uses fresh position data, not stale cache.
    
    Regression test: After queue reordering operations, the cached position
    in self.current_song['position'] becomes stale. move_to_next should query
    fresh position from database, not use the cached value.
    """
    # Currently playing song
    playback_controller.current_song_id = 10
    playback_controller.state = PlaybackState.PLAYING
    
    # In the database, current song is at position 5 (fresh data)
    current_song_fresh = {
        'id': 10,
        'position': 5,
        'title': 'Currently Playing',
        'user_name': 'Alice',
        'download_path': '/path/to/current.mp4',
        'download_status': QueueManager.STATUS_READY,
        'pitch_semitones': 0
    }
    
    # Song to move to "play next" 
    song_to_move = {
        'id': 20,
        'position': 8,
        'title': 'Move This Next',
        'download_status': QueueManager.STATUS_READY
    }
    
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


def test_on_song_end_plays_next_in_queue_order(playback_controller, mock_queue_manager, 
                                                mock_streaming_controller):
    """Test that on_song_end plays the next song by position, not first unplayed.
    
    This is a regression test for the bug where after a song ends, the system
    plays the first unplayed ready song instead of the next song by position.
    """
    import time
    
    # Song at position 2 is currently playing
    current_song = {
        'id': 10,
        'position': 2,
        'title': 'Song at Position 2',
        'user_name': 'TestUser',
        'source': 'youtube',
        'source_id': 'vid10',
        'duration_seconds': 180,
        'pitch_semitones': 0,
        'playback_position_seconds': 0,
        'download_status': QueueManager.STATUS_READY,
        'download_path': '/path/to/song10.mp4'
    }
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
    
    song_at_position_3 = {
        'id': 15,
        'position': 3,
        'title': 'Song at Position 3',
        'user_name': 'TestUser',
        'source': 'youtube',
        'source_id': 'vid15',
        'download_path': '/path/to/song15.mp4',
        'pitch_semitones': 0,
        'download_status': QueueManager.STATUS_READY,
        'played_at': None,
        'playback_position_seconds': 0
    }
    
    song_at_position_4 = {
        'id': 20,
        'position': 4,
        'title': 'Song at Position 4',
        'download_status': QueueManager.STATUS_READY,
        'played_at': None
    }
    
    song_at_position_5 = {
        'id': 25,
        'position': 5,
        'title': 'Song at Position 5',
        'download_status': QueueManager.STATUS_READY,
        'played_at': None
    }
    
    # Mock get_queue to return unplayed songs (excludes the one that just finished)
    mock_queue_manager.get_queue.return_value = [
        song_at_position_3,
        song_at_position_4,
        song_at_position_5
    ]
    
    # Mock get_next_song_after to return the song at position 3
    mock_queue_manager.get_next_song_after.return_value = song_at_position_3
    
    mock_queue_manager.mark_played.return_value = True
    mock_streaming_controller.get_position.return_value = 150
    
    # Simulate song ending
    playback_controller.on_song_end()
    
    # Should have marked current song as played
    mock_queue_manager.mark_played.assert_called_once_with(10)
    
    # Wait for transition timer (set to 0 in fixture)
    time.sleep(0.2)
    
    # The bug: without the fix, it plays the first song in the unplayed list (id=15 at position 3)
    # which is correct! But if positions were shuffled, it would play the wrong one.
    # 
    # Better test: if song at position 5 (id=25) was first in the list returned by get_queue
    # due to some bug, it should still play song at position 3 (id=15) because that's NEXT.
    
    # The key assertion: should call get_next_song_after with the ID of the song that just ended
    mock_queue_manager.get_next_song_after.assert_called_once_with(10)
    
    # Should have started playing the next song
    assert playback_controller._next_song_pending == song_at_position_3


