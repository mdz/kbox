"""
Per-song playback tuning looked up from the analysis caches.

Both lookups are advisory: if the feature is off, no analyzer is configured,
or the video has not been measured yet, they return the "do nothing" answer
rather than failing. A song that has not been analyzed plays at its native
level and to its literal end, which is exactly how playback worked before
these features existed.

Neither function keeps state, so both take their analyzer and config as
arguments.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def lookup_trim_point(silence_analyzer, config_manager, video_id: str) -> Optional[int]:
    """
    Look up the cached trailing-silence trim point for a video.

    Returns None (no early skip) if the feature is disabled, no analyzer
    is configured, or analysis hasn't found any exploitable silence.
    Never raises -- a lookup failure just means normal end-of-file
    playback, same as before this feature existed.
    """
    if not silence_analyzer:
        return None
    if not config_manager.get_bool("skip_trailing_silence_enabled", True):
        return None
    try:
        return silence_analyzer.get_cached_trim_point(video_id)
    except Exception as e:
        logger.warning("Error looking up trim point for %s: %s", video_id, e)
        return None


def lookup_volume_gain_db(loudness_analyzer, config_manager, video_id: str) -> float:
    """
    Look up the loudness-normalization gain for a video, in dB.

    Returns 0.0 (no adjustment) if normalization is disabled, no
    analyzer is configured, or the video hasn't been measured yet - a
    freshly-added song plays at its native level until analysis catches
    up, rather than blocking playback. Never raises.
    """
    if not loudness_analyzer:
        return 0.0
    if not config_manager.get_bool("loudness_normalization_enabled", True):
        return 0.0
    try:
        loudness = loudness_analyzer.get_cached_loudness(video_id)
        if loudness is None:
            return 0.0

        from ..loudness import DEFAULT_TARGET_LUFS, compute_gain_db

        target_lufs = config_manager.get_float("loudness_target_lufs", DEFAULT_TARGET_LUFS)
        return compute_gain_db(loudness, target_lufs=target_lufs)
    except Exception as e:
        logger.warning("Error looking up volume gain for %s: %s", video_id, e)
        return 0.0
