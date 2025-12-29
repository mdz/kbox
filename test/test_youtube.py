"""
Unit tests for YouTubeSource.

Uses mocks to avoid actual API calls.
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from kbox.youtube import YouTubeSource


@pytest.fixture
def temp_storage_dir():
    """Create a temporary storage directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_config_manager(temp_storage_dir):
    """Create a mock ConfigManager for tests."""
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "youtube_api_key": "fake_api_key",
        "cache_directory": temp_storage_dir,
        "video_max_resolution": "480",
        "cache_max_size_gb": "10",
    }.get(key, default)
    config.get_int.side_effect = lambda key, default=None: {
        "video_max_resolution": 480,
        "cache_max_size_gb": 10,
    }.get(key, default)
    return config


@pytest.fixture
def youtube_source(mock_config_manager):
    """Create a YouTubeSource instance with mocked API."""
    with patch("kbox.youtube.build") as mock_build:
        mock_youtube = Mock()
        mock_build.return_value = mock_youtube
        source = YouTubeSource(mock_config_manager)
        # Force initialization of the lazy client
        source._youtube = mock_youtube
        source._last_api_key = "fake_api_key"
        yield source


def test_source_id(youtube_source):
    """Test source_id property."""
    assert youtube_source.source_id == "youtube"


def test_search_success(youtube_source):
    """Test successful YouTube search."""
    # Mock search response
    mock_search_response = {
        "items": [
            {
                "id": {"videoId": "vid1"},
                "snippet": {
                    "title": "Test Song 1",
                    "thumbnails": {"default": {"url": "http://thumb1.jpg"}},
                    "channelTitle": "Test Channel",
                },
            },
            {
                "id": {"videoId": "vid2"},
                "snippet": {
                    "title": "Test Song 2",
                    "thumbnails": {"default": {"url": "http://thumb2.jpg"}},
                    "channelTitle": "Test Channel",
                },
            },
        ]
    }

    # Mock videos().list() response
    mock_videos_response = {
        "items": [
            {
                "id": "vid1",
                "snippet": {
                    "title": "Test Song 1",
                    "thumbnails": {"default": {"url": "http://thumb1.jpg"}},
                    "channelTitle": "Test Channel",
                    "description": "Description 1",
                },
                "contentDetails": {"duration": "PT3M30S"},
            },
            {
                "id": "vid2",
                "snippet": {
                    "title": "Test Song 2",
                    "thumbnails": {"default": {"url": "http://thumb2.jpg"}},
                    "channelTitle": "Test Channel",
                    "description": "Description 2",
                },
                "contentDetails": {"duration": "PT4M15S"},
            },
        ]
    }

    # Setup mocks
    mock_search = Mock()
    mock_search.list.return_value.execute.return_value = mock_search_response

    mock_videos = Mock()
    mock_videos.list.return_value.execute.return_value = mock_videos_response

    youtube_source._youtube.search.return_value = mock_search
    youtube_source._youtube.videos.return_value = mock_videos

    # Test search
    results = youtube_source.search("test query")

    # Verify search was called with "karaoke" appended
    call_args = youtube_source._youtube.search.return_value.list.call_args
    assert "karaoke" in call_args[1]["q"].lower()

    # Verify results
    assert len(results) == 2
    assert results[0]["id"] == "vid1"
    assert results[0]["title"] == "Test Song 1"
    assert results[0]["duration_seconds"] == 210  # 3:30
    assert results[1]["duration_seconds"] == 255  # 4:15


def test_search_no_results(youtube_source):
    """Test search with no results."""
    mock_search_response = {"items": []}

    mock_search = Mock()
    mock_search.list.return_value.execute.return_value = mock_search_response
    youtube_source._youtube.search.return_value = mock_search

    results = youtube_source.search("nonexistent")
    assert len(results) == 0


def test_parse_duration(youtube_source):
    """Test duration parsing."""
    assert youtube_source._parse_duration("PT3M30S") == 210  # 3:30
    assert youtube_source._parse_duration("PT1H5M30S") == 3930  # 1:05:30
    assert youtube_source._parse_duration("PT45S") == 45
    assert youtube_source._parse_duration("PT2H") == 7200
    assert youtube_source._parse_duration("") is None
    assert youtube_source._parse_duration("invalid") is None


def test_get_video_info(youtube_source):
    """Test getting video information."""
    mock_response = {
        "items": [
            {
                "id": "vid1",
                "snippet": {
                    "title": "Test Song",
                    "thumbnails": {"default": {"url": "http://thumb.jpg"}},
                    "channelTitle": "Test Channel",
                    "description": "Description",
                },
                "contentDetails": {"duration": "PT3M30S"},
            }
        ]
    }

    mock_videos = Mock()
    mock_videos.list.return_value.execute.return_value = mock_response
    youtube_source._youtube.videos.return_value = mock_videos

    info = youtube_source.get_video_info("vid1")

    assert info is not None
    assert info["id"] == "vid1"
    assert info["title"] == "Test Song"
    assert info["duration_seconds"] == 210


def test_get_video_info_not_found(youtube_source):
    """Test getting info for non-existent video."""
    mock_response = {"items": []}

    mock_videos = Mock()
    mock_videos.list.return_value.execute.return_value = mock_response
    youtube_source._youtube.videos.return_value = mock_videos

    info = youtube_source.get_video_info("nonexistent")
    assert info is None


def test_find_downloaded_file(youtube_source, temp_storage_dir):
    """Test finding downloaded file in output directory."""
    output_dir = Path(temp_storage_dir) / "test_video"
    output_dir.mkdir(parents=True)

    # No file yet
    assert youtube_source._find_downloaded_file(output_dir) is None

    # Create video file
    video_file = output_dir / "video.mp4"
    video_file.touch()

    found = youtube_source._find_downloaded_file(output_dir)
    assert found == video_file


def test_find_downloaded_file_webm(youtube_source, temp_storage_dir):
    """Test finding downloaded file with webm extension."""
    output_dir = Path(temp_storage_dir) / "test_video"
    output_dir.mkdir(parents=True)

    # Create webm file
    video_file = output_dir / "video.webm"
    video_file.touch()

    found = youtube_source._find_downloaded_file(output_dir)
    assert found == video_file
