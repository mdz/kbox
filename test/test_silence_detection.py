"""
Unit tests for trailing-silence detection.

Most tests mock subprocess.run to exercise the parsing/decision logic
deterministically. A couple of end-to-end tests generate real audio with
ffmpeg to confirm the actual command syntax works. ffmpeg/ffprobe are a
hard runtime dependency of this feature (and already assumed present
elsewhere in the test suite, e.g. test_streaming.py's fixtures), so these
are not skipped when they're missing -- that would silently drop coverage
of the real command syntax instead of failing loudly.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kbox.database import Database
from kbox.silence_detection import TrailingSilenceAnalyzer, detect_trailing_silence


def _fake_run(returncode=0, stdout="", stderr=""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# =============================================================================
# detect_trailing_silence -- mocked subprocess
# =============================================================================


class TestDetectTrailingSilenceMocked:
    def test_no_duration_returns_none(self):
        """If ffprobe can't report a duration, fail open."""
        with patch("subprocess.run", return_value=_fake_run(returncode=1)):
            assert detect_trailing_silence("/fake/path.mp4") is None

    def test_trailing_silence_to_eof_returns_trim_point(self):
        """Silence that starts partway through the window and runs to EOF
        (no silence_end before the window ends) yields a trim point."""

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return _fake_run(stdout="200.0\n")
            # window is last 25s: 175.0 -> 200.0. Silence starts at
            # relative 10.0s (absolute 185.0), no silence_end reported.
            return _fake_run(stderr="[silencedetect] silence_start: 10.0\n")

        with patch("subprocess.run", side_effect=fake_run):
            trim = detect_trailing_silence("/fake/path.mp4")

        # 175.0 (window start) + 10.0 (relative start) + 2s debounce = 187
        assert trim == 187

    def test_silence_that_resumes_before_eof_returns_none(self):
        """A mid-song pause (silence_end well before window end) is not trailing."""

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return _fake_run(stdout="200.0\n")
            return _fake_run(
                stderr=(
                    "[silencedetect] silence_start: 5.0\n"
                    "[silencedetect] silence_end: 8.0 | silence_duration: 3.0\n"
                )
            )

        with patch("subprocess.run", side_effect=fake_run):
            assert detect_trailing_silence("/fake/path.mp4") is None

    def test_silence_end_near_eof_still_counts_as_trailing(self):
        """silence_end within the EOF tolerance still counts (encoder padding)."""

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return _fake_run(stdout="200.0\n")
            # window duration is 25.0s; silence_end at 24.8 is within 1s of EOF
            return _fake_run(
                stderr=(
                    "[silencedetect] silence_start: 20.0\n"
                    "[silencedetect] silence_end: 24.8 | silence_duration: 4.8\n"
                )
            )

        with patch("subprocess.run", side_effect=fake_run):
            trim = detect_trailing_silence("/fake/path.mp4")

        assert trim == 197  # 175 + 20 + 2

    def test_no_silence_detected_returns_none(self):
        """No silence_start lines at all -- e.g. audio/vocals run to the last frame."""

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return _fake_run(stdout="150.0\n")
            return _fake_run(stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            assert detect_trailing_silence("/fake/path.mp4") is None

    def test_debounce_eating_entire_window_returns_none(self):
        """If the debounce margin would push the trim point past the actual
        end of the file, there's nothing safe to trim."""

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return _fake_run(stdout="200.0\n")
            # window is 175 -> 200 (25s). Silence starts at relative 24.5s
            # (absolute 199.5), only 0.5s before EOF -- debounce (2s) blows past it.
            return _fake_run(stderr="[silencedetect] silence_start: 24.5\n")

        with patch("subprocess.run", side_effect=fake_run):
            assert detect_trailing_silence("/fake/path.mp4") is None

    def test_ffmpeg_exception_fails_open(self):
        """Any subprocess error must return None, never raise."""

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return _fake_run(stdout="200.0\n")
            raise OSError("ffmpeg not found")

        with patch("subprocess.run", side_effect=fake_run):
            assert detect_trailing_silence("/fake/path.mp4") is None

    def test_short_file_analyzes_whole_window(self):
        """Files shorter than the analysis window still work (window clamped to 0)."""

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return _fake_run(stdout="10.0\n")
            # whole 10s file is silent from t=3 onward
            return _fake_run(stderr="[silencedetect] silence_start: 3.0\n")

        with patch("subprocess.run", side_effect=fake_run):
            trim = detect_trailing_silence("/fake/path.mp4")

        assert trim == 5  # 0 + 3 + 2 debounce


# =============================================================================
# detect_trailing_silence -- real ffmpeg
# =============================================================================


class TestDetectTrailingSilenceReal:
    @pytest.fixture(scope="class")
    def fixtures_dir(self):
        d = Path(tempfile.mkdtemp(prefix="kbox_silence_test_"))
        yield d
        shutil.rmtree(d, ignore_errors=True)

    def _make_audio(self, path: Path, filter_complex: str, duration: float):
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={duration}",
                "-af",
                filter_complex,
                str(path),
            ],
            check=True,
            capture_output=True,
        )

    def test_real_trailing_silence_detected(self, fixtures_dir):
        """A tone that fades to silence for the last 5s of a 15s clip."""
        path = fixtures_dir / "trailing_silence.wav"
        # Full volume for first 10s, then silence for the last 5s.
        self._make_audio(
            path,
            "volume=enable='lt(t,10)':volume=1,volume=enable='gte(t,10)':volume=0",
            duration=15,
        )
        trim = detect_trailing_silence(str(path))
        assert trim is not None
        assert 10 <= trim <= 13  # onset (~10s) + debounce (2s), some tolerance

    def test_real_no_trailing_silence(self, fixtures_dir):
        """A tone playing at full volume through to the end."""
        path = fixtures_dir / "no_silence.wav"
        self._make_audio(path, "volume=1", duration=10)
        assert detect_trailing_silence(str(path)) is None


