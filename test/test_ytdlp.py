"""
Unit tests for YtDlpClient.
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import yt_dlp

from kbox.ytdlp import YtDlpClient, _friendly_download_error, download_format


def _make_config():
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "video_max_resolution": "480",
    }.get(key, default)
    config.get_int.side_effect = lambda key, default=None: {
        "video_max_resolution": 480,
    }.get(key, default)
    return config


def _make_search_result():
    return {
        "entries": [
            {
                "id": "vid1",
                "title": "Karaoke Song 1",
                "thumbnail": "http://thumb1.jpg",
                "channel": "Karaoke Channel",
                "uploader": "Karaoke Uploader",
                "duration": 195,
                "description": "A great karaoke track",
            },
            {
                "id": "vid2",
                "title": "Karaoke Song 2",
                "thumbnail": "http://thumb2.jpg",
                "channel": "",
                "uploader": "Some Uploader",
                "duration": 240,
                "description": "Another karaoke track",
            },
        ]
    }


def _make_video_info():
    return {
        "id": "info_vid1",
        "title": "Info Song",
        "thumbnail": "http://info_thumb.jpg",
        "channel": "Info Channel",
        "uploader": "Info Uploader",
        "duration": 300,
        "description": "Video description here",
    }


def _patch_ytdlp(mock_ydl_instance):
    """Context manager that patches yt_dlp.YoutubeDL to return mock_ydl_instance."""
    p = patch("kbox.ytdlp.yt_dlp.YoutubeDL")
    mock_cls = p.start()
    mock_cls.return_value.__enter__ = Mock(return_value=mock_ydl_instance)
    mock_cls.return_value.__exit__ = Mock(return_value=False)
    return p


# =========================================================================
# Search
# =========================================================================


def test_search_success():
    client = YtDlpClient(_make_config())
    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = _make_search_result()

    p = _patch_ytdlp(mock_ydl)
    try:
        results = client.search("test query")
    finally:
        p.stop()

    assert len(results) == 2
    assert results[0]["id"] == "vid1"
    assert results[0]["title"] == "Karaoke Song 1"
    assert results[0]["channel"] == "Karaoke Channel"
    assert results[0]["duration_seconds"] == 195
    assert results[1]["channel"] == "Some Uploader"

    call_args = mock_ydl.extract_info.call_args
    assert "ytsearch" in call_args[0][0]
    assert "karaoke" in call_args[0][0]


def test_search_no_results():
    client = YtDlpClient(_make_config())
    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = {"entries": []}

    p = _patch_ytdlp(mock_ydl)
    try:
        results = client.search("nonexistent")
    finally:
        p.stop()

    assert len(results) == 0


def test_search_error_returns_empty():
    client = YtDlpClient(_make_config())
    mock_ydl = MagicMock()
    mock_ydl.extract_info.side_effect = Exception("Network error")

    p = _patch_ytdlp(mock_ydl)
    try:
        results = client.search("test")
    finally:
        p.stop()

    assert results == []


# =========================================================================
# Video info
# =========================================================================


def test_get_video_info_success():
    client = YtDlpClient(_make_config())
    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = _make_video_info()

    p = _patch_ytdlp(mock_ydl)
    try:
        info = client.get_video_info("info_vid1")
    finally:
        p.stop()

    assert info is not None
    assert info["id"] == "info_vid1"
    assert info["title"] == "Info Song"
    assert info["channel"] == "Info Channel"
    assert info["duration_seconds"] == 300


def test_get_video_info_error_returns_none():
    client = YtDlpClient(_make_config())
    mock_ydl = MagicMock()
    mock_ydl.extract_info.side_effect = Exception("Video unavailable")

    p = _patch_ytdlp(mock_ydl)
    try:
        info = client.get_video_info("bad_vid")
    finally:
        p.stop()

    assert info is None


# =========================================================================
# Rate limiting
# =========================================================================


def test_rate_limit_enforces_minimum_interval():
    client = YtDlpClient(_make_config())
    client._min_interval = 0.2

    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = _make_search_result()

    p = _patch_ytdlp(mock_ydl)
    try:
        start = time.monotonic()
        client.search("query1")
        client.search("query2")
        elapsed = time.monotonic() - start
    finally:
        p.stop()

    assert elapsed >= 0.15


# =========================================================================
# Provide (download)
# =========================================================================


def test_provide_success(tmp_path):
    client = YtDlpClient(_make_config())
    mock_ydl = MagicMock()
    video_file = tmp_path / "video.mp4"
    video_file.touch()
    mock_ydl.extract_info.return_value = {"id": "test_vid", "title": "Test"}
    mock_ydl.prepare_filename.return_value = str(video_file)

    p = _patch_ytdlp(mock_ydl)
    try:
        result = client.provide("test_vid", tmp_path)
    finally:
        p.stop()

    assert result == video_file
    mock_ydl.extract_info.assert_called_once()


def test_provide_uses_default_clients_and_capped_format(tmp_path):
    client = YtDlpClient(_make_config())
    mock_ydl = MagicMock()
    video_file = tmp_path / "video.mp4"
    video_file.touch()
    mock_ydl.extract_info.return_value = {"id": "test_vid"}
    mock_ydl.prepare_filename.return_value = str(video_file)

    with patch("kbox.ytdlp.yt_dlp.YoutubeDL") as mock_cls:
        mock_cls.return_value.__enter__.return_value = mock_ydl
        client.provide("test_vid", tmp_path)

    opts = mock_cls.call_args.args[0]
    assert "extractor_args" not in opts
    assert opts["format"] == download_format(480)


def _fmt(format_id, height, vcodec, acodec, ext):
    return {
        "format_id": format_id,
        "height": height,
        "width": height * 16 // 9 if height else None,
        "vcodec": vcodec,
        "acodec": acodec,
        "ext": ext,
        "url": f"https://example.invalid/{format_id}",
        "protocol": "https",
    }


# Shaped like what YouTube offers a 480p-capable karaoke video: separate
# video and audio streams in AV1, VP9 and H.264, plus AAC and Opus audio.
_ADAPTIVE_FORMATS = [
    _fmt("397", 480, "av01.0.04M.08", "none", "mp4"),
    _fmt("136", 720, "avc1.4d401f", "none", "mp4"),
    _fmt("140", None, "none", "mp4a.40.2", "m4a"),
    _fmt("244", 480, "vp9", "none", "webm"),
    _fmt("134", 360, "avc1.4D401E", "none", "mp4"),
    _fmt("135", 480, "avc1.4D401E", "none", "mp4"),
    _fmt("251", None, "none", "opus", "webm"),
]


def _select(formats, max_height=480):
    """Run yt-dlp's real format selection on a fixed list, without network."""
    with yt_dlp.YoutubeDL({"quiet": True, "format": download_format(max_height)}) as ydl:
        info = ydl.process_ie_result(
            {
                "id": "vid",
                "title": "Song",
                "formats": formats,
                "extractor": "youtube",
                "extractor_key": "Youtube",
                "webpage_url": "https://www.youtube.com/watch?v=vid",
            },
            download=False,
        )
    return info["format_id"], info["ext"]


