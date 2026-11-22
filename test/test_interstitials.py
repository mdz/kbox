"""Tests for the interstitial screen generation module."""

import os
import shutil
import tempfile

import pytest

from kbox.interstitials import PIL_AVAILABLE, InterstitialGenerator, QueueSummary
from kbox.models import QueueItem, SongMetadata, SongSettings


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


def _item(id, user_id, user_name, duration_seconds=180):
    return QueueItem(
        id=id,
        position=id,
        user_id=user_id,
        user_name=user_name,
        video_id=f"youtube:{id}",
        metadata=SongMetadata(title=f"Song {id}", duration_seconds=duration_seconds),
        settings=SongSettings(),
    )


class TestQueueSummary:
    def test_singers_listed_once_in_queue_order(self):
        summary = QueueSummary.from_items(
            [_item(1, "u-bob", "Bob"), _item(2, "u-alice", "Alice"), _item(3, "u-bob", "Bob")]
        )
        assert summary.singer_names == ("Bob", "Alice")
        assert summary.song_count == 3

    def test_singers_with_the_same_name_are_both_listed(self):
        summary = QueueSummary.from_items([_item(1, "u-1", "Mike"), _item(2, "u-2", "Mike")])
        assert summary.singer_names == ("Mike", "Mike")

    def test_total_ignores_unknown_durations(self):
        summary = QueueSummary.from_items(
            [_item(1, "a", "A", duration_seconds=200), _item(2, "b", "B", duration_seconds=None)]
        )
        assert summary.total_seconds == 200

    def test_empty(self):
        assert QueueSummary.from_items([]) == QueueSummary()

    def test_stats_text_plural(self):
        summary = QueueSummary(song_count=5, total_seconds=18 * 60, singer_names=("A", "B", "C"))
        assert summary.stats_text() == "3 singers · 5 songs · about 18 min"

    def test_stats_text_singular(self):
        summary = QueueSummary(song_count=1, total_seconds=200, singer_names=("A",))
        assert summary.stats_text() == "1 singer · 1 song · about 3 min"

    def test_stats_text_omits_unknown_time(self):
        summary = QueueSummary(song_count=2, total_seconds=0, singer_names=("A",))
        assert summary.stats_text() == "1 singer · 2 songs"

    def test_stats_text_hours(self):
        assert "about 1 hr 7 min" in QueueSummary(3, 67 * 60, ("A",)).stats_text()
        assert "about 2 hr" in QueueSummary(3, 120 * 60, ("A",)).stats_text()

    def test_stats_text_short_queue_rounds_up_to_a_minute(self):
        assert "about 1 min" in QueueSummary(1, 10, ("A",)).stats_text()


class TestIdleScreenQueue:
    def test_renders_with_queue_summary(self, generator, cache_dir):
        summary = QueueSummary(song_count=3, total_seconds=600, singer_names=("Alice", "Bob"))
        path = generator.generate_idle_screen(party_theme="80s", queue=summary)
        assert os.path.exists(path)
        assert os.listdir(cache_dir) == ["interstitial_idle.png"]

    def test_names_that_fit_are_all_shown(self, generator):
        _, draw = generator._create_base_image()
        font = generator._get_font(40, bold=True)
        assert generator._fit_names(draw, ("Alice", "Bob"), font, 1000) == "Alice · Bob"

    def test_names_that_do_not_fit_collapse_into_more(self, generator):
        _, draw = generator._create_base_image()
        font = generator._get_font(40, bold=True)
        names = tuple(f"Singer{i}" for i in range(20))
        text = generator._fit_names(draw, names, font, 600)
        assert text.startswith("Singer0 · ")
        assert text.endswith(" more")
        bbox = draw.textbbox((0, 0), text, font=font)
        assert bbox[2] - bbox[0] <= 600

    def test_one_name_is_always_shown(self, generator):
        _, draw = generator._create_base_image()
        font = generator._get_font(40, bold=True)
        assert generator._fit_names(draw, ("A" * 100, "Bob"), font, 100) == "A" * 100 + " · +1 more"


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
