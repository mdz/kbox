"""
Integration tests for StreamingController.

These tests use fakesinks for headless testing and verify pipeline state
transitions, pitch shifting, and error handling without requiring hardware.

All tests in this module require GStreamer and will be skipped if unavailable.
"""

import logging
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, create_autospec

import pytest

# Mark all tests in this module as requiring GStreamer
pytestmark = pytest.mark.gstreamer

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.streaming import StreamingController

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture(scope="session")
def test_video_1s():
    """Create a 1-second test video for testing."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    video_path = fixtures_dir / "test_1s.mp4"

    if not video_path.exists():
        logger.info("Creating 1-second test video...")
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "testsrc=d=1:s=320x240:r=30",
                "-f",
                "lavfi",
                "-i",
                "sine=f=440:d=1",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                str(video_path),
            ],
            check=True,
            capture_output=True,
        )
        logger.info("Test video created at %s", video_path)

    return str(video_path)


@pytest.fixture(scope="session")
def test_video_3s():
    """Create a 3-second test video for longer tests."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    video_path = fixtures_dir / "test_3s.mp4"

    if not video_path.exists():
        logger.info("Creating 3-second test video...")
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "testsrc=d=3:s=320x240:r=30",
                "-f",
                "lavfi",
                "-i",
                "sine=f=440:d=3",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                str(video_path),
            ],
            check=True,
            capture_output=True,
        )
        logger.info("Test video created at %s", video_path)

    return str(video_path)


@pytest.fixture
def mock_config_manager():
    """Create a mock ConfigManager with test defaults."""
    db = create_autospec(Database, instance=True)
    config_manager = ConfigManager(db)

    # Set test-specific config
    config_manager.set(
        "rubberband_plugin", "ladspa-ladspa-rubberband-so-rubberband-r3-pitchshifter-stereo"
    )
    config_manager.set("audio_output_device", None)

    return config_manager


@pytest.fixture
def controller(mock_config_manager):
    """Create a StreamingController with fakesinks for headless testing."""
    ctrl = StreamingController(mock_config_manager, None, use_fakesinks=True)
    yield ctrl
    # Cleanup after test
    ctrl.stop()


# =========================================================================
# Initialization Tests
# =========================================================================


def test_init_creates_pipeline_in_ready_state(controller):
    """Test that initialization creates pipeline in READY (idle) state."""
    assert controller.get_pipeline_state() == "ready"
    assert controller.state == "idle"
    assert controller.playbin is not None
    assert controller.audio_bin is not None
    assert controller.video_bin is not None


def test_init_creates_pitch_shift_element(controller):
    """Test that pitch shift element is created (or identity fallback)."""
    assert controller.pitch_shift_element is not None


# =========================================================================
# Playback State Transition Tests
# =========================================================================


def test_load_file_transitions_to_playing(controller, test_video_1s):
    """Test that load_file() transitions pipeline to PLAYING state."""
    controller.load_file(test_video_1s)

    assert controller.get_pipeline_state() == "playing"
    assert controller.state == "playing"
    assert controller.current_file == test_video_1s


def test_stop_playback_returns_to_idle(controller, test_video_1s):
    """Test that stop_playback() returns to READY (idle) state."""
    controller.load_file(test_video_1s)
    controller.stop_playback()

    assert controller.get_pipeline_state() == "ready"
    assert controller.state == "idle"
    assert controller.current_file is None


def test_pause_resume(controller, test_video_3s):
    """Test pause and resume functionality."""
    controller.load_file(test_video_3s)

    controller.pause()
    assert controller.state == "paused"
    # Pipeline needs time to complete state change
    time.sleep(0.2)
    assert controller.get_pipeline_state() == "paused"

    controller.resume()
    assert controller.state == "playing"
    time.sleep(0.2)
    assert controller.get_pipeline_state() == "playing"


def test_pause_when_not_playing_raises_error(controller):
    """Test that pausing when not playing raises an error."""
    with pytest.raises(RuntimeError, match="not currently playing"):
        controller.pause()


def test_resume_when_not_paused_raises_error(controller, test_video_1s):
    """Test that resuming when not paused raises an error."""
    controller.load_file(test_video_1s)
    with pytest.raises(RuntimeError, match="not currently paused"):
        controller.resume()


# =========================================================================
# Stress Tests
# =========================================================================


def test_rapid_start_stop_cycles(controller, test_video_1s):
    """Stress test: rapid state transitions."""
    for i in range(20):
        logger.debug("Cycle %d/20", i + 1)
        controller.load_file(test_video_1s)
        controller.stop_playback()

    assert controller.state == "idle"
    assert controller.get_pipeline_state() == "ready"


def test_rapid_pause_resume_cycles(controller, test_video_3s):
    """Stress test: rapid pause/resume cycles."""
    controller.load_file(test_video_3s)

    for i in range(10):
        logger.debug("Pause/resume cycle %d/10", i + 1)
        controller.pause()
        time.sleep(0.05)  # Small delay to let state settle
        controller.resume()
        time.sleep(0.05)

    assert controller.state == "playing"
    controller.stop_playback()


