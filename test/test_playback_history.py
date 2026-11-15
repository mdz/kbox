"""
Unit tests for the performance-history recording policy.

These exercise kbox.playback.history_policy directly: the threshold
decisions are a function of numbers, so none of them needs a
PlaybackController or a streaming pipeline.
"""

from unittest.mock import Mock

import pytest

from kbox.playback import history_policy


@pytest.fixture
def mock_config_manager():
    """Create a mock ConfigManager."""
    mock = Mock()
    mock.get = Mock(return_value=None)
    return mock


def test_history_thresholds_from_config(mock_config_manager):
    """Thresholds come from config, with defaults when unset or unparseable."""
    mock_config_manager.get.side_effect = lambda key: {
        "history_threshold_percentage": 70,
        "history_threshold_seconds": 90,
    }.get(key)
    assert history_policy.history_thresholds(mock_config_manager) == (70, 90)

    mock_config_manager.get.side_effect = lambda key: None
    assert history_policy.history_thresholds(mock_config_manager) == (
        history_policy.DEFAULT_THRESHOLD_PERCENTAGE,
        history_policy.DEFAULT_THRESHOLD_SECONDS,
    )

    mock_config_manager.get.side_effect = lambda key: "not-a-number"
    assert history_policy.history_thresholds(mock_config_manager) == (
        history_policy.DEFAULT_THRESHOLD_PERCENTAGE,
        history_policy.DEFAULT_THRESHOLD_SECONDS,
    )


def test_should_record_history_percentage_threshold():
    """Test history recording threshold - percentage met."""
    # 75% of 200 seconds = 150 seconds (meets 70% threshold)
    assert history_policy.should_record_history(200, 150, 70, 90) is True


def test_should_record_history_time_threshold():
    """Test history recording threshold - time met."""
    # 95 seconds meets 90 second threshold (even if percentage is low)
    assert history_policy.should_record_history(300, 95, 70, 90) is True


def test_should_record_history_threshold_not_met():
    """Test history recording threshold - not met."""
    # 30 seconds of 200 second song (15%, below both thresholds)
    assert history_policy.should_record_history(200, 30, 70, 90) is False


def test_should_record_history_no_duration():
    """Test history recording with unknown duration."""
    # No duration, but 95 seconds meets time threshold
    assert history_policy.should_record_history(None, 95, 70, 90) is True

    # No duration, 30 seconds doesn't meet time threshold
    assert history_policy.should_record_history(None, 30, 70, 90) is False


def test_calculate_completion_percentage():
    """Test completion percentage calculation."""
    # 150 of 200 = 75%
    assert history_policy.calculate_completion_percentage(150, 200) == 75.0

    # 100 of 100 = 100%
    assert history_policy.calculate_completion_percentage(100, 100) == 100.0

    # More than duration (capped at 100%)
    assert history_policy.calculate_completion_percentage(150, 100) == 100.0

    # No duration
    assert history_policy.calculate_completion_percentage(150, None) == 0.0
    assert history_policy.calculate_completion_percentage(150, 0) == 0.0
