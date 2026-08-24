"""
Unit tests for loudness measurement and gain calculation.
"""

import logging
import math
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from kbox.database import Database
from kbox.loudness import (
    LoudnessAnalyzer,
    LoudnessInfo,
    compute_gain_db,
    db_to_linear,
    measure_loudness,
)

logger = logging.getLogger(__name__)


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path=path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture(scope="session")
def quiet_tone():
    """A quiet 2-second sine tone with plenty of headroom (~-30 dBFS)."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    audio_path = fixtures_dir / "test_quiet_tone.wav"

    if not audio_path.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=f=440:d=2",
                "-af",
                "volume=-30dB",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )
    return str(audio_path)


@pytest.fixture(scope="session")
def loud_tone():
    """A near-full-scale 2-second sine tone with little headroom."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    audio_path = fixtures_dir / "test_loud_tone.wav"

    if not audio_path.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=f=440:d=2",
                "-af",
                "volume=-1dB",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )
    return str(audio_path)


# =========================================================================
# measure_loudness()
# =========================================================================


def test_measure_loudness_quiet_tone(quiet_tone):
    """A quiet tone should measure well below a typical -16 LUFS target."""
    result = measure_loudness(quiet_tone)

    assert result is not None
    assert math.isfinite(result.integrated_lufs)
    assert math.isfinite(result.true_peak_dbtp)
    assert result.integrated_lufs < -25


def test_measure_loudness_loud_tone_louder_than_quiet(quiet_tone, loud_tone):
    """A tone with more gain should measure as louder."""
    quiet = measure_loudness(quiet_tone)
    loud = measure_loudness(loud_tone)

    assert quiet is not None
    assert loud is not None
    assert loud.integrated_lufs > quiet.integrated_lufs


def test_measure_loudness_missing_file():
    """A nonexistent file should fail gracefully, not raise."""
    result = measure_loudness("/nonexistent/path/to/file.mp4")
    assert result is None


def test_measure_loudness_ffmpeg_not_found(monkeypatch):
    """If ffmpeg isn't on PATH, measurement should fail gracefully."""

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = measure_loudness("/some/file.mp4")
    assert result is None


def test_measure_loudness_timeout(monkeypatch):
    """A hung ffmpeg process should fail gracefully, not hang the caller."""

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = measure_loudness("/some/file.mp4")
    assert result is None


def test_measure_loudness_unparseable_output(monkeypatch):
    """Unexpected ffmpeg output shouldn't raise, just return None."""

    class FakeResult:
        stderr = "some unrelated ffmpeg output with no JSON in it"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    result = measure_loudness("/some/file.mp4")
    assert result is None


# =========================================================================
# compute_gain_db()
# =========================================================================


def test_compute_gain_db_cuts_loud_track():
    """A track louder than target should get a negative (cutting) gain."""
    loudness = LoudnessInfo(integrated_lufs=-9.88, true_peak_dbtp=6.31)
    gain = compute_gain_db(loudness, target_lufs=-16.0)
    assert gain == pytest.approx(-6.12, abs=0.01)


def test_compute_gain_db_boosts_quiet_track_with_headroom():
    """A quiet track with peak headroom should get a positive (boosting) gain."""
    loudness = LoudnessInfo(integrated_lufs=-20.0, true_peak_dbtp=-10.0)
    gain = compute_gain_db(loudness, target_lufs=-16.0)
    assert gain == pytest.approx(4.0, abs=0.01)


def test_compute_gain_db_boost_capped_by_peak_headroom():
    """
    A quiet track whose peak is already close to the ceiling shouldn't be
    boosted past the point where it would clip.
    """
    # Loudness gain would be +4dB, but true peak only has 0.5dB of headroom
    # before hitting the -1.5 dBTP ceiling.
    loudness = LoudnessInfo(integrated_lufs=-20.0, true_peak_dbtp=-2.0)
    gain = compute_gain_db(loudness, target_lufs=-16.0)
    assert gain == pytest.approx(0.5, abs=0.01)


def test_compute_gain_db_boost_becomes_cut_when_already_peaking():
    """
    A track that's quiet on average but already peaking near/above 0 dBTP
    (e.g. a clipped master) should be turned down, not boosted.
    """
    loudness = LoudnessInfo(integrated_lufs=-18.53, true_peak_dbtp=0.06)
    gain = compute_gain_db(loudness, target_lufs=-16.0)
    assert gain < 0


def test_compute_gain_db_clamped_to_max():
    """Extreme measurements shouldn't produce an unbounded gain adjustment."""
    very_quiet = LoudnessInfo(integrated_lufs=-40.0, true_peak_dbtp=-40.0)
    gain = compute_gain_db(very_quiet, target_lufs=-16.0)
    assert gain == pytest.approx(12.0)

    very_loud = LoudnessInfo(integrated_lufs=0.0, true_peak_dbtp=0.0)
    gain = compute_gain_db(very_loud, target_lufs=-16.0)
    assert gain == pytest.approx(-12.0)


