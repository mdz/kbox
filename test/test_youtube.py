"""
Unit tests for YouTubeAPI (YouTube Data API v3 client).
"""

import threading
from unittest.mock import Mock, patch

import pytest

from kbox.youtube import YouTubeAPI


@pytest.fixture
def mock_config_manager():
    """ConfigManager with an API key."""
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "youtube_api_key": "fake_api_key",
    }.get(key, default)
    return config


@pytest.fixture
def mock_config_no_key():
    """ConfigManager without an API key."""
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "youtube_api_key": None,
    }.get(key, default)
    return config


@pytest.fixture
def mock_client():
    """The Google API client that the patched build() hands out."""
    return Mock()


@pytest.fixture
def youtube_api(mock_config_manager, mock_client):
    """YouTubeAPI instance whose build() returns mock_client."""
    with patch("kbox.youtube.build", return_value=mock_client):
        yield YouTubeAPI(mock_config_manager)


# =========================================================================
# Availability
# =========================================================================


def test_is_available_with_key(youtube_api):
    assert youtube_api.is_available() is True


def test_is_available_without_key(mock_config_no_key):
    api = YouTubeAPI(mock_config_no_key)
    assert api.is_available() is False


# =========================================================================
# Per-thread clients
# =========================================================================


def test_client_is_reused_within_a_thread(mock_config_manager):
    with patch("kbox.youtube.build", side_effect=lambda *a, **kw: Mock()) as mock_build:
        api = YouTubeAPI(mock_config_manager)
        first = api._get_client()
        assert api._get_client() is first
    assert mock_build.call_count == 1


def test_each_thread_gets_its_own_client(mock_config_manager):
    """Concurrent searches must not share one client: its httplib2 connection is not thread-safe."""
    with patch("kbox.youtube.build", side_effect=lambda *a, **kw: Mock()) as mock_build:
        api = YouTubeAPI(mock_config_manager)
        main_client = api._get_client()

        other: list = []
        thread = threading.Thread(target=lambda: other.append(api._get_client()))
        thread.start()
        thread.join()

    assert other[0] is not None
    assert other[0] is not main_client
    assert mock_build.call_count == 2


def test_client_is_rebuilt_when_api_key_changes():
    keys = iter(["key_one", "key_one", "key_two"])
    config = Mock()
    config.get.side_effect = lambda key, default=None: next(keys)

    with patch("kbox.youtube.build", side_effect=lambda *a, **kw: Mock()) as mock_build:
        api = YouTubeAPI(config)
        first = api._get_client()
        assert api._get_client() is first
        second = api._get_client()

    assert second is not first
    assert [c.kwargs["developerKey"] for c in mock_build.call_args_list] == ["key_one", "key_two"]


# =========================================================================
# Search
# =========================================================================


def test_search_success(youtube_api, mock_client):
    mock_search_response = {
        "items": [
            {"id": {"videoId": "vid1"}, "snippet": {}},
            {"id": {"videoId": "vid2"}, "snippet": {}},
        ]
    }
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

    mock_search = Mock()
    mock_search.list.return_value.execute.return_value = mock_search_response
    mock_videos = Mock()
    mock_videos.list.return_value.execute.return_value = mock_videos_response

    mock_client.search.return_value = mock_search
    mock_client.videos.return_value = mock_videos

    results = youtube_api.search("test query")

    call_args = mock_client.search.return_value.list.call_args
    assert "karaoke" in call_args[1]["q"].lower()

    assert len(results) == 2
    assert results[0]["id"] == "vid1"
    assert results[0]["title"] == "Test Song 1"
    assert results[0]["duration_seconds"] == 210
    assert results[1]["duration_seconds"] == 255


def test_search_no_results(youtube_api, mock_client):
    mock_search = Mock()
    mock_search.list.return_value.execute.return_value = {"items": []}
    mock_client.search.return_value = mock_search

    results = youtube_api.search("nonexistent")
    assert len(results) == 0


def test_search_raises_without_key(mock_config_no_key):
    api = YouTubeAPI(mock_config_no_key)
    with pytest.raises(RuntimeError, match="not configured"):
        api.search("test")


# =========================================================================
# Video info
# =========================================================================


def test_get_video_info_success(youtube_api, mock_client):
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
    mock_client.videos.return_value = mock_videos

    info = youtube_api.get_video_info("vid1")

    assert info is not None
    assert info["id"] == "vid1"
    assert info["title"] == "Test Song"
    assert info["duration_seconds"] == 210


def test_get_video_info_not_found(youtube_api, mock_client):
    mock_videos = Mock()
    mock_videos.list.return_value.execute.return_value = {"items": []}
    mock_client.videos.return_value = mock_videos

    assert youtube_api.get_video_info("nonexistent") is None


def test_get_video_info_raises_without_key(mock_config_no_key):
    api = YouTubeAPI(mock_config_no_key)
    with pytest.raises(RuntimeError, match="not configured"):
        api.get_video_info("vid1")


# =========================================================================
# Duration parsing
# =========================================================================


def test_parse_duration():
    parse = YouTubeAPI._parse_duration
    assert parse("PT3M30S") == 210
    assert parse("PT1H5M30S") == 3930
    assert parse("PT45S") == 45
    assert parse("PT2H") == 7200
    assert parse("") is None
    assert parse("invalid") is None
