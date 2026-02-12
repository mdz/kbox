"""
Unit tests for YouTubeSource.

Uses mocks to avoid actual API/yt-dlp calls.
"""

import shutil
import tempfile
from unittest.mock import MagicMock, Mock, patch

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


# =========================================================================
# yt-dlp search tests (no API key)
# =========================================================================


@pytest.fixture
def mock_config_no_api_key(temp_storage_dir):
    """Create a mock ConfigManager without an API key."""
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "youtube_api_key": None,
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
def youtube_source_no_api(mock_config_no_api_key):
    """Create a YouTubeSource without an API key (yt-dlp only)."""
    source = YouTubeSource(mock_config_no_api_key)
    return source


def _make_ytdlp_search_result():
    """Helper: mock yt-dlp search result with two entries."""
    return {
        "entries": [
            {
                "id": "ytdlp_vid1",
                "title": "Karaoke Song 1",
                "thumbnail": "http://thumb1.jpg",
                "channel": "Karaoke Channel",
                "uploader": "Karaoke Uploader",
                "duration": 195,
                "description": "A great karaoke track",
            },
            {
                "id": "ytdlp_vid2",
                "title": "Karaoke Song 2",
                "thumbnail": "http://thumb2.jpg",
                "channel": "",
                "uploader": "Some Uploader",
                "duration": 240,
                "description": "Another karaoke track",
            },
        ]
    }


def _make_ytdlp_video_info():
    """Helper: mock yt-dlp single video info result."""
    return {
        "id": "info_vid1",
        "title": "Info Song",
        "thumbnail": "http://info_thumb.jpg",
        "channel": "Info Channel",
        "uploader": "Info Uploader",
        "duration": 300,
        "description": "Video description here",
    }


def test_is_configured_always_true(youtube_source_no_api):
    """is_configured() returns True even without an API key."""
    assert youtube_source_no_api.is_configured() is True


def test_search_ytdlp_success(youtube_source_no_api):
    """Test yt-dlp search returns formatted results."""
    mock_ydl_instance = MagicMock()
    mock_ydl_instance.extract_info.return_value = _make_ytdlp_search_result()

    with patch("kbox.youtube.yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl_cls.return_value.__enter__ = Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = Mock(return_value=False)

        results = youtube_source_no_api.search("test query")

    assert len(results) == 2
    assert results[0]["id"] == "ytdlp_vid1"
    assert results[0]["title"] == "Karaoke Song 1"
    assert results[0]["channel"] == "Karaoke Channel"
    assert results[0]["duration_seconds"] == 195

    # Second result has empty channel, should fall back to uploader
    assert results[1]["channel"] == "Some Uploader"
    assert results[1]["duration_seconds"] == 240

    # Verify ytsearch URL was called with karaoke appended
    call_args = mock_ydl_instance.extract_info.call_args
    assert "ytsearch" in call_args[0][0]
    assert "karaoke" in call_args[0][0]


def test_search_ytdlp_no_results(youtube_source_no_api):
    """Test yt-dlp search with no results."""
    mock_ydl_instance = MagicMock()
    mock_ydl_instance.extract_info.return_value = {"entries": []}

    with patch("kbox.youtube.yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl_cls.return_value.__enter__ = Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = Mock(return_value=False)

        results = youtube_source_no_api.search("nonexistent")

    assert len(results) == 0


def test_search_ytdlp_error_returns_empty(youtube_source_no_api):
    """Test yt-dlp search error returns empty list."""
    mock_ydl_instance = MagicMock()
    mock_ydl_instance.extract_info.side_effect = Exception("Network error")

    with patch("kbox.youtube.yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl_cls.return_value.__enter__ = Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = Mock(return_value=False)

        results = youtube_source_no_api.search("test")

    assert results == []


def test_get_video_info_ytdlp_success(youtube_source_no_api):
    """Test yt-dlp get_video_info returns formatted result."""
    mock_ydl_instance = MagicMock()
    mock_ydl_instance.extract_info.return_value = _make_ytdlp_video_info()

    with patch("kbox.youtube.yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl_cls.return_value.__enter__ = Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = Mock(return_value=False)

        info = youtube_source_no_api.get_video_info("info_vid1")

    assert info is not None
    assert info["id"] == "info_vid1"
    assert info["title"] == "Info Song"
    assert info["channel"] == "Info Channel"
    assert info["duration_seconds"] == 300


def test_get_video_info_ytdlp_error_returns_none(youtube_source_no_api):
    """Test yt-dlp get_video_info error returns None."""
    mock_ydl_instance = MagicMock()
    mock_ydl_instance.extract_info.side_effect = Exception("Video unavailable")

    with patch("kbox.youtube.yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl_cls.return_value.__enter__ = Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = Mock(return_value=False)

        info = youtube_source_no_api.get_video_info("bad_vid")

    assert info is None


# =========================================================================
# Fallback tests (API configured but fails -> yt-dlp used)
# =========================================================================


def test_search_fallback_on_api_error(youtube_source):
    """When API search raises, search() falls back to yt-dlp."""
    # Make API search raise
    youtube_source._youtube.search.return_value.list.return_value.execute.side_effect = Exception(
        "Quota exceeded"
    )

    # Mock yt-dlp fallback
    mock_ydl_instance = MagicMock()
    mock_ydl_instance.extract_info.return_value = _make_ytdlp_search_result()

    with patch("kbox.youtube.yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl_cls.return_value.__enter__ = Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = Mock(return_value=False)

        results = youtube_source.search("test query")

    # Should get yt-dlp results, not empty list
    assert len(results) == 2
    assert results[0]["id"] == "ytdlp_vid1"


def test_get_video_info_fallback_on_api_error(youtube_source):
    """When API get_video_info raises, falls back to yt-dlp."""
    # Make API raise
    youtube_source._youtube.videos.return_value.list.return_value.execute.side_effect = Exception(
        "API error"
    )

    # Mock yt-dlp fallback
    mock_ydl_instance = MagicMock()
    mock_ydl_instance.extract_info.return_value = _make_ytdlp_video_info()

    with patch("kbox.youtube.yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl_cls.return_value.__enter__ = Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = Mock(return_value=False)

        info = youtube_source.get_video_info("info_vid1")

    assert info is not None
    assert info["id"] == "info_vid1"
    assert info["title"] == "Info Song"


def test_search_uses_api_when_configured(youtube_source):
    """When API is configured and works, search() uses API (not yt-dlp)."""
    # Setup working API mocks
    mock_search_response = {"items": [{"id": {"videoId": "api_vid1"}, "snippet": {}}]}
    mock_videos_response = {
        "items": [
            {
                "id": "api_vid1",
                "snippet": {
                    "title": "API Result",
                    "thumbnails": {"default": {"url": "http://api_thumb.jpg"}},
                    "channelTitle": "API Channel",
                    "description": "API desc",
                },
                "contentDetails": {"duration": "PT3M0S"},
            }
        ]
    }

    mock_search = Mock()
    mock_search.list.return_value.execute.return_value = mock_search_response
    mock_videos = Mock()
    mock_videos.list.return_value.execute.return_value = mock_videos_response

    youtube_source._youtube.search.return_value = mock_search
    youtube_source._youtube.videos.return_value = mock_videos

    with patch("kbox.youtube.yt_dlp.YoutubeDL") as mock_ydl_cls:
        results = youtube_source.search("test")

        # yt-dlp should NOT have been called
        mock_ydl_cls.assert_not_called()

    assert len(results) == 1
    assert results[0]["id"] == "api_vid1"
    assert results[0]["title"] == "API Result"
