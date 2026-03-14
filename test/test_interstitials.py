"""Tests for the interstitial screen generation module."""

import os
import shutil
import tempfile

import pytest

from kbox.interstitials import PIL_AVAILABLE, InterstitialGenerator


@pytest.fixture
def cache_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def generator(cache_dir):
    return InterstitialGenerator(cache_dir=cache_dir)


@pytest.mark.skipif(not PIL_AVAILABLE, reason="PIL not available")
class TestIdleScreen:
    def test_returns_valid_png_path(self, generator, cache_dir):
        path = generator.generate_idle_screen()
        assert path.endswith(".png")
        assert os.path.exists(path)
        assert path.startswith(cache_dir)

    def test_image_dimensions(self, generator):
        from PIL import Image

        path = generator.generate_idle_screen()
        img = Image.open(path)
        assert img.size == (1280, 720)

    def test_custom_dimensions(self, cache_dir):
        gen = InterstitialGenerator(width=800, height=600, cache_dir=cache_dir)
        path = gen.generate_idle_screen()
        from PIL import Image

        img = Image.open(path)
        assert img.size == (800, 600)


@pytest.mark.skipif(not PIL_AVAILABLE, reason="PIL not available")
class TestTransitionScreen:
    def test_returns_valid_png(self, generator):
        path = generator.generate_transition_screen("Alice")
        assert os.path.exists(path)
        assert path.endswith(".png")

    def test_with_song_title(self, generator):
        path = generator.generate_transition_screen("Alice", song_title="Bohemian Rhapsody")
        assert os.path.exists(path)

    def test_with_artist(self, generator):
        path = generator.generate_transition_screen("Alice", artist="Queen")
        assert os.path.exists(path)

    def test_with_all_fields(self, generator):
        path = generator.generate_transition_screen(
            "Alice",
            song_title="Bohemian Rhapsody",
            artist="Queen",
            web_url="http://example.com",
        )
        assert os.path.exists(path)

    def test_long_title_truncated(self, generator):
        # Title > 50 chars should be truncated (the image should still generate)
        long_title = "A" * 100
        path = generator.generate_transition_screen("Alice", song_title=long_title)
        assert os.path.exists(path)

    def test_long_artist_truncated(self, generator):
        long_artist = "B" * 100
        path = generator.generate_transition_screen("Alice", artist=long_artist)
        assert os.path.exists(path)


@pytest.mark.skipif(not PIL_AVAILABLE, reason="PIL not available")
class TestEndOfQueueScreen:
    def test_returns_valid_png(self, generator):
        path = generator.generate_end_of_queue_screen()
        assert os.path.exists(path)
        assert path.endswith(".png")

    def test_custom_message(self, generator):
        path = generator.generate_end_of_queue_screen(message="Party's over!")
        assert os.path.exists(path)


@pytest.mark.skipif(not PIL_AVAILABLE, reason="PIL not available")
class TestFontCaching:
    def test_same_font_is_cached(self, generator):
        font1 = generator._get_font(48, bold=False)
        font2 = generator._get_font(48, bold=False)
        assert font1 is font2

    def test_different_sizes_are_separate(self, generator):
        font1 = generator._get_font(24)
        font2 = generator._get_font(48)
        assert font1 is not font2

    def test_bold_vs_regular_are_separate(self, generator):
        regular = generator._get_font(48, bold=False)
        bold = generator._get_font(48, bold=True)
        # They may or may not be the same object depending on available fonts,
        # but they should be in the cache as separate entries
        assert (48, False) in generator._font_cache
        assert (48, True) in generator._font_cache


class TestFallback:
    def test_fallback_returns_empty_string(self, generator):
        result = generator._generate_fallback_image("idle")
        assert result == ""
