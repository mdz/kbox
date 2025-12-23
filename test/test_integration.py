"""
Integration tests for kbox.

Tests the integration between components without external dependencies.
"""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from kbox.database import Database
from kbox.config_manager import ConfigManager
from kbox.queue import QueueManager
from kbox.user import UserManager
from kbox.youtube import YouTubeClient
from kbox.playback import PlaybackController, PlaybackState

# Test user IDs
ALICE_ID = 'alice-uuid-1234'
BOB_ID = 'bob-uuid-5678'
CHARLIE_ID = 'charlie-uuid-9012'


@pytest.fixture
def temp_db():
    """Create a temporary database."""
    fd, path = tempfile.mkstemp(suffix='.db')
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
def full_system(temp_db, temp_cache_dir):
    """Create a full system with all components."""
    # Config manager
    config_manager = ConfigManager(temp_db)
    config_manager.set('youtube_api_key', 'test_key')
    config_manager.set('cache_directory', temp_cache_dir)
    config_manager.set('transition_duration_seconds', '0')  # No transition delay in tests
    
    # YouTube client (mocked)
    with patch('kbox.youtube.build') as mock_build:
        mock_youtube = Mock()
        mock_build.return_value = mock_youtube
        youtube_client = YouTubeClient('test_key', cache_directory=temp_cache_dir)
        youtube_client.youtube = mock_youtube
    
    # User manager
    user_manager = UserManager(temp_db)
    # Create test users
    alice = user_manager.get_or_create_user(ALICE_ID, 'Alice')
    bob = user_manager.get_or_create_user(BOB_ID, 'Bob')
    charlie = user_manager.get_or_create_user(CHARLIE_ID, 'Charlie')
    
    # Queue manager (with mocked youtube client, but no download monitor for tests)
    queue_manager = QueueManager(temp_db)  # No youtube_client = no download monitor
    
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
        queue_manager,
        mock_streaming,
        config_manager
    )
    
    return {
        'config': config_manager,
        'queue': queue_manager,
        'user': user_manager,
        'users': {'alice': alice, 'bob': bob, 'charlie': charlie},
        'youtube': youtube_client,
        'streaming': mock_streaming,
        'playback': playback_controller
    }


def test_add_song_to_queue_and_play(full_system):
    """Test adding a song to queue and playing it."""
    system = full_system
    
    # Add song to queue
    item_id = system['queue'].add_song(
        user=system['users']['alice'],
        source='youtube',
        source_id='test123',
        title='Test Song',
        duration_seconds=180,
        pitch_semitones=2
    )
    
    assert item_id == 1
    
    # Mark as ready
    system['queue'].update_download_status(
        item_id,
        QueueManager.STATUS_READY,
        download_path='/fake/path/to/video.mp4'
    )
    
    # Try to play
    result = system['playback'].play()
    
    assert result is True
    assert system['playback'].state == PlaybackState.PLAYING
    assert system['playback'].current_song_id is not None
    assert system['playback'].current_song_id == item_id
    
    # Verify streaming controller was called
    system['streaming'].set_pitch_shift.assert_called_once_with(2)
    system['streaming'].load_file.assert_called_once_with('/fake/path/to/video.mp4')


def test_queue_persistence_across_restarts(temp_db):
    """Test that queue persists when components are recreated."""
    # Create first system
    config1 = ConfigManager(temp_db)
    user1 = UserManager(temp_db)
    alice = user1.get_or_create_user(ALICE_ID, 'Alice')
    bob = user1.get_or_create_user(BOB_ID, 'Bob')
    queue1 = QueueManager(temp_db)
    
    # Add songs
    id1 = queue1.add_song(alice, 'youtube', 'vid1', 'Song 1')
    id2 = queue1.add_song(bob, 'youtube', 'vid2', 'Song 2')
    queue1.update_download_status(id1, QueueManager.STATUS_READY, download_path='/path1')
    
    # Create second system (simulating restart)
    config2 = ConfigManager(temp_db)
    queue2 = QueueManager(temp_db)
    
    # Verify queue persisted
    queue = queue2.get_queue()
    assert len(queue) == 2
    assert queue[0].user_id == ALICE_ID
    assert queue[1].user_id == BOB_ID
    assert queue[0].download_status == QueueManager.STATUS_READY