# =============================================================================
# TrailingSilenceAnalyzer
# =============================================================================


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path=path)
    yield db
    db.close()
    os.unlink(path)


class TestTrailingSilenceAnalyzer:
    def test_analyze_caches_result(self, temp_db):
        analyzer = TrailingSilenceAnalyzer(temp_db)

        with patch(
            "kbox.silence_detection.detect_trailing_silence", return_value=120
        ) as mock_detect:
            trim = analyzer.analyze("youtube:abc", "/fake/path.mp4")
            assert trim == 120
            mock_detect.assert_called_once_with("/fake/path.mp4")

        # Second call should hit the cache, not re-run detection
        with patch("kbox.silence_detection.detect_trailing_silence") as mock_detect2:
            trim2 = analyzer.analyze("youtube:abc", "/fake/path.mp4")
            assert trim2 == 120
            mock_detect2.assert_not_called()

    def test_analyze_caches_none_result(self, temp_db):
        """A video with no exploitable silence is still cached (as 'analyzed, nothing found')."""
        analyzer = TrailingSilenceAnalyzer(temp_db)

        with patch("kbox.silence_detection.detect_trailing_silence", return_value=None):
            trim = analyzer.analyze("youtube:xyz", "/fake/path.mp4")
            assert trim is None

        assert analyzer.get_cached_trim_point("youtube:xyz") is None

        # Confirm it's genuinely cached (not just "never analyzed") by checking
        # a second analyze() call doesn't re-run ffmpeg.
        with patch("kbox.silence_detection.detect_trailing_silence") as mock_detect:
            analyzer.analyze("youtube:xyz", "/fake/path.mp4")
            mock_detect.assert_not_called()

    def test_get_cached_trim_point_before_analysis(self, temp_db):
        analyzer = TrailingSilenceAnalyzer(temp_db)
        assert analyzer.get_cached_trim_point("youtube:never-analyzed") is None

    def test_analysis_exception_caches_none(self, temp_db):
        """If detection itself raises, that's cached as 'no silence' too --
        we don't want to retry a broken file on every play."""
        analyzer = TrailingSilenceAnalyzer(temp_db)

        with patch(
            "kbox.silence_detection.detect_trailing_silence",
            side_effect=Exception("boom"),
        ):
            trim = analyzer.analyze("youtube:broken", "/fake/path.mp4")
            assert trim is None

        assert analyzer.get_cached_trim_point("youtube:broken") is None

    def test_analysis_does_not_clobber_metadata_extraction(self, temp_db):
        """Silence analysis writing its columns must not wipe out artist/
        song_name written independently by metadata extraction (or vice
        versa) -- the two pipelines share a row keyed by video_id."""
        from kbox.song_metadata import SongMetadataExtractor

        extractor = SongMetadataExtractor(temp_db, llm_client=None)
        analyzer = TrailingSilenceAnalyzer(temp_db)

        # Metadata extraction writes first (simulate a cache seeded some
        # other way, since llm_client=None means extract() itself no-ops).
        extractor._cache_result("youtube:both", "Queen", "Bohemian Rhapsody")

        with patch("kbox.silence_detection.detect_trailing_silence", return_value=90):
            analyzer.analyze("youtube:both", "/fake/path.mp4")

        # Both should still be readable afterward.
        assert extractor._get_cached("youtube:both") == ("Queen", "Bohemian Rhapsody")
        assert analyzer.get_cached_trim_point("youtube:both") == 90

        # And in the other order.
        with patch("kbox.silence_detection.detect_trailing_silence", return_value=60):
            analyzer.analyze("youtube:both2", "/fake/path.mp4")
        extractor._cache_result("youtube:both2", "ABBA", "Dancing Queen")

        assert analyzer.get_cached_trim_point("youtube:both2") == 60
        assert extractor._get_cached("youtube:both2") == ("ABBA", "Dancing Queen")
