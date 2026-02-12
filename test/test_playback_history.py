"""
Unit tests for playback history functionality.
"""

from unittest.mock import Mock

import pytest

from kbox.playback import PlaybackController
from kbox.queue import QueueManager


@pytest.fixture
def mock_queue_manager():
    """Create a mock QueueManager."""
    qm = Mock(spec=QueueManager)
    # PlaybackController needs queue_manager.database for ConfigRepository
    qm.database = Mock()
    qm.database.get_connection.return_value.cursor.return_value.fetchone.return_value = None
    # Provide a mock repository for get_cursor_position lookups
    qm.repository = Mock()
    qm.repository.get_item.return_value = None
    return qm


@pytest.fixture
def mock_streaming_controller():
    """Create a mock StreamingController."""
    mock = Mock()
    mock.get_position = Mock(return_value=0)
    mock.set_pitch_shift = Mock()
    mock.set_eos_callback = Mock()
    return mock


@pytest.fixture
def mock_config_manager():
    """Create a mock ConfigManager."""
    mock = Mock()
    mock.get = Mock(return_value=None)
    return mock


@pytest.fixture
def playback_controller(mock_queue_manager, mock_streaming_controller, mock_config_manager):
    """Create a PlaybackController instance for testing."""
    return PlaybackController(
        queue_manager=mock_queue_manager,
        streaming_controller=mock_streaming_controller,
        config_manager=mock_config_manager,
    )


def test_should_record_history_percentage_threshold(playback_controller, mock_config_manager):
    """Test history recording threshold - percentage met."""
    mock_config_manager.get.side_effect = lambda key: {
        "history_threshold_percentage": 70,
        "history_threshold_seconds": 90,
    }.get(key)

    # 75% of 200 seconds = 150 seconds (meets 70% threshold)
    assert playback_controller._should_record_history(200, 150) is True


def test_should_record_history_time_threshold(playback_controller, mock_config_manager):
    """Test history recording threshold - time met."""
    mock_config_manager.get.side_effect = lambda key: {
        "history_threshold_percentage": 70,
        "history_threshold_seconds": 90,
    }.get(key)

    # 95 seconds meets 90 second threshold (even if percentage is low)
    assert playback_controller._should_record_history(300, 95) is True


def test_should_record_history_threshold_not_met(playback_controller, mock_config_manager):
    """Test history recording threshold - not met."""
    mock_config_manager.get.side_effect = lambda key: {
        "history_threshold_percentage": 70,
        "history_threshold_seconds": 90,
    }.get(key)

    # 30 seconds of 200 second song (15%, below both thresholds)
    assert playback_controller._should_record_history(200, 30) is False


def test_should_record_history_no_duration(playback_controller, mock_config_manager):
    """Test history recording with unknown duration."""
    mock_config_manager.get.side_effect = lambda key: {
        "history_threshold_percentage": 70,
        "history_threshold_seconds": 90,
    }.get(key)

    # No duration, but 95 seconds meets time threshold
    assert playback_controller._should_record_history(None, 95) is True

    # No duration, 30 seconds doesn't meet time threshold
    assert playback_controller._should_record_history(None, 30) is False


def test_calculate_completion_percentage(playback_controller):
    """Test completion percentage calculation."""
    # 150 of 200 = 75%
    assert playback_controller._calculate_completion_percentage(150, 200) == 75.0

    # 100 of 100 = 100%
    assert playback_controller._calculate_completion_percentage(100, 100) == 100.0

    # More than duration (capped at 100%)
    assert playback_controller._calculate_completion_percentage(150, 100) == 100.0

    # No duration
    assert playback_controller._calculate_completion_percentage(150, None) == 0.0
    assert playback_controller._calculate_completion_percentage(150, 0) == 0.0