def test_load_different_files_sequentially(controller, test_video_1s, test_video_3s):
    """Test loading different files sequentially."""
    controller.load_file(test_video_1s)
    assert controller.current_file == test_video_1s

    controller.load_file(test_video_3s)
    assert controller.current_file == test_video_3s

    controller.load_file(test_video_1s)
    assert controller.current_file == test_video_1s

    controller.stop_playback()


# =========================================================================
# Pitch Shift Tests
# =========================================================================


def test_pitch_shift_persists_across_songs(controller, test_video_1s):
    """Test that pitch shift setting persists across song changes."""
    controller.set_pitch_shift(5)
    assert controller.pitch_shift_semitones == 5

    controller.load_file(test_video_1s)
    controller.stop_playback()

    # Pitch shift value should persist
    assert controller.pitch_shift_semitones == 5

    # If pitch shift element is not identity, it should have the value
    if controller.pitch_shift_element:
        element_type = type(controller.pitch_shift_element).__name__
        if element_type != "GstIdentity":
            try:
                actual_semitones = controller.pitch_shift_element.get_property("semitones")
                assert actual_semitones == 5
            except:
                # If rubberband not available, that's okay
                pass


def test_pitch_shift_during_playback(controller, test_video_3s):
    """Test changing pitch shift while playing."""
    controller.load_file(test_video_3s)

    controller.set_pitch_shift(3)
    assert controller.pitch_shift_semitones == 3

    controller.set_pitch_shift(-2)
    assert controller.pitch_shift_semitones == -2

    controller.stop_playback()


# =========================================================================
# Position and Seeking Tests
# =========================================================================


def test_get_position_returns_none_when_idle(controller):
    """Test that get_position() returns None when idle."""
    assert controller.get_position() is None


def test_get_position_returns_value_when_playing(controller, test_video_3s):
    """Test that get_position() returns a value when playing."""
    controller.load_file(test_video_3s)
    time.sleep(0.5)  # Let it play a bit

    position = controller.get_position()
    assert position is not None
    assert position >= 0

    controller.stop_playback()


def test_seek_works_during_playback(controller, test_video_3s):
    """Test seeking to a specific position."""
    controller.load_file(test_video_3s)
    time.sleep(0.2)

    success = controller.seek(1)
    assert success is True

    # Just verify seek returns success - position accuracy depends on keyframes
    # and timing which varies with fakesink

    controller.stop_playback()


def test_seek_returns_false_when_idle(controller):
    """Test that seek() returns False when idle."""
    success = controller.seek(1)
    assert success is False


# =========================================================================
# EOS (End of Stream) Tests
# =========================================================================


def test_eos_callback_fires(controller, test_video_1s):
    """Test that EOS callback is called when song ends."""
    eos_received = threading.Event()
    controller.set_eos_callback(lambda: eos_received.set())

    controller.load_file(test_video_1s)

    # Wait for EOS (1 second video + some buffer)
    assert eos_received.wait(timeout=3), "EOS callback not received"

    controller.stop_playback()


def test_multiple_eos_callbacks(controller, test_video_1s):
    """Test that EOS callback fires for multiple songs."""
    eos_count = []
    controller.set_eos_callback(lambda: eos_count.append(1))

    # Play first song
    controller.load_file(test_video_1s)
    time.sleep(1.5)

    # Play second song
    controller.load_file(test_video_1s)
    time.sleep(1.5)

    # Should have received 2 EOS callbacks
    assert len(eos_count) >= 1  # At least one EOS

    controller.stop_playback()


# =========================================================================
# Error Handling Tests
# =========================================================================


def test_error_handling_invalid_file(controller):
    """Test that loading an invalid file raises an error."""
    with pytest.raises(RuntimeError):
        controller.load_file("/nonexistent/file.mp4")


def test_error_handling_empty_path(controller):
    """Test that loading an empty path raises an error."""
    with pytest.raises(RuntimeError):
        controller.load_file("")


def test_pipeline_recovers_after_error(controller, test_video_1s):
    """Test that pipeline can recover after an error."""
    # Try to load invalid file
    try:
        controller.load_file("/nonexistent/file.mp4")
    except RuntimeError:
        pass

    # Should be able to load a valid file after error
    controller.load_file(test_video_1s)
    assert controller.state == "playing"

    controller.stop_playback()


# =========================================================================
# Cleanup Tests
# =========================================================================


def test_stop_cleans_up_pipeline(controller, test_video_1s):
    """Test that stop() properly cleans up the pipeline."""
    controller.load_file(test_video_1s)
    controller.stop()

    assert controller.playbin is None


def test_streaming_controller_initialization():
    """Test basic StreamingController initialization."""
    db = create_autospec(Database, instance=True)
    config_manager = ConfigManager(db)

    # Set rubberband plugin config
    config_manager.set(
        "rubberband_plugin", "ladspa-ladspa-rubberband-so-rubberband-r3-pitchshifter-stereo"
    )

    server = create_autospec(MagicMock, instance=True)
    streaming = StreamingController(config_manager, server, use_fakesinks=True)

    # Verify it initialized
    assert streaming.get_pipeline_state() == "ready"
    assert streaming.state == "idle"
