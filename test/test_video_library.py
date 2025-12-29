"""
Unit tests for VideoLibrary.

Tests the VideoLibrary facade that manages video sources and storage.
"""

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import Mock

import pytest

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.video_library import VideoLibrary, VideoSource


class FakeVideoSource(VideoSource):
    """Fake video source for testing."""

    def __init__(self, source_id: str = "fake", configured: bool = True):
        self._source_id = source_id
        self._configured = configured
        self._search_results: List[Dict[str, Any]] = []
        self._video_info: Optional[Dict[str, Any]] = None
        self.download_calls: List[tuple] = []
        self.download_should_succeed = True
        self.search_error: Optional[Exception] = None

    @property
    def source_id(self) -> str:
        return self._source_id

    def is_configured(self) -> bool:
        return self._configured

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        if self.search_error:
            raise self.search_error
        return self._search_results[:max_results]

    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        if self._video_info and self._video_info.get("id") == video_id:
            return self._video_info.copy()
        return None

    def download(
        self,
        video_id: str,
        output_dir: Path,
        status_callback: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
    ) -> None:
        self.download_calls.append((video_id, output_dir, status_callback))
        if self.download_should_succeed:
            # Simulate download by creating file
            output_dir.mkdir(parents=True, exist_ok=True)
            video_file = output_dir / "video.mp4"
            video_file.write_bytes(b"x" * 1000)
            if status_callback:
                status_callback("ready", str(video_file), None)
        else:
            if status_callback:
                status_callback("error", None, "Download failed")


def _set_mtime(path: Path, seconds_ago: float) -> None:
    """Set file mtime to a specific time in the past."""
    mtime = time.time() - seconds_ago
    os.utime(path, (mtime, mtime))


@pytest.fixture
def temp_db():
    """Create a temporary database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path=path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def temp_storage_dir():
    """Create a temporary storage directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def config_manager(temp_db, temp_storage_dir):
    """Create a ConfigManager with test configuration."""
    config = ConfigManager(temp_db)
    config.set("youtube_api_key", "test_key")
    config.set("cache_directory", temp_storage_dir)
    return config


@pytest.fixture
def video_library(config_manager):
    """Create a VideoLibrary instance."""
    library = VideoLibrary(config_manager)
    yield library


# =============================================================================
# Video ID Handling Tests
# =============================================================================


class TestVideoIdHandling:
    """Tests for opaque video ID parsing and creation."""

    def test_make_video_id(self, video_library):
        """Test creating opaque video IDs."""
        video_id = video_library._make_video_id("youtube", "abc123")
        assert video_id == "youtube:abc123"

    def test_parse_video_id(self, video_library):
        """Test parsing opaque video IDs."""
        source, source_id = video_library._parse_video_id("youtube:abc123")
        assert source == "youtube"
        assert source_id == "abc123"

    def test_parse_video_id_with_colon_in_source_id(self, video_library):
        """Test parsing video IDs where source_id contains colons."""
        source, source_id = video_library._parse_video_id("youtube:abc:123:xyz")
        assert source == "youtube"
        assert source_id == "abc:123:xyz"

    def test_parse_invalid_video_id(self, video_library):
        """Test parsing invalid video ID raises ValueError."""
        with pytest.raises(ValueError):
            video_library._parse_video_id("invalid-no-colon")


# =============================================================================
# Storage Management Tests
# =============================================================================


class TestStorageManagement:
    """Tests for video storage directory management."""

    def test_get_video_directory(self, video_library, temp_storage_dir):
        """Test getting video directory path."""
        video_dir = video_library._get_video_directory("youtube:abc123")
        assert video_dir == Path(temp_storage_dir) / "youtube" / "abc123"

    def test_find_video_file_mp4(self, video_library, temp_storage_dir):
        """Test finding video file with .mp4 extension."""
        video_dir = Path(temp_storage_dir) / "youtube" / "test123"
        video_dir.mkdir(parents=True)
        video_file = video_dir / "video.mp4"
        video_file.touch()

        found = video_library._find_video_file(video_dir)
        assert found == video_file

    def test_find_video_file_webm(self, video_library, temp_storage_dir):
        """Test finding video file with .webm extension."""
        video_dir = Path(temp_storage_dir) / "youtube" / "test123"
        video_dir.mkdir(parents=True)
        video_file = video_dir / "video.webm"
        video_file.touch()

        found = video_library._find_video_file(video_dir)
        assert found == video_file

    def test_find_video_file_not_found(self, video_library, temp_storage_dir):
        """Test finding video file when directory is empty."""
        video_dir = Path(temp_storage_dir) / "youtube" / "test123"
        video_dir.mkdir(parents=True)

        found = video_library._find_video_file(video_dir)
        assert found is None


