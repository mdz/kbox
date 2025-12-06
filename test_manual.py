#!/usr/bin/env python3
"""
Manual test script for kbox.

This script helps test the system without requiring full hardware setup.
Run with: uv run python test_manual.py
"""

import sys
import logging
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from kbox.database import Database
from kbox.config_manager import ConfigManager
from kbox.queue import QueueManager
from kbox.youtube import YouTubeClient
from kbox.playback import PlaybackController, PlaybackState

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def test_basic_flow():
    """Test basic queue and playback flow."""
    print("\n=== Testing Basic Queue Flow ===\n")
    
    # Setup
    db = Database(db_path=':memory:')  # In-memory database
    config = ConfigManager(db)
    config.set('youtube_api_key', 'test_key')  # Will be mocked
    
    queue = QueueManager(db)
    
    # Mock YouTube client
    from unittest.mock import Mock, patch
    with patch('kbox.youtube.build'):
        youtube = YouTubeClient('test_key')
        youtube.youtube = Mock()
    
    # Mock streaming controller
    mock_streaming = Mock()
    mock_streaming.set_pitch_shift = Mock()
    mock_streaming.load_file = Mock()
    mock_streaming.pause = Mock()
    mock_streaming.resume = Mock()
    mock_streaming.stop = Mock()
    mock_streaming.set_eos_callback = Mock()
    
    playback = PlaybackController(queue, youtube, mock_streaming, config)
    playback._monitoring = False  # Stop download monitor
    
    # Test 1: Add songs to queue
    print("1. Adding songs to queue...")
    id1 = queue.add_song('Alice', 'vid1', 'Song 1', duration_seconds=180, pitch_semitones=0)
    id2 = queue.add_song('Bob', 'vid2', 'Song 2', duration_seconds=200, pitch_semitones=1)
    id3 = queue.add_song('Charlie', 'vid3', 'Song 3', duration_seconds=220, pitch_semitones=-1)
    print(f"   ✓ Added 3 songs (IDs: {id1}, {id2}, {id3})")
    
    # Test 2: Check queue
    print("\n2. Checking queue...")
    q = queue.get_queue()
    print(f"   ✓ Queue has {len(q)} items")
    for item in q:
        print(f"     - {item['user_name']}: {item['title']} (status: {item['download_status']})")
    
    # Test 3: Mark songs as ready
    print("\n3. Marking songs as ready...")
    queue.update_download_status(id1, QueueManager.STATUS_READY, download_path='/fake/path1.mp4')
    queue.update_download_status(id2, QueueManager.STATUS_READY, download_path='/fake/path2.mp4')
    queue.update_download_status(id3, QueueManager.STATUS_READY, download_path='/fake/path3.mp4')
    print("   ✓ All songs marked as ready")
    
    # Test 4: Play first song
    print("\n4. Playing first song...")
    result = playback.play()
    assert result, "Play should succeed"
    assert playback.state == PlaybackState.PLAYING, "Should be playing"
    assert playback.current_song['id'] == id1, "Should be playing first song"
    print(f"   ✓ Playing: {playback.current_song['title']} by {playback.current_song['user_name']}")
    
    # Test 5: Adjust pitch
    print("\n5. Adjusting pitch...")
    playback.set_pitch(3)
    item = queue.get_item(id1)
    assert item['pitch_semitones'] == 3, "Pitch should be updated"
    print(f"   ✓ Pitch adjusted to {item['pitch_semitones']} semitones")
    
    # Test 6: Skip to next song
    print("\n6. Skipping to next song...")
    result = playback.skip()
    assert result, "Skip should succeed"
    assert playback.current_song['id'] == id2, "Should be playing second song"
    print(f"   ✓ Now playing: {playback.current_song['title']} by {playback.current_song['user_name']}")
    
    # Test 7: Pause and resume
    print("\n7. Pausing and resuming...")
    playback.pause()
    assert playback.state == PlaybackState.PAUSED, "Should be paused"
    print("   ✓ Paused")
    playback.play()  # Resume
    assert playback.state == PlaybackState.PLAYING, "Should be playing"
    print("   ✓ Resumed")
    
    # Test 8: Song end transition
    print("\n8. Simulating song end...")
    playback.on_song_end()
    assert playback.current_song['id'] == id3, "Should transition to third song"
    print(f"   ✓ Transitioned to: {playback.current_song['title']} by {playback.current_song['user_name']}")
    
    # Test 9: Reorder queue
    print("\n9. Reordering queue...")
    queue.reorder_song(id1, 3)  # Move first song to last
    q = queue.get_queue()
    assert q[2]['id'] == id1, "First song should be last"
    print("   ✓ Reordered queue successfully")
    
    # Test 10: Clear queue
    print("\n10. Clearing queue...")
    count = queue.clear_queue()
    assert count == 3, "Should clear 3 items"
    assert len(queue.get_queue()) == 0, "Queue should be empty"
    print(f"   ✓ Cleared {count} items")
    
    print("\n=== All Tests Passed! ===\n")
    
    # Cleanup
    playback.stop()
    db.close()

def test_config():
    """Test configuration management."""
    print("\n=== Testing Configuration ===\n")
    
    db = Database(db_path=':memory:')
    config = ConfigManager(db)
    
    # Test setting and getting
    config.set('operator_pin', '5678')
    assert config.get('operator_pin') == '5678'
    print("✓ Set and get operator_pin")
    
    # Test type conversion
    config.set('test_int', '42')
    assert config.get_int('test_int') == 42
    print("✓ Integer conversion")
    
    config.set('test_float', '3.14')
    assert config.get_float('test_float') == 3.14
    print("✓ Float conversion")
    
    config.set('test_bool', 'true')
    assert config.get_bool('test_bool') is True
    print("✓ Boolean conversion")
    
    print("\n=== Configuration Tests Passed! ===\n")
    db.close()

if __name__ == '__main__':
    try:
        test_config()
        test_basic_flow()
        print("✅ All manual tests completed successfully!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

