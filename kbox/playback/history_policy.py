"""
Whether and how a performance gets written to history.

These are decisions about numbers, not about playback state: given how long a
song ran and how long it was supposed to run, should it count, and how
complete was it. Nothing here touches the controller, so each function is
callable with plain ints.

The config thresholds are read once by history_thresholds() and passed in,
rather than fetched mid-computation, so should_record_history() is a pure
function of its arguments.
"""

import logging
from typing import Optional, Tuple

from ..models import QueueItem

logger = logging.getLogger(__name__)

# Defaults when the config table has no value (or an unparseable one).
DEFAULT_THRESHOLD_PERCENTAGE = 70
DEFAULT_THRESHOLD_SECONDS = 90


def history_thresholds(config_manager) -> Tuple[int, int]:
    """
    Read the history-recording thresholds from config.

    Args:
        config_manager: ConfigManager instance

    Returns:
        (threshold_percentage, threshold_seconds), with defaults substituted
        for missing or unparseable values.
    """
    threshold_pct = config_manager.get("history_threshold_percentage")
    threshold_seconds = config_manager.get("history_threshold_seconds")

    # Use defaults if not configured
    if threshold_pct is None:
        threshold_pct = DEFAULT_THRESHOLD_PERCENTAGE
    else:
        try:
            threshold_pct = int(threshold_pct)
        except (ValueError, TypeError):
            threshold_pct = DEFAULT_THRESHOLD_PERCENTAGE

    if threshold_seconds is None:
        threshold_seconds = DEFAULT_THRESHOLD_SECONDS
    else:
        try:
            threshold_seconds = int(threshold_seconds)
        except (ValueError, TypeError):
            threshold_seconds = DEFAULT_THRESHOLD_SECONDS

    return threshold_pct, threshold_seconds


def should_record_history(
    duration_seconds: Optional[int],
    played_seconds: int,
    threshold_percentage: int,
    threshold_seconds: int,
) -> bool:
    """
    Check if a performance should be recorded in history.

    Records if played duration meets threshold:
    - 70% of total duration (default), OR
    - 90+ seconds (default)

    Args:
        duration_seconds: Total song duration (None if unknown)
        played_seconds: How long the song was played
        threshold_percentage: Percentage-of-duration threshold
        threshold_seconds: Absolute seconds threshold

    Returns:
        True if performance should be recorded
    """
    # Check time threshold
    if played_seconds >= threshold_seconds:
        logger.debug(
            "History threshold met: %s seconds >= %s seconds", played_seconds, threshold_seconds
        )
        return True

    # Check percentage threshold if duration known
    if duration_seconds and duration_seconds > 0:
        percentage = (played_seconds / duration_seconds) * 100
        if percentage >= threshold_percentage:
            logger.debug("History threshold met: %.1f%% >= %s%%", percentage, threshold_percentage)
            return True

    logger.debug(
        "History threshold NOT met: %s seconds (threshold: %s%% or %s seconds)",
        played_seconds,
        threshold_percentage,
        threshold_seconds,
    )
    return False


def calculate_completion_percentage(played_seconds: int, duration_seconds: Optional[int]) -> float:
    """
    Calculate completion percentage.

    Args:
        played_seconds: How long the song was played
        duration_seconds: Total song duration

    Returns:
        Completion percentage (0.0-100.0)
    """
    if not duration_seconds or duration_seconds <= 0:
        return 0.0

    return min(100.0, (played_seconds / duration_seconds) * 100.0)


def record_performance_history(
    history_manager,
    queue_item: QueueItem,
    played_duration_seconds: int,
    playback_end_position_seconds: int,
    completion_percentage: float,
    theme: Optional[str],
):
    """
    Record a performance in history.

    Args:
        history_manager: HistoryManager instance
        queue_item: Queue item with song details
        played_duration_seconds: How long the song was played
        playback_end_position_seconds: Where playback ended
        completion_percentage: Percentage of song completed
        theme: Party theme to attribute the performance to, or None
    """
    history_manager.record_performance(
        user_id=queue_item.user_id,
        user_name=queue_item.user_name,
        video_id=queue_item.video_id,
        metadata=queue_item.metadata,
        settings=queue_item.settings,
        played_duration_seconds=played_duration_seconds,
        playback_end_position_seconds=playback_end_position_seconds,
        completion_percentage=completion_percentage,
        theme=theme,
    )
