"""
Unit tests for VideoManager.

Tests the VideoManager facade that manages multiple video sources.
"""

import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import Mock, patch

import pytest

from kbox.cache import CacheManager
from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.video_source import VideoManager, VideoSource


class FakeVideoSource(VideoSource):
    """Fake video source for testing."""

    def __init__(self, source_id: str = "fake", configured: bool = True):
        self._source_id = source_id
        self._configured = configured
        self._search_results: List[Dict[str, Any]] = []
        self._video_info: Optional[Dict[str, Any]] = None
        self._cached_path: Optional[Path] = None
        self._is_cached = False
        self.download_calls: List[tuple] = []

    @property
    def source_id(self) -> str:
        return self._source_id

    def is_configured(self) -> bool:
        return self._configured

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        return self._search_results[:max_results]

    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        if self._video_info and self._video_info.get("id") == video_id:
            return self._video_info
        return None

    def download(
        self,
        video_id: str,
        queue_item_id: int,
        status_callback: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
    ) -> Optional[str]:
        self.download_calls.append((video_id, queue_item_id, status_callback))
        if self._cached_path:
            if status_callback:
                status_callback("ready", str(self._cached_path), None)
            return str(self._cached_path)
        return None

    def get_cached_path(self, video_id: str, touch: bool = True) -> Optional[Path]:
        return self._cached_path

    def is_cached(self, video_id: str) -> bool:
        return self._is_cached