def test_download_format_prefers_h264_and_aac():
    assert _select(_ADAPTIVE_FORMATS) == ("135+140", "mp4")


def test_download_format_respects_max_height():
    assert _select(_ADAPTIVE_FORMATS, max_height=360) == ("134+140", "mp4")
    assert _select(_ADAPTIVE_FORMATS, max_height=720) == ("136+140", "mp4")


def test_download_format_avoids_av1_without_h264():
    no_h264 = [f for f in _ADAPTIVE_FORMATS if not f["vcodec"].startswith("avc1")]
    assert _select(no_h264) == ("244+251", "webm")


def test_download_format_takes_progressive_h264():
    progressive = [_fmt("18", 360, "avc1.42001E", "mp4a.40.2", "mp4")]
    assert _select(progressive) == ("18", "mp4")


def test_provide_raises_on_failure():
    client = YtDlpClient(_make_config())
    mock_ydl = MagicMock()
    mock_ydl.extract_info.side_effect = Exception("403 Forbidden")

    p = _patch_ytdlp(mock_ydl)
    try:
        with pytest.raises(RuntimeError, match="403"):
            client.provide("bad_vid", Path("/tmp/test_provide"))
    finally:
        p.stop()


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "ERROR: unable to download video data: HTTP Error 403: Forbidden",
            "YouTube blocked the download (403 Forbidden). Try updating yt-dlp.",
        ),
        (
            "ERROR: [youtube] xiAoME9BIvE: This video is unavailable",
            "Video is unavailable or has been removed",
        ),
        (
            "ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader",
            "Video is unavailable or has been removed",
        ),
        (
            "ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video",
            "Video is private",
        ),
        (
            "ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies-from-browser",
            "YouTube asked for a bot check. Try again in a few minutes.",
        ),
        (
            "ERROR: [youtube] abc: This content isn't available, try again later. "
            "The current session has been rate-limited by YouTube for up to an hour.",
            "YouTube is limiting downloads right now. Try again in a few minutes.",
        ),
        (
            "ERROR: [youtube] abc: Sign in to confirm your age. "
            "This video may be inappropriate for some users.",
            "Video is age-restricted",
        ),
        (
            "ERROR: [youtube] abc: The uploader has not made this video available in your country",
            "Video is not available in this country",
        ),
        (
            "ERROR: [youtube] xiAoME9BIvE: Requested format is not available. "
            "Use --list-formats for a list of available formats",
            "YouTube didn't offer a playable version this time. Try again.",
        ),
        ("something nobody planned for", "something nobody planned for"),
    ],
)
def test_friendly_download_error(raw, expected):
    assert _friendly_download_error(raw) == expected


def test_friendly_download_error_ignores_403_inside_video_id():
    raw = "ERROR: [youtube] ab403cdEFgh: This video is unavailable"
    assert _friendly_download_error(raw) == "Video is unavailable or has been removed"


# =========================================================================
# Rate limiting
# =========================================================================


def test_rate_limit_no_wait_when_interval_passed():
    client = YtDlpClient(_make_config())
    client._min_interval = 0.05

    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = _make_search_result()

    p = _patch_ytdlp(mock_ydl)
    try:
        client.search("query1")
        time.sleep(0.1)

        start = time.monotonic()
        client.search("query2")
        elapsed = time.monotonic() - start
    finally:
        p.stop()

    assert elapsed < 0.05
