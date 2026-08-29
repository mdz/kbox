"""
Loudness measurement and gain calculation for volume normalization.

Karaoke tracks come from a mix of providers and uploaders with wildly
different mastering levels, so guests hear jarring volume jumps between
songs. This module measures each track's loudness with ffmpeg's `loudnorm`
filter (EBU R128) and computes a playback gain that brings it toward a
common target loudness, without pushing an already loud/limited track into
clipping.
"""

import json
import logging
import math
import re
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from .priority import lower_priority

if TYPE_CHECKING:
    from .database import Database

logger = logging.getLogger(__name__)


# Single-pass loudnorm analysis. The I/TP/LRA values here only affect
# ffmpeg's internal target_offset calculation, not our own gain formula --
# we only read the measured input_i/input_tp fields back out.
_LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json"

_ANALYSIS_TIMEOUT_SECONDS = 120

DEFAULT_TARGET_LUFS = -16.0
_TARGET_TRUE_PEAK_DBTP = -1.5
_MAX_GAIN_DB = 12.0


@dataclass
class LoudnessInfo:
    """Measured loudness characteristics of an audio track."""

    integrated_lufs: float  # EBU R128 integrated loudness, in LUFS
    true_peak_dbtp: float  # True peak level, in dBTP


def measure_loudness(file_path: str) -> Optional[LoudnessInfo]:
    """
    Measure the integrated loudness and true peak of a media file.

    Runs a single-pass ffmpeg loudnorm analysis (produces no output file).
    Takes roughly 1/30th of the file's duration to run.

    Args:
        file_path: Path to the audio/video file to analyze

    Returns:
        LoudnessInfo, or None if ffmpeg is unavailable or analysis fails
    """
    cmd = ["ffmpeg", "-i", file_path, "-af", _LOUDNORM_FILTER, "-f", "null", "-"]
    lower_priority()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_ANALYSIS_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        logger.warning("ffmpeg not found, cannot measure loudness for %s", file_path)
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Loudness analysis timed out for %s", file_path)
        return None

    # loudnorm prints its JSON report to stderr, interleaved with progress lines
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", result.stderr)
    if not match:
        logger.warning("No loudnorm measurement found for %s", file_path)
        return None

    try:
        data = json.loads(match.group(0))
        return LoudnessInfo(
            integrated_lufs=float(data["input_i"]),
            true_peak_dbtp=float(data["input_tp"]),
        )
    except (ValueError, KeyError, TypeError) as e:
        logger.warning("Could not parse loudness measurement for %s: %s", file_path, e)
        return None


def compute_gain_db(
    loudness: LoudnessInfo,
    target_lufs: float = DEFAULT_TARGET_LUFS,
) -> float:
    """
    Compute the playback gain (in dB) that brings a track toward target_lufs.

    Boosts are capped so the track's true peak won't exceed
    _TARGET_TRUE_PEAK_DBTP after gain is applied. Some quiet tracks are
    quiet because of a limited/near-clipped master rather than mastering
    headroom, and boosting those to full loudness would clip them. Cuts
    (bringing an overly loud track down) are never capped by peak, only by
    _MAX_GAIN_DB.

    Args:
        loudness: Measured loudness of the track
        target_lufs: Desired integrated loudness, in LUFS

    Returns:
        Gain in dB to apply during playback. 0.0 if the measurement isn't
        finite (e.g. near-silent audio measures as -inf LUFS).
    """
    if not math.isfinite(loudness.integrated_lufs) or not math.isfinite(loudness.true_peak_dbtp):
        return 0.0

    gain_db = target_lufs - loudness.integrated_lufs

    if gain_db > 0:
        peak_headroom_db = _TARGET_TRUE_PEAK_DBTP - loudness.true_peak_dbtp
        gain_db = min(gain_db, peak_headroom_db)

    return max(-_MAX_GAIN_DB, min(_MAX_GAIN_DB, gain_db))


def db_to_linear(gain_db: float) -> float:
    """Convert a gain in decibels to a linear amplitude multiplier."""
    return 10 ** (gain_db / 20)


class LoudnessAnalyzer:
    """Analyzes downloaded videos for loudness and caches the result.

    Results are cached by video_id in the song_metadata_cache table (shared
    with LLM-extracted artist/song_name and trailing-silence detection,
    each written independently) so a given video is only ever measured once.
    """

    def __init__(self, database: "Database"):
        self.database = database
        self.logger = logging.getLogger(__name__)

    def analyze(self, video_id: str, filepath: str) -> Optional[LoudnessInfo]:
        """
        Measure a downloaded video's loudness, caching the result.

        Safe to call repeatedly for the same video -- returns the cached
        result without re-running ffmpeg once analysis has completed.

        Args:
            video_id: Opaque video ID (e.g. "youtube:abc123")
            filepath: Path to the local downloaded file

        Returns:
            LoudnessInfo, or None if measurement failed.
        """
        if self._is_analyzed(video_id):
            return self.get_cached_loudness(video_id)

        start = time.monotonic()
        try:
            loudness = measure_loudness(filepath)
        except Exception as e:
            self.logger.warning("Loudness analysis failed for %s: %s", video_id, e)
            loudness = None
        elapsed = time.monotonic() - start

        self._cache_result(video_id, loudness)

        if loudness is not None:
            self.logger.info(
                "Measured loudness for %s in %.1fs: %.1f LUFS, %.1f dBTP",
                video_id,
                elapsed,
                loudness.integrated_lufs,
                loudness.true_peak_dbtp,
            )
        else:
            self.logger.info(
                "No loudness measurement available for %s (analyzed in %.1fs)", video_id, elapsed
            )

        return loudness

    def get_cached_loudness(self, video_id: str) -> Optional[LoudnessInfo]:
        """Read-only lookup of a previously-cached loudness measurement (no analysis)."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT integrated_lufs, true_peak_dbtp FROM song_metadata_cache
                WHERE video_id = ? AND loudness_analyzed_at IS NOT NULL
                """,
                (video_id,),
            )
            row = cursor.fetchone()
            if row and row["integrated_lufs"] is not None and row["true_peak_dbtp"] is not None:
                return LoudnessInfo(
                    integrated_lufs=row["integrated_lufs"],
                    true_peak_dbtp=row["true_peak_dbtp"],
                )
            return None
        finally:
            conn.close()

    def _is_analyzed(self, video_id: str) -> bool:
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM song_metadata_cache WHERE video_id = ? AND loudness_analyzed_at IS NOT NULL",
                (video_id,),
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()

    def _cache_result(self, video_id: str, loudness: Optional[LoudnessInfo]) -> None:
        """Cache analysis result, upserting only this class's own columns."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO song_metadata_cache
                    (video_id, integrated_lufs, true_peak_dbtp, loudness_analyzed_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(video_id) DO UPDATE SET
                    integrated_lufs = excluded.integrated_lufs,
                    true_peak_dbtp = excluded.true_peak_dbtp,
                    loudness_analyzed_at = excluded.loudness_analyzed_at
                """,
                (
                    video_id,
                    loudness.integrated_lufs if loudness else None,
                    loudness.true_peak_dbtp if loudness else None,
                ),
            )
            conn.commit()
            self.logger.debug("Cached loudness analysis for %s", video_id)
        finally:
            conn.close()
