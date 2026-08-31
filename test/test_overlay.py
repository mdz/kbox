"""Tests for the overlay module."""

import os
import shutil
import tempfile
from unittest.mock import patch

from kbox.overlay import format_notification, generate_qr_code


class TestGenerateQrCode:
    def test_returns_valid_png_path(self):
        output_dir = tempfile.mkdtemp()
        try:
            path = generate_qr_code("https://example.com", output_dir=output_dir)
            assert path is not None
            assert os.path.exists(path)
            assert path.endswith(".png")
        finally:
            shutil.rmtree(output_dir)

    def test_respects_output_dir(self):
        output_dir = tempfile.mkdtemp()
        try:
            path = generate_qr_code("https://example.com", output_dir=output_dir)
            assert path is not None
            assert path.startswith(output_dir)
            assert os.path.basename(path) == "qr_code.png"
        finally:
            shutil.rmtree(output_dir)

    def test_creates_output_dir_if_missing(self):
        output_dir = os.path.join(tempfile.mkdtemp(), "nested", "dir")
        try:
            path = generate_qr_code("https://example.com", output_dir=output_dir)
            assert path is not None
            assert os.path.isdir(output_dir)
        finally:
            shutil.rmtree(os.path.dirname(os.path.dirname(output_dir)))

    def test_returns_none_when_import_fails(self):
        output_dir = tempfile.mkdtemp()
        try:
            with patch("builtins.__import__", side_effect=ImportError("no qrcode")):
                result = generate_qr_code("https://example.com", output_dir=output_dir)
                assert result is None
        finally:
            shutil.rmtree(output_dir)


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
