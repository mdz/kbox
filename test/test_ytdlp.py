"""
Unit tests for YtDlpClient.
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from kbox.ytdlp import YtDlpClient


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