# =============================================================================
# Search Tests
# =============================================================================


class TestSearch:
    """Tests for search functionality."""

    def test_search_returns_opaque_ids(self, config_manager):
        """Search results should have opaque IDs."""
        library = VideoLibrary(config_manager)
        fake_source = FakeVideoSource("youtube")
        fake_source._search_results = [
            {"id": "vid1", "title": "Video 1"},
            {"id": "vid2", "title": "Video 2"},
        ]
        library.register_source(fake_source)

        results = library.search("test query")

        assert len(results) == 2
        assert results[0]["id"] == "youtube:vid1"
        assert results[1]["id"] == "youtube:vid2"

    def test_search_multiple_sources(self, config_manager):
        """Search should aggregate results from multiple sources."""
        library = VideoLibrary(config_manager)

        youtube_source = FakeVideoSource("youtube")
        youtube_source._search_results = [{"id": "yt1", "title": "YT Video"}]
        library.register_source(youtube_source)

        vimeo_source = FakeVideoSource("vimeo")
        vimeo_source._search_results = [{"id": "vim1", "title": "Vimeo Video"}]
        library.register_source(vimeo_source)

        results = library.search("test query")

        assert len(results) == 2
        ids = {r["id"] for r in results}
        assert "youtube:yt1" in ids
        assert "vimeo:vim1" in ids

    def test_search_skips_unconfigured_sources(self, config_manager):
        """Search should skip unconfigured sources."""
        library = VideoLibrary(config_manager)

        configured = FakeVideoSource("youtube")
        configured._search_results = []
        library.register_source(configured)

        unconfigured = FakeVideoSource("unconfigured", configured=False)
        unconfigured._search_results = [{"id": "vid1", "title": "Should not appear"}]
        library.register_source(unconfigured)

        results = library.search("test query")
        assert all("unconfigured" not in r.get("id", "") for r in results)

    def test_search_propagates_error_when_all_sources_fail(self, config_manager):
        """Search should propagate exception when all configured sources fail."""
        library = VideoLibrary(config_manager)

        failing_source = FakeVideoSource("youtube")
        failing_source.search_error = RuntimeError("API error")
        library.register_source(failing_source)

        with pytest.raises(RuntimeError, match="API error"):
            library.search("test query")

    def test_search_returns_partial_results_when_some_sources_fail(self, config_manager):
        """Search should return results from working sources even if others fail."""
        library = VideoLibrary(config_manager)

        working_source = FakeVideoSource("youtube")
        working_source._search_results = [{"id": "vid1", "title": "Video 1"}]
        library.register_source(working_source)

        failing_source = FakeVideoSource("vimeo")
        failing_source.search_error = RuntimeError("API error")
        library.register_source(failing_source)

        results = library.search("test query")
        assert len(results) == 1
        assert results[0]["id"] == "youtube:vid1"


# =============================================================================
# Get Info Tests
# =============================================================================


class TestGetInfo:
    """Tests for get_info functionality."""

    def test_get_info_returns_opaque_id(self, config_manager):
        """get_info should return opaque ID in result."""
        library = VideoLibrary(config_manager)
        fake_source = FakeVideoSource("youtube")
        fake_source._video_info = {
            "id": "abc123",
            "title": "Test Video",
        }
        library.register_source(fake_source)

        info = library.get_info("youtube:abc123")

        assert info is not None
        assert info["id"] == "youtube:abc123"
        assert info["title"] == "Test Video"

    def test_get_info_unknown_source(self, video_library):
        """get_info should return None for unknown source."""
        info = video_library.get_info("nonexistent:vid123")
        assert info is None

    def test_get_info_invalid_video_id(self, video_library):
        """get_info should raise ValueError for invalid video ID format."""
        with pytest.raises(ValueError):
            video_library.get_info("invalid-no-colon")


# =============================================================================
# Availability Tests
# =============================================================================


