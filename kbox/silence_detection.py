"""
Trailing-silence detection for karaoke tracks.

Detects genuine digital silence at the very end of a downloaded video file
(dead air after the song ends) so playback can advance to the next song
without waiting through it. Deliberately conservative: only reports a trim
point when the silence is sustained and runs all the way to end-of-file
(never a mid-song pause, which resumes with more audio), and adds a
debounce margin so a lingering lyric caption or the natural decay of the
last note isn't cut short.

See: https://github.com/mdz/kbox/issues/97
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from typing import TYPE_CHECKING, Optional

from .priority import lower_priority

if TYPE_CHECKING:
    from .database import Database

logger = logging.getLogger(__name__)


# How far from the end of the file to look for trailing silence.
_ANALYSIS_WINDOW_SECONDS = 25
# Audio below this level is considered silence.
_SILENCE_THRESHOLD_DB = -35
# Minimum sustained silence duration for ffmpeg to report it.
_MIN_SILENCE_DURATION = 0.3
# Safety margin added after the detected silence onset, so playback doesn't
# cut off a lingering lyric caption or the natural decay of the last note.
_DEBOUNCE_SECONDS = 2
# How close to end-of-file the last detected silence must reach to count as
# "trailing" rather than a mid-song pause that resumes with more audio.
_EOF_TOLERANCE_SECONDS = 1.0


def _get_duration_seconds(filepath: str) -> Optional[float]:
    """Get media duration via ffprobe, or None on any failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        logger.debug("ffprobe failed for %s: %s", filepath, e)
        return None


def detect_trailing_silence(filepath: str) -> Optional[int]:
    """
    Detect a trailing run of true digital silence at the end of a video file.

    Only returns a trim point when the silence is sustained and runs all the
    way to end-of-file. Fails open: any error (missing ffmpeg, corrupt file,
    unreadable path, etc.) returns None, never raises.

    Args:
        filepath: Path to the local video/audio file

    Returns:
        Playback position (whole seconds) at which it's safe to advance to
        the next song, or None if no exploitable trailing silence was found.
    """
    duration = _get_duration_seconds(filepath)
    if duration is None or duration <= 0:
        return None

    window_start = max(0.0, duration - _ANALYSIS_WINDOW_SECONDS)

    lower_priority()
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-nostats",
                "-loglevel",
                "info",
                "-ss",
                str(window_start),
                "-i",
                filepath,
                "-af",
                f"silencedetect=noise={_SILENCE_THRESHOLD_DB}dB:d={_MIN_SILENCE_DURATION}",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("ffmpeg silencedetect failed for %s: %s", filepath, e)
        return None

    output = result.stderr or ""

    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", output)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", output)]

    if not starts:
        return None

    last_start = starts[-1]
    window_duration = duration - window_start

    # Confirm the silence actually runs to (near) end-of-file, not just a
    # mid-song pause that resumes with more audio.
    if ends:
        last_end = ends[-1]
        if (window_duration - last_end) >= _EOF_TOLERANCE_SECONDS:
            return None

    trim_point = window_start + last_start + _DEBOUNCE_SECONDS
    if trim_point >= duration:
        return None  # debounce margin would eat the entire silence window

    return int(trim_point)


class TrailingSilenceAnalyzer:
    """Analyzes downloaded videos for trailing silence and caches the result.

    Results are cached by video_id in the song_metadata_cache table (shared
    with LLM-extracted artist/song_name, written independently) so a given
    video is only ever analyzed once.
    """

    def __init__(self, database: "Database"):
        self.database = database
        self.logger = logging.getLogger(__name__)

    def analyze(self, video_id: str, filepath: str) -> Optional[int]:
        """
        Analyze a downloaded video for trailing silence, caching the result.

        Safe to call repeatedly for the same video -- returns the cached
        result without re-running ffmpeg once analysis has completed.

        Args:
            video_id: Opaque video ID (e.g. "youtube:abc123")
            filepath: Path to the local downloaded file

        Returns:
            Trim point in seconds, or None if no exploitable trailing
            silence was found (or analysis failed).
        """
        if self._is_analyzed(video_id):
            return self.get_cached_trim_point(video_id)

        start = time.monotonic()
        try:
            trim_point = detect_trailing_silence(filepath)
        except Exception as e:
            self.logger.warning("Silence analysis failed for %s: %s", video_id, e)
            trim_point = None
        elapsed = time.monotonic() - start

        self._cache_result(video_id, trim_point)

        if trim_point is not None:
            self.logger.info(
                "Trailing silence detected for %s in %.1fs: trim at %ss",
                video_id,
                elapsed,
                trim_point,
            )
        else:
            self.logger.info(
                "No exploitable trailing silence for %s (analyzed in %.1fs)", video_id, elapsed
            )

        return trim_point

    def get_cached_trim_point(self, video_id: str) -> Optional[int]:
        """Read-only lookup of a previously-cached trim point (no analysis)."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT trailing_silence_start_seconds FROM song_metadata_cache
                WHERE video_id = ? AND silence_analyzed_at IS NOT NULL
                """,
                (video_id,),
            )
            row = cursor.fetchone()
            return row["trailing_silence_start_seconds"] if row else None
        finally:
            conn.close()

    def _is_analyzed(self, video_id: str) -> bool:
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM song_metadata_cache WHERE video_id = ? AND silence_analyzed_at IS NOT NULL",
                (video_id,),
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()

    def _cache_result(self, video_id: str, trim_point: Optional[int]) -> None:
        """Cache analysis result, upserting only this class's own columns."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO song_metadata_cache
                    (video_id, trailing_silence_start_seconds, silence_analyzed_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(video_id) DO UPDATE SET
                    trailing_silence_start_seconds = excluded.trailing_silence_start_seconds,
                    silence_analyzed_at = excluded.silence_analyzed_at
                """,
                (video_id, trim_point),
            )
            conn.commit()
            self.logger.debug("Cached silence analysis for %s", video_id)
        finally:
            conn.close()