@pytest.fixture
def temp_db():
    """Create a temporary database."""
    import os

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path=path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory."""
    import shutil

    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def config_manager(temp_db, temp_cache_dir):
    """Create a ConfigManager with test configuration."""
    config = ConfigManager(temp_db)
    config.set("youtube_api_key", "test_key")
    config.set("cache_directory", temp_cache_dir)
    return config


@pytest.fixture
def cache_manager(config_manager):
    """Create a CacheManager."""
    return CacheManager(config_manager)


# =============================================================================
# VideoManager Initialization Tests
# =============================================================================


class TestVideoManagerInit:
    """Tests for VideoManager initialization."""

    def test_init_registers_youtube_source(self, config_manager, cache_manager):
        """VideoManager should auto-register YouTubeSource on init."""
        video_manager = VideoManager(config_manager, cache_manager)

        # YouTube source should be registered and configured
        assert video_manager.is_source_configured("youtube") is True

    def test_init_stores_managers(self, config_manager, cache_manager):
        """VideoManager should store config and cache managers."""
        video_manager = VideoManager(config_manager, cache_manager)

        assert video_manager.config_manager is config_manager
        assert video_manager.cache_manager is cache_manager


# =============================================================================
# Source Configuration Tests
# =============================================================================


class TestSourceConfiguration:
    """Tests for source configuration checking."""

    def test_is_source_configured_true(self, config_manager, cache_manager):
        """is_source_configured should return True for configured source."""
        video_manager = VideoManager(config_manager, cache_manager)

        fake_source = FakeVideoSource("test", configured=True)
        video_manager._register_source(fake_source)

        assert video_manager.is_source_configured("test") is True

    def test_is_source_configured_false(self, config_manager, cache_manager):
        """is_source_configured should return False for unconfigured source."""
        video_manager = VideoManager(config_manager, cache_manager)

        fake_source = FakeVideoSource("test", configured=False)
        video_manager._register_source(fake_source)

        assert video_manager.is_source_configured("test") is False

    def test_is_source_configured_unknown(self, config_manager, cache_manager):
        """is_source_configured should return False for unknown source."""
        video_manager = VideoManager(config_manager, cache_manager)

        assert video_manager.is_source_configured("nonexistent") is False


# =============================================================================
# Search Tests
# =============================================================================


class TestSearch:
    """Tests for search functionality."""

    def test_search_delegates_to_source(self, config_manager, cache_manager):
        """search should delegate to registered sources."""
        video_manager = VideoManager(config_manager, cache_manager)

        fake_source = FakeVideoSource("test")
        fake_source._search_results = [
            {"id": "vid1", "title": "Test Video 1"},
            {"id": "vid2", "title": "Test Video 2"},
        ]
        video_manager._register_source(fake_source)

        results = video_manager.search("test query")

        # Results should be tagged with source
        test_results = [r for r in results if r.get("source") == "test"]
        assert len(test_results) == 2
        assert test_results[0]["id"] == "vid1"
        assert test_results[0]["source"] == "test"

    def test_search_skips_unconfigured_sources(self, config_manager, cache_manager):
        """search should skip unconfigured sources."""
        video_manager = VideoManager(config_manager, cache_manager)

        unconfigured = FakeVideoSource("unconfigured", configured=False)
        unconfigured._search_results = [{"id": "vid1", "title": "Should not appear"}]
        video_manager._register_source(unconfigured)

        results = video_manager.search("test query")

        # Unconfigured source results should not appear
        assert all(r.get("source") != "unconfigured" for r in results)

    def test_search_handles_source_error(self, config_manager, cache_manager):
        """search should handle errors from sources gracefully."""
        video_manager = VideoManager(config_manager, cache_manager)

        # Create a source that raises an error on search
        class ErrorSource(FakeVideoSource):
            def search(self, query: str, max_results: int = 10):
                raise Exception("API Error")

        error_source = ErrorSource("error_source")
        video_manager._register_source(error_source)

        # Should not raise, just return empty results
        results = video_manager.search("test query")
        assert results == []

    def test_search_respects_max_results(self, config_manager, cache_manager):
        """search should respect max_results parameter."""
        video_manager = VideoManager(config_manager, cache_manager)

        fake_source = FakeVideoSource("test")
        fake_source._search_results = [{"id": f"vid{i}", "title": f"Video {i}"} for i in range(20)]
        video_manager._register_source(fake_source)

        results = video_manager.search("test query", max_results=5)

        test_results = [r for r in results if r.get("source") == "test"]
        assert len(test_results) == 5


# =============================================================================
# Get Video Info Tests
# =============================================================================


class TestGetVideoInfo:
    """Tests for get_video_info functionality."""

    def test_get_video_info_delegates_to_source(self, config_manager, cache_manager):
        """get_video_info should delegate to the correct source."""
        video_manager = VideoManager(config_manager, cache_manager)

        fake_source = FakeVideoSource("test")
        fake_source._video_info = {"id": "vid1", "title": "Test Video"}
        video_manager._register_source(fake_source)

        info = video_manager.get_video_info("test", "vid1")

        assert info is not None
        assert info["id"] == "vid1"
        assert info["title"] == "Test Video"
        assert info["source"] == "test"

    def test_get_video_info_unknown_source(self, config_manager, cache_manager):
        """get_video_info should return None for unknown source."""
        video_manager = VideoManager(config_manager, cache_manager)

        info = video_manager.get_video_info("nonexistent", "vid1")

        assert info is None

    def test_get_video_info_not_found(self, config_manager, cache_manager):
        """get_video_info should return None if video not found."""
        video_manager = VideoManager(config_manager, cache_manager)

        fake_source = FakeVideoSource("test")
        fake_source._video_info = None
        video_manager._register_source(fake_source)

        info = video_manager.get_video_info("test", "nonexistent")

        assert info is None


# =============================================================================
# Download Tests
# =============================================================================


class TestDownload:
    """Tests for download functionality."""

    def test_download_delegates_to_source(self, config_manager, cache_manager):
        """download should delegate to the correct source."""
        video_manager = VideoManager(config_manager, cache_manager)

        fake_source = FakeVideoSource("test")
        video_manager._register_source(fake_source)

        callback = Mock()
        video_manager.download("test", "vid1", 42, status_callback=callback)

        assert len(fake_source.download_calls) == 1
        assert fake_source.download_calls[0][0] == "vid1"
        assert fake_source.download_calls[0][1] == 42

    def test_download_unknown_source(self, config_manager, cache_manager):
        """download should call error callback for unknown source."""
        video_manager = VideoManager(config_manager, cache_manager)

        callback = Mock()
        result = video_manager.download("nonexistent", "vid1", 42, status_callback=callback)

        assert result is None
        callback.assert_called_once_with("error", None, "Unknown source: nonexistent")

    def test_download_returns_cached_path(self, config_manager, cache_manager, temp_cache_dir):
        """download should return cached path if video is already cached."""
        video_manager = VideoManager(config_manager, cache_manager)

        fake_source = FakeVideoSource("test")
        cached_path = Path(temp_cache_dir) / "test_video.mp4"
        cached_path.touch()
        fake_source._cached_path = cached_path
        video_manager._register_source(fake_source)

        callback = Mock()
        result = video_manager.download("test", "vid1", 42, status_callback=callback)

        assert result == str(cached_path)
        callback.assert_called_once_with("ready", str(cached_path), None)


# =============================================================================
# Cache Operations Tests
# =============================================================================


class TestCacheOperations:
    """Tests for cache-related operations."""

    def test_get_cached_path_delegates_to_source(
        self, config_manager, cache_manager, temp_cache_dir
    ):
        """get_cached_path should delegate to the correct source."""
        video_manager = VideoManager(config_manager, cache_manager)

        fake_source = FakeVideoSource("test")
        cached_path = Path(temp_cache_dir) / "test_video.mp4"
        cached_path.touch()
        fake_source._cached_path = cached_path
        video_manager._register_source(fake_source)

        result = video_manager.get_cached_path("test", "vid1")

        assert result == cached_path

    def test_get_cached_path_unknown_source(self, config_manager, cache_manager):
        """get_cached_path should return None for unknown source."""
        video_manager = VideoManager(config_manager, cache_manager)

        result = video_manager.get_cached_path("nonexistent", "vid1")

        assert result is None

    def test_is_cached_delegates_to_source(self, config_manager, cache_manager):
        """is_cached should delegate to the correct source."""
        video_manager = VideoManager(config_manager, cache_manager)

        fake_source = FakeVideoSource("test")
        fake_source._is_cached = True
        video_manager._register_source(fake_source)

        assert video_manager.is_cached("test", "vid1") is True

    def test_is_cached_unknown_source(self, config_manager, cache_manager):
        """is_cached should return False for unknown source."""
        video_manager = VideoManager(config_manager, cache_manager)

        assert video_manager.is_cached("nonexistent", "vid1") is False

    def test_cleanup_cache_delegates_to_cache_manager(self, config_manager, cache_manager):
        """cleanup_cache should delegate to CacheManager."""
        video_manager = VideoManager(config_manager, cache_manager)

        with patch.object(cache_manager, "cleanup", return_value=5) as mock_cleanup:
            protected = {("youtube", "vid1"), ("youtube", "vid2")}
            result = video_manager.cleanup_cache(protected)

        mock_cleanup.assert_called_once_with(protected)
        assert result == 5
