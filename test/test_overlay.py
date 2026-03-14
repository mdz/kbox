"""Tests for the overlay module."""

import os
import shutil
import tempfile
from unittest.mock import patch

from kbox.overlay import format_notification, generate_qr_code


class TestGenerateQrCode:
    def test_returns_valid_png_path(self):
        path = generate_qr_code("https://example.com")
        assert path is not None
        assert os.path.exists(path)
        assert path.endswith(".png")

    def test_respects_cache_dir(self):
        cache_dir = tempfile.mkdtemp()
        try:
            path = generate_qr_code("https://example.com", cache_dir=cache_dir)
            assert path is not None
            assert path.startswith(cache_dir)
            assert os.path.basename(path) == "qr_code.png"
        finally:
            shutil.rmtree(cache_dir)

    def test_creates_cache_dir_if_missing(self):
        cache_dir = os.path.join(tempfile.mkdtemp(), "nested", "dir")
        try:
            path = generate_qr_code("https://example.com", cache_dir=cache_dir)
            assert path is not None
            assert os.path.isdir(cache_dir)
        finally:
            shutil.rmtree(os.path.dirname(os.path.dirname(cache_dir)))

    def test_default_uses_tempdir(self):
        path = generate_qr_code("https://example.com")
        assert path is not None
        assert "kbox_qr_code.png" in path

    def test_returns_none_when_import_fails(self):
        with patch("builtins.__import__", side_effect=ImportError("no qrcode")):
            result = generate_qr_code("https://example.com")
            assert result is None


class TestFormatNotification:
    def test_short_text_unchanged(self):
        assert format_notification("Hello") == "Hello"

    def test_exact_max_length_unchanged(self):
        text = "x" * 50
        assert format_notification(text) == text

    def test_long_text_truncated(self):
        text = "x" * 60
        result = format_notification(text)
        assert len(result) == 50
        assert result.endswith("...")

    def test_custom_max_length(self):
        text = "Hello World!"
        result = format_notification(text, max_length=8)
        assert result == "Hello..."
        assert len(result) == 8

    def test_empty_string(self):
        assert format_notification("") == ""
