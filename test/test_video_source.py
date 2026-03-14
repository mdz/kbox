"""
Unit tests for YouTubeSource (composition of YouTubeAPI + YtDlpClient).
"""

from unittest.mock import Mock

from kbox.video_source import YouTubeSource


def _make_api(available=True):
    api = Mock()
    api.is_available.return_value = available
    return api


def _make_ytdlp():
    return Mock()


# =========================================================================
# source_id / is_configured
# =========================================================================


def test_source_id():
    source = YouTubeSource(_make_api(), _make_ytdlp())
    assert source.source_id == "youtube"


def test_is_configured_always_true():
    source = YouTubeSource(_make_api(available=False), _make_ytdlp())
    assert source.is_configured() is True


# =========================================================================
# Search: API first, yt-dlp fallback
# =========================================================================


def test_search_uses_api_when_available():
    api = _make_api()
    api.search.return_value = [{"id": "api1", "title": "API Result"}]
    ytdlp = _make_ytdlp()

    source = YouTubeSource(api, ytdlp)
    results = source.search("test")

    assert len(results) == 1
    assert results[0]["id"] == "api1"
    ytdlp.search.assert_not_called()


def test_search_falls_back_to_ytdlp_on_api_error():
    api = _make_api()
    api.search.side_effect = Exception("API error")
    ytdlp = _make_ytdlp()
    ytdlp.search.return_value = [{"id": "yt1", "title": "yt-dlp Result"}]

    source = YouTubeSource(api, ytdlp)
    results = source.search("test")

    assert len(results) == 1
    assert results[0]["id"] == "yt1"


def test_search_uses_ytdlp_when_api_unavailable():
    api = _make_api(available=False)
    ytdlp = _make_ytdlp()
    ytdlp.search.return_value = [{"id": "yt1", "title": "yt-dlp Result"}]

    source = YouTubeSource(api, ytdlp)
    results = source.search("test")

    assert len(results) == 1
    assert results[0]["id"] == "yt1"
    api.search.assert_not_called()


def test_search_both_fail_returns_empty():
    api = _make_api()
    api.search.side_effect = Exception("API error")
    ytdlp = _make_ytdlp()
    ytdlp.search.side_effect = Exception("yt-dlp error")

    source = YouTubeSource(api, ytdlp)
    results = source.search("test")

    assert results == []


# =========================================================================
# get_video_info: API first, yt-dlp fallback
# =========================================================================


def test_get_video_info_uses_api_when_available():
    api = _make_api()
    api.get_video_info.return_value = {"id": "v1", "title": "API Song"}
    ytdlp = _make_ytdlp()

    source = YouTubeSource(api, ytdlp)
    info = source.get_video_info("v1")

    assert info["id"] == "v1"
    ytdlp.get_video_info.assert_not_called()


def test_get_video_info_falls_back_to_ytdlp_on_api_error():
    api = _make_api()
    api.get_video_info.side_effect = Exception("API error")
    ytdlp = _make_ytdlp()
    ytdlp.get_video_info.return_value = {"id": "v1", "title": "yt-dlp Song"}

    source = YouTubeSource(api, ytdlp)
    info = source.get_video_info("v1")

    assert info["title"] == "yt-dlp Song"


def test_get_video_info_falls_back_when_api_returns_none():
    api = _make_api()
    api.get_video_info.return_value = None
    ytdlp = _make_ytdlp()
    ytdlp.get_video_info.return_value = {"id": "v1", "title": "yt-dlp Song"}

    source = YouTubeSource(api, ytdlp)
    info = source.get_video_info("v1")

    assert info["title"] == "yt-dlp Song"


def test_get_video_info_uses_ytdlp_when_api_unavailable():
    api = _make_api(available=False)
    ytdlp = _make_ytdlp()
    ytdlp.get_video_info.return_value = {"id": "v1", "title": "yt-dlp Song"}

    source = YouTubeSource(api, ytdlp)
    info = source.get_video_info("v1")

    assert info["title"] == "yt-dlp Song"
    api.get_video_info.assert_not_called()


def test_get_video_info_both_fail_returns_none():
    api = _make_api()
    api.get_video_info.side_effect = Exception("API error")
    ytdlp = _make_ytdlp()
    ytdlp.get_video_info.side_effect = Exception("yt-dlp error")

    source = YouTubeSource(api, ytdlp)
    assert source.get_video_info("v1") is None
