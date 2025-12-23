"""
Unit tests for CacheManager.
"""

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from kbox.cache import CacheManager


def _set_mtime(path: Path, seconds_ago: float) -> None:
    """Set file mtime to a specific time in the past."""
    mtime = time.time() - seconds_ago
    os.utime(path, (mtime, mtime))


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_config_manager(temp_cache_dir):
    """Create a mock ConfigManager for tests."""
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "cache_directory": temp_cache_dir,
        "cache_max_size_gb": "10",
    }.get(key, default)
    config.get_int.side_effect = lambda key, default=None: {
        "cache_max_size_gb": 10,
    }.get(key, default)
    return config


@pytest.fixture
def cache_manager(mock_config_manager):
    """Create a CacheManager instance."""
    return CacheManager(mock_config_manager)


def test_get_source_directory(cache_manager, temp_cache_dir):
    """Test getting source-specific cache directory."""
    youtube_dir = cache_manager.get_source_directory("youtube")
    assert youtube_dir == Path(temp_cache_dir) / "youtube"
    assert youtube_dir.exists()

    vimeo_dir = cache_manager.get_source_directory("vimeo")
    assert vimeo_dir == Path(temp_cache_dir) / "vimeo"
    assert vimeo_dir.exists()


def test_get_file_path(cache_manager, temp_cache_dir):
    """Test getting path to cached file."""
    # Not cached
    assert cache_manager.get_file_path("youtube", "vid1") is None

    # Create a cached file
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)
    test_file = youtube_dir / "vid1.mp4"
    test_file.touch()

    path = cache_manager.get_file_path("youtube", "vid1")
    assert path is not None
    assert path == test_file


def test_get_file_path_touches_file(cache_manager, temp_cache_dir):
    """Test that get_file_path updates mtime for LRU tracking."""
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)
    test_file = youtube_dir / "vid1.mp4"
    test_file.touch()

    original_mtime = test_file.stat().st_mtime

    time.sleep(0.1)
    path = cache_manager.get_file_path("youtube", "vid1", touch=True)

    assert path is not None
    new_mtime = path.stat().st_mtime
    assert new_mtime > original_mtime


def test_get_file_path_no_touch(cache_manager, temp_cache_dir):
    """Test that get_file_path can skip touching when requested."""
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)
    test_file = youtube_dir / "vid1.mp4"
    test_file.touch()

    original_mtime = test_file.stat().st_mtime

    time.sleep(0.1)
    path = cache_manager.get_file_path("youtube", "vid1", touch=False)

    assert path is not None
    new_mtime = path.stat().st_mtime
    assert new_mtime == original_mtime


def test_is_cached(cache_manager, temp_cache_dir):
    """Test checking if file is cached."""
    assert cache_manager.is_cached("youtube", "vid1") is False

    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)
    (youtube_dir / "vid1.mp4").touch()

    assert cache_manager.is_cached("youtube", "vid1") is True


def test_get_output_template(cache_manager, temp_cache_dir):
    """Test getting output template for downloads."""
    template = cache_manager.get_output_template("youtube", "vid1")
    assert template == str(Path(temp_cache_dir) / "youtube" / "vid1.%(ext)s")


def test_get_cache_files(cache_manager, temp_cache_dir):
    """Test getting list of cached files sorted by mtime."""
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)

    # Create files with explicit mtimes for ordering
    file1 = youtube_dir / "old_video.mp4"
    file2 = youtube_dir / "new_video.mp4"
    file3 = youtube_dir / "mid_video.webm"

    file1.write_bytes(b"x" * 1000)
    _set_mtime(file1, 3600)  # 1 hour ago (oldest)

    file3.write_bytes(b"x" * 2000)
    _set_mtime(file3, 1800)  # 30 min ago (middle)

    file2.write_bytes(b"x" * 500)
    _set_mtime(file2, 60)  # 1 min ago (newest)

    cache_files = cache_manager._get_cache_files()

    # Should be sorted by mtime, oldest first
    assert len(cache_files) == 3
    assert cache_files[0][0] == file1
    assert cache_files[1][0] == file3
    assert cache_files[2][0] == file2

    # Check sizes
    assert cache_files[0][1] == 1000
    assert cache_files[1][1] == 2000
    assert cache_files[2][1] == 500