def test_compute_gain_db_non_finite_measurement_returns_zero():
    """Silent/unmeasurable audio (-inf LUFS) shouldn't produce a gain."""
    silent = LoudnessInfo(integrated_lufs=float("-inf"), true_peak_dbtp=float("-inf"))
    assert compute_gain_db(silent) == 0.0

    nan_measurement = LoudnessInfo(integrated_lufs=float("nan"), true_peak_dbtp=-10.0)
    assert compute_gain_db(nan_measurement) == 0.0


# =========================================================================
# db_to_linear()
# =========================================================================


def test_db_to_linear_zero_is_unity():
    assert db_to_linear(0.0) == pytest.approx(1.0)


def test_db_to_linear_positive_boosts():
    assert db_to_linear(6.0) == pytest.approx(1.995, abs=0.01)


def test_db_to_linear_negative_cuts():
    assert db_to_linear(-6.0) == pytest.approx(0.501, abs=0.01)


# =========================================================================
# LoudnessAnalyzer
# =========================================================================


class TestLoudnessAnalyzer:
    def test_analyze_caches_result(self, temp_db):
        analyzer = LoudnessAnalyzer(temp_db)
        measured = LoudnessInfo(integrated_lufs=-14.0, true_peak_dbtp=-1.0)

        with patch("kbox.loudness.measure_loudness", return_value=measured) as mock_measure:
            result = analyzer.analyze("youtube:abc", "/fake/path.mp4")
            assert result == measured
            mock_measure.assert_called_once_with("/fake/path.mp4")

        # Second call should hit the cache, not re-run ffmpeg
        with patch("kbox.loudness.measure_loudness") as mock_measure2:
            result2 = analyzer.analyze("youtube:abc", "/fake/path.mp4")
            assert result2 == measured
            mock_measure2.assert_not_called()

    def test_analyze_caches_failed_measurement(self, temp_db):
        """A video whose loudness couldn't be measured is still cached (as
        'analyzed, nothing found') so it isn't re-run on every play."""
        analyzer = LoudnessAnalyzer(temp_db)

        with patch("kbox.loudness.measure_loudness", return_value=None):
            result = analyzer.analyze("youtube:xyz", "/fake/path.mp4")
            assert result is None

        assert analyzer.get_cached_loudness("youtube:xyz") is None

        with patch("kbox.loudness.measure_loudness") as mock_measure:
            analyzer.analyze("youtube:xyz", "/fake/path.mp4")
            mock_measure.assert_not_called()

    def test_get_cached_loudness_before_analysis(self, temp_db):
        analyzer = LoudnessAnalyzer(temp_db)
        assert analyzer.get_cached_loudness("youtube:never-analyzed") is None

    def test_analysis_exception_caches_none(self, temp_db):
        """If measurement itself raises, that's cached as 'no measurement'
        too -- we don't want to retry a broken file on every play."""
        analyzer = LoudnessAnalyzer(temp_db)

        with patch("kbox.loudness.measure_loudness", side_effect=Exception("boom")):
            result = analyzer.analyze("youtube:broken", "/fake/path.mp4")
            assert result is None

        assert analyzer.get_cached_loudness("youtube:broken") is None

    def test_analysis_does_not_clobber_metadata_extraction(self, temp_db):
        """Loudness analysis writing its columns must not wipe out artist/
        song_name written independently by metadata extraction (or vice
        versa) -- the two pipelines share a row keyed by video_id."""
        from kbox.song_metadata import SongMetadataExtractor

        extractor = SongMetadataExtractor(temp_db, llm_client=None)
        analyzer = LoudnessAnalyzer(temp_db)

        # Metadata extraction writes first (simulate a cache seeded some
        # other way, since llm_client=None means extract() itself no-ops).
        extractor._cache_result("youtube:both", "Queen", "Bohemian Rhapsody")

        measured = LoudnessInfo(integrated_lufs=-14.0, true_peak_dbtp=-1.0)
        with patch("kbox.loudness.measure_loudness", return_value=measured):
            analyzer.analyze("youtube:both", "/fake/path.mp4")

        # Both should still be readable afterward.
        assert extractor._get_cached("youtube:both") == ("Queen", "Bohemian Rhapsody")
        assert analyzer.get_cached_loudness("youtube:both") == measured

        # And in the other order.
        measured2 = LoudnessInfo(integrated_lufs=-12.0, true_peak_dbtp=-0.5)
        with patch("kbox.loudness.measure_loudness", return_value=measured2):
            analyzer.analyze("youtube:both2", "/fake/path.mp4")
        extractor._cache_result("youtube:both2", "ABBA", "Dancing Queen")

        assert analyzer.get_cached_loudness("youtube:both2") == measured2
        assert extractor._get_cached("youtube:both2") == ("ABBA", "Dancing Queen")