class TestAvailability:
    """Tests for video availability checks and requests."""

    def test_is_available_false_when_not_downloaded(self, video_library, temp_storage_dir):
        """is_available should return False when video not downloaded."""
        assert video_library.is_available("youtube:notdownloaded") is False

    def test_is_available_true_when_downloaded(self, video_library, temp_storage_dir):
        """is_available should return True when video is downloaded."""
        # Create video directory and file
        video_dir = Path(temp_storage_dir) / "youtube" / "test123"
        video_dir.mkdir(parents=True)
        (video_dir / "video.mp4").write_bytes(b"x" * 1000)

        assert video_library.is_available("youtube:test123") is True

    def test_get_path_returns_path_when_exists(self, video_library, temp_storage_dir):
        """get_path should return path when video exists."""
        video_dir = Path(temp_storage_dir) / "youtube" / "test123"
        video_dir.mkdir(parents=True)
        video_file = video_dir / "video.mp4"
        video_file.write_bytes(b"x" * 1000)

        path = video_library.get_path("youtube:test123")
        assert path == video_file

    def test_get_path_returns_none_when_not_exists(self, video_library):
        """get_path should return None when video doesn't exist."""
        path = video_library.get_path("youtube:nonexistent")
        assert path is None

    def test_request_returns_path_when_cached(self, video_library, temp_storage_dir):
        """request should return path immediately when video is cached."""
        video_dir = Path(temp_storage_dir) / "youtube" / "test123"
        video_dir.mkdir(parents=True)
        video_file = video_dir / "video.mp4"
        video_file.write_bytes(b"x" * 1000)

        callback = Mock()
        result = video_library.request("youtube:test123", callback=callback)

        assert result == str(video_file)
        callback.assert_called_once_with("ready", str(video_file), None)

    def test_request_starts_download_when_not_cached(self, config_manager, temp_storage_dir):
        """request should start download when video not cached."""
        library = VideoLibrary(config_manager)

        fake_source = FakeVideoSource("fake")
        library.register_source(fake_source)

        callback = Mock()
        result = library.request("fake:newvideo", callback=callback)

        # Should return None (download is async)
        assert result is None
        # Download should have been called
        assert len(fake_source.download_calls) == 1
        assert fake_source.download_calls[0][0] == "newvideo"


# =============================================================================
# Storage Lifecycle Tests
# =============================================================================


class TestStorageLifecycle:
    """Tests for storage cleanup and LRU eviction."""

    def test_manage_storage_under_limit(self, video_library, temp_storage_dir):
        """manage_storage should do nothing when under limit."""
        # Create a small video
        video_dir = Path(temp_storage_dir) / "youtube" / "small"
        video_dir.mkdir(parents=True)
        (video_dir / "video.mp4").write_bytes(b"x" * 1000)

        deleted = video_library.manage_storage()
        assert deleted == 0

    def test_manage_storage_evicts_oldest(self, config_manager, temp_storage_dir):
        """manage_storage should evict oldest videos first."""
        # Set limit to 0 to force eviction
        config_manager.set("cache_max_size_gb", "0")

        library = VideoLibrary(config_manager)

        # Create two videos
        old_dir = Path(temp_storage_dir) / "youtube" / "old"
        old_dir.mkdir(parents=True)
        old_file = old_dir / "video.mp4"
        old_file.write_bytes(b"x" * 1000)
        _set_mtime(old_dir, 3600)  # 1 hour ago

        new_dir = Path(temp_storage_dir) / "youtube" / "new"
        new_dir.mkdir(parents=True)
        new_file = new_dir / "video.mp4"
        new_file.write_bytes(b"x" * 1000)

        deleted = library.manage_storage()

        assert deleted == 2
        assert not old_dir.exists()
        assert not new_dir.exists()

    def test_manage_storage_respects_keep_set(self, config_manager, temp_storage_dir):
        """manage_storage should not evict videos in keep set."""
        # Set limit to 0 to force eviction
        config_manager.set("cache_max_size_gb", "0")

        library = VideoLibrary(config_manager)

        # Create protected video
        protected_dir = Path(temp_storage_dir) / "youtube" / "protected"
        protected_dir.mkdir(parents=True)
        (protected_dir / "video.mp4").write_bytes(b"x" * 1000)
        _set_mtime(protected_dir, 3600)  # older

        # Create unprotected video
        unprotected_dir = Path(temp_storage_dir) / "youtube" / "unprotected"
        unprotected_dir.mkdir(parents=True)
        (unprotected_dir / "video.mp4").write_bytes(b"x" * 1000)

        deleted = library.manage_storage(keep={"youtube:protected"})

        assert deleted == 1
        assert protected_dir.exists()
        assert not unprotected_dir.exists()

    def test_get_storage_stats(self, video_library, temp_storage_dir):
        """get_storage_stats should return accurate statistics."""
        # Create test videos
        for i in range(3):
            video_dir = Path(temp_storage_dir) / "youtube" / f"vid{i}"
            video_dir.mkdir(parents=True)
            (video_dir / "video.mp4").write_bytes(b"x" * 1000)

        stats = video_library.get_storage_stats()

        assert stats["video_count"] == 3
        assert stats["total_size_bytes"] == 3000
