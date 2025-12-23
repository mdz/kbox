"""
Unit tests for YouTubeClient.

Uses mocks to avoid actual API calls.
"""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from kbox.youtube import YouTubeClient


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_config_manager(temp_cache_dir):
    """Create a mock ConfigManager for tests."""
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "youtube_api_key": "fake_api_key",
        "cache_directory": temp_cache_dir,
        "video_max_resolution": "480",
    }.get(key, default)
    config.get_int.side_effect = lambda key, default=None: {
        "video_max_resolution": 480,
    }.get(key, default)
    return config


@pytest.fixture
def youtube_client(temp_cache_dir, mock_config_manager):
    """Create a YouTubeClient instance with mocked API."""
    with patch("kbox.youtube.build") as mock_build:
        mock_youtube = Mock()
        mock_build.return_value = mock_youtube
        client = YouTubeClient(mock_config_manager)
        # Force initialization of the lazy client
        client._youtube = mock_youtube
        client._last_api_key = "fake_api_key"
        yield client


def test_search_success(youtube_client):
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

    youtube_client._youtube.search.return_value = mock_search
    youtube_client._youtube.videos.return_value = mock_videos

    # Test search
    results = youtube_client.search("test query")

    # Verify search was called with "karaoke" appended
    call_args = youtube_client._youtube.search.return_value.list.call_args
    assert "karaoke" in call_args[1]["q"].lower()

    # Verify results
    assert len(results) == 2
    assert results[0]["id"] == "vid1"
    assert results[0]["title"] == "Test Song 1"
    assert results[0]["duration_seconds"] == 210  # 3:30
    assert results[1]["duration_seconds"] == 255  # 4:15


def test_search_no_results(youtube_client):
    """Test search with no results."""
    mock_search_response = {"items": []}

    mock_search = Mock()
    mock_search.list.return_value.execute.return_value = mock_search_response
    youtube_client._youtube.search.return_value = mock_search

    results = youtube_client.search("nonexistent")
    assert len(results) == 0


def test_parse_duration(youtube_client):
    """Test duration parsing."""
    assert youtube_client._parse_duration("PT3M30S") == 210  # 3:30
    assert youtube_client._parse_duration("PT1H5M30S") == 3930  # 1:05:30
    assert youtube_client._parse_duration("PT45S") == 45
    assert youtube_client._parse_duration("PT2H") == 7200
    assert youtube_client._parse_duration("") is None
    assert youtube_client._parse_duration("invalid") is None


def test_get_video_info(youtube_client):
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
    youtube_client._youtube.videos.return_value = mock_videos

    info = youtube_client.get_video_info("vid1")

    assert info is not None
    assert info["id"] == "vid1"
    assert info["title"] == "Test Song"
    assert info["duration_seconds"] == 210


def test_get_video_info_not_found(youtube_client):
    """Test getting info for non-existent video."""
    mock_response = {"items": []}

    mock_videos = Mock()
    mock_videos.list.return_value.execute.return_value = mock_response
    youtube_client._youtube.videos.return_value = mock_videos

    info = youtube_client.get_video_info("nonexistent")
    assert info is None


def test_is_downloaded(youtube_client, temp_cache_dir):
    """Test checking if video is downloaded."""
    # Not downloaded
    assert youtube_client.is_downloaded("vid1") is False

    # Create a fake downloaded file in youtube subdirectory
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)
    fake_file = youtube_dir / "vid1.mp4"
    fake_file.touch()

    assert youtube_client.is_downloaded("vid1") is True


def test_get_download_path(youtube_client, temp_cache_dir):
    """Test getting download path."""
    # Not downloaded
    assert youtube_client.get_download_path("vid1") is None

    # Create a fake downloaded file in youtube subdirectory
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)
    fake_file = youtube_dir / "vid1.mp4"
    fake_file.touch()

    path = youtube_client.get_download_path("vid1")
    assert path is not None
    assert path.exists()


def test_download_video_already_cached(youtube_client, temp_cache_dir):
    """Test download when video is already cached."""
    # Create a fake cached file in youtube subdirectory
    youtube_dir = Path(temp_cache_dir) / "youtube"
    youtube_dir.mkdir(exist_ok=True)
    fake_file = youtube_dir / "vid1.mp4"
    fake_file.touch()

    callback_calls = []

    def status_callback(status, path, error):
        callback_calls.append((status, path, error))

    result = youtube_client.download_video("vid1", 1, status_callback)

    # Should return path immediately
    assert result == str(fake_file)
    # Callback should be called with ready status
    assert len(callback_calls) == 1
    assert callback_calls[0][0] == "ready"
    assert callback_calls[0][1] == str(fake_file)