def test_playback_state_transitions(full_system):
    """Test playback state transitions."""
    system = full_system
    
    # Start in idle
    assert system['playback'].state == PlaybackState.IDLE
    
    # Add and mark ready
    item_id = system['queue'].add_song(system['users']['alice'], 'youtube', 'vid1', 'Song 1')
    system['queue'].update_download_status(
        item_id, QueueManager.STATUS_READY, download_path='/fake/path.mp4'
    )
    
    # Play
    system['playback'].play()
    assert system['playback'].state == PlaybackState.PLAYING
    
    # Pause
    system['playback'].pause()
    assert system['playback'].state == PlaybackState.PAUSED
    
    # Resume
    system['playback'].play()
    assert system['playback'].state == PlaybackState.PLAYING


def test_pitch_adjustment_during_playback(full_system):
    """Test pitch adjustment for current song."""
    system = full_system
    
    # Add and play song
    item_id = system['queue'].add_song(system['users']['alice'], 'youtube', 'vid1', 'Song 1', pitch_semitones=0)
    system['queue'].update_download_status(
        item_id, QueueManager.STATUS_READY, download_path='/fake/path.mp4'
    )
    system['playback'].play()
    
    # Adjust pitch
    result = system['playback'].set_pitch(3)
    assert result is True
    
    # Verify pitch was updated in queue
    item = system['queue'].get_item(item_id)
    assert item.settings.pitch_semitones == 3
    
    # Verify streaming controller was called
    system['streaming'].set_pitch_shift.assert_called_with(3)


def test_song_transition_on_end(full_system):
    """Test automatic transition to next song on end."""
    import time
    system = full_system
    
    # Add two songs
    id1 = system['queue'].add_song(system['users']['alice'], 'youtube', 'vid1', 'Song 1')
    id2 = system['queue'].add_song(system['users']['bob'], 'youtube', 'vid2', 'Song 2')
    
    system['queue'].update_download_status(
        id1, QueueManager.STATUS_READY, download_path='/fake/path1.mp4'
    )
    system['queue'].update_download_status(
        id2, QueueManager.STATUS_READY, download_path='/fake/path2.mp4'
    )
    
    # Play first song
    system['playback'].play()
    assert system['playback'].current_song_id == id1
    
    # Simulate end of song
    system['playback'].on_song_end()
    
    # Wait for transition timer (set to 0 seconds in fixture)
    time.sleep(0.1)
    
    # Should transition to next song
    assert system['playback'].current_song_id is not None
    assert system['playback'].current_song_id == id2
    assert system['playback'].state == PlaybackState.PLAYING


def test_skip_to_next_song(full_system):
    """Test skipping to next song."""
    system = full_system
    
    # Add two songs
    id1 = system['queue'].add_song(system['users']['alice'], 'youtube', 'vid1', 'Song 1')
    id2 = system['queue'].add_song(system['users']['bob'], 'youtube', 'vid2', 'Song 2')
    
    system['queue'].update_download_status(
        id1, QueueManager.STATUS_READY, download_path='/fake/path1.mp4'
    )
    system['queue'].update_download_status(
        id2, QueueManager.STATUS_READY, download_path='/fake/path2.mp4'
    )
    
    # Play first song
    system['playback'].play()
    assert system['playback'].current_song_id == id1
    
    # Skip
    result = system['playback'].skip()
    assert result is True
    assert system['playback'].current_song_id == id2


def test_queue_reordering(full_system):
    """Test reordering songs in queue."""
    system = full_system
    
    # Add three songs
    id1 = system['queue'].add_song(system['users']['alice'], 'youtube', 'vid1', 'Song 1')
    id2 = system['queue'].add_song(system['users']['bob'], 'youtube', 'vid2', 'Song 2')
    id3 = system['queue'].add_song(system['users']['charlie'], 'youtube', 'vid3', 'Song 3')
    
    # Move last to first
    result = system['queue'].reorder_song(id3, 1)
    assert result is True
    
    queue = system['queue'].get_queue()
    assert queue[0].id == id3
    assert queue[1].id == id1
    assert queue[2].id == id2


def test_config_persistence(temp_db):
    """Test configuration persistence."""
    # Set config
    config1 = ConfigManager(temp_db)
    config1.set('operator_pin', '9999')
    config1.set('custom_key', 'custom_value')
    
    # Recreate config manager
    config2 = ConfigManager(temp_db)
    
    # Verify persistence
    assert config2.get('operator_pin') == '9999'
    assert config2.get('custom_key') == 'custom_value'