def test_get_cache_files_multiple_sources(cache_manager, temp_cache_dir):
    """Test getting files from multiple source directories."""
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)
    vimeo_dir = Path(temp_cache_dir) / "vimeo"
    vimeo_dir.mkdir(exist_ok=True)

    (youtube_dir / "yt_vid.mp4").write_bytes(b"x" * 100)
    (vimeo_dir / "vimeo_vid.mp4").write_bytes(b"x" * 200)

    cache_files = cache_manager._get_cache_files()
    assert len(cache_files) == 2


def test_cleanup_under_limit(cache_manager, temp_cache_dir):
    """Test that cleanup does nothing when under limit."""
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)

    test_file = youtube_dir / "vid1.mp4"
    test_file.write_bytes(b"x" * 1000)

    deleted_count = cache_manager.cleanup()

    assert deleted_count == 0
    assert test_file.exists()


def test_cleanup_over_limit(temp_cache_dir):
    """Test that cleanup evicts oldest files when over limit."""
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "cache_directory": temp_cache_dir,
    }.get(key, default)
    config.get_int.side_effect = lambda key, default=None: {
        "cache_max_size_gb": 0,  # Force eviction
    }.get(key, 0)

    manager = CacheManager(config)

    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)

    old_file = youtube_dir / "old.mp4"
    old_file.write_bytes(b"x" * 1000)
    _set_mtime(old_file, 3600)  # 1 hour ago

    new_file = youtube_dir / "new.mp4"
    new_file.write_bytes(b"x" * 1000)

    deleted_count = manager.cleanup()

    assert deleted_count == 2
    assert not old_file.exists()
    assert not new_file.exists()


def test_cleanup_respects_protected_keys(temp_cache_dir):
    """Test that cleanup does not delete protected files identified by (source, id)."""
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "cache_directory": temp_cache_dir,
    }.get(key, default)
    config.get_int.side_effect = lambda key, default=None: {
        "cache_max_size_gb": 0,  # Force eviction
    }.get(key, 0)

    manager = CacheManager(config)

    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)

    protected_file = youtube_dir / "protected_vid.mp4"
    protected_file.write_bytes(b"x" * 1000)
    _set_mtime(protected_file, 3600)  # 1 hour ago (older, would be evicted first)

    unprotected_file = youtube_dir / "unprotected_vid.mp4"
    unprotected_file.write_bytes(b"x" * 1000)

    # Protect using (source, file_id) tuple
    deleted_count = manager.cleanup(protected={("youtube", "protected_vid")})

    assert deleted_count == 1
    assert protected_file.exists()
    assert not unprotected_file.exists()


def test_cleanup_same_id_different_sources(temp_cache_dir):
    """Test that same file_id in different sources are treated separately."""
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "cache_directory": temp_cache_dir,
    }.get(key, default)
    config.get_int.side_effect = lambda key, default=None: {
        "cache_max_size_gb": 0,  # Force eviction
    }.get(key, 0)

    manager = CacheManager(config)

    # Create same file ID in two different sources
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)
    vimeo_dir = Path(temp_cache_dir) / "vimeo"
    vimeo_dir.mkdir(exist_ok=True)

    youtube_file = youtube_dir / "vid123.mp4"
    youtube_file.write_bytes(b"x" * 1000)
    _set_mtime(youtube_file, 3600)  # 1 hour ago (older, would be evicted first)

    vimeo_file = vimeo_dir / "vid123.mp4"
    vimeo_file.write_bytes(b"x" * 1000)

    # Protect only YouTube's vid123, Vimeo's should be deleted
    deleted_count = manager.cleanup(protected={("youtube", "vid123")})

    assert deleted_count == 1
    assert youtube_file.exists()
    assert not vimeo_file.exists()


def test_get_cache_stats(cache_manager, temp_cache_dir):
    """Test getting cache statistics."""
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)

    (youtube_dir / "vid1.mp4").write_bytes(b"x" * 1000)
    (youtube_dir / "vid2.mp4").write_bytes(b"x" * 2000)

    stats = cache_manager.get_cache_stats()

    assert stats["file_count"] == 2
    assert stats["total_size_bytes"] == 3000
    assert stats["max_size_gb"] == 10
