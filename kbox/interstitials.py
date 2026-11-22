"""
Interstitial screen generation for kbox.

Generates images for display between songs, during idle, and at end of queue.
"""

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

from .models import QueueItem

logger = logging.getLogger(__name__)

# Try to import PIL, but gracefully handle if not available
try:
    from PIL import Image, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("PIL/Pillow not installed, interstitials will be text-only")


# kbox brand colors (see docs/branding/)
BACKGROUND_COLOR = (24, 15, 38)  # Stage - dark violet-black
PRIMARY_TEXT_COLOR = (255, 255, 255)  # White
ACCENT_COLOR = (176, 107, 255)  # Marquee violet
WORDMARK_PINK = (255, 63, 164)  # Hot mic pink, "box" in the kbox wordmark
SECONDARY_TEXT_COLOR = (150, 150, 160)  # Muted gray

WORDMARK_FONT_PATH = Path(__file__).resolve().parent / "assets" / "fonts" / "Baloo2-Variable.ttf"


@dataclass(frozen=True)
class QueueSummary:
    """
    What the idle screen says about the queue before playback starts.

    Not the queue itself: just enough to show the room that the party is
    filling up (who is in line, roughly how long it runs) and nudge the
    rest to add their songs. Frozen and comparable, so the caller can
    redraw only when it changes.
    """

    song_count: int = 0
    total_seconds: int = 0
    singer_names: Tuple[str, ...] = ()

    @classmethod
    def from_items(cls, items: Sequence[QueueItem]) -> "QueueSummary":
        """Summarize the songs waiting to be sung, in queue order."""
        names: list[str] = []
        seen_users: set[str] = set()
        for item in items:
            if item.user_id not in seen_users:
                seen_users.add(item.user_id)
                names.append(item.user_name)
        return cls(
            song_count=len(items),
            total_seconds=sum(item.metadata.duration_seconds or 0 for item in items),
            singer_names=tuple(names),
        )

    def stats_text(self) -> str:
        """E.g. "3 singers · 5 songs · about 18 min"; songs of unknown length count as 0."""
        singers = len(self.singer_names)
        parts = [
            f"{singers} {'singer' if singers == 1 else 'singers'}",
            f"{self.song_count} {'song' if self.song_count == 1 else 'songs'}",
        ]
        if self.total_seconds > 0:
            parts.append(f"about {_format_minutes(self.total_seconds)}")
        return " · ".join(parts)


def _format_minutes(seconds: int) -> str:
    """Round a duration to whole minutes for display: "18 min", "1 hr 5 min", "2 hr"."""
    minutes = max(1, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours == 0:
        return f"{minutes} min"
    if minutes == 0:
        return f"{hours} hr"
    return f"{hours} hr {minutes} min"


class InterstitialGenerator:
    """Generates interstitial screen images."""

    def __init__(self, width: int = 1280, height: int = 720, cache_dir: Optional[str] = None):
        """
        Initialize the interstitial generator.

        Args:
            width: Output image width in pixels (default: 1280 to match typical video)
            height: Output image height in pixels (default: 720 to match typical video)
            cache_dir: Directory to store generated images (default: temp dir)
        """
        self.width = width
        self.height = height
        self.cache_dir = cache_dir or tempfile.gettempdir()
        self.logger = logging.getLogger(__name__)

        # Ensure cache directory exists
        os.makedirs(self.cache_dir, exist_ok=True)

        # Font paths (will try system fonts)
        self._font_cache: dict[tuple[int, bool], Any] = {}
        self._wordmark_font_cache: dict[int, Any] = {}

    def _get_font(
        self, size: int, bold: bool = False
    ) -> Any:  # Returns FreeTypeFont or Font (default)
        """Get a font at the specified size, with caching."""
        cache_key = (size, bold)
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        # Try to find a good font
        font_names = [
            # macOS fonts
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/SFNSDisplay.ttf",
            "/Library/Fonts/Arial.ttf",
            # Linux fonts
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ]

        if bold:
            font_names = [
                "/System/Library/Fonts/Helvetica.ttc",
                "/Library/Fonts/Arial Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            ] + font_names

        font: Any = None
        for font_path in font_names:
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, size)
                    break
                except Exception:
                    continue

        if font is None:
            # Fall back to default font
            font = ImageFont.load_default()
            self.logger.warning("Could not load system font, using default")

        self._font_cache[cache_key] = font
        return font

    def _get_wordmark_font(self, size: int) -> Any:
        """Get the bold Baloo 2 font used for the kbox wordmark, with caching."""
        if size in self._wordmark_font_cache:
            return self._wordmark_font_cache[size]

        font: Any = None
        if WORDMARK_FONT_PATH.exists():
            try:
                font = ImageFont.truetype(str(WORDMARK_FONT_PATH), size)
                if hasattr(font, "set_variation_by_name"):
                    font.set_variation_by_name("Bold")
            except Exception:
                self.logger.warning("Could not load wordmark font, falling back", exc_info=True)
                font = None

        if font is None:
            font = self._get_font(size, bold=True)

        self._wordmark_font_cache[size] = font
        return font

    def _create_base_image(self) -> Tuple[Any, Any]:  # Returns (Image, ImageDraw)
        """Create a base image with background color."""
        img = Image.new("RGB", (self.width, self.height), BACKGROUND_COLOR)
        draw = ImageDraw.Draw(img)
        return img, draw

    def _center_text(
        self,
        draw: Any,  # ImageDraw.Draw
        text: str,
        y: int,
        font: "ImageFont.FreeTypeFont",
        color: Tuple[int, int, int],
    ) -> None:
        """Draw centered text at the specified y position."""
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        x = (self.width - text_width) // 2
        draw.text((x, y), text, font=font, fill=color)

    def _center_wordmark(self, draw: Any, y: int, font: Any) -> None:
        """Draw the kbox wordmark centered, 'k' in violet and 'box' in pink."""
        k_bbox = draw.textbbox((0, 0), "k", font=font)
        box_bbox = draw.textbbox((0, 0), "box", font=font)
        k_width = k_bbox[2] - k_bbox[0]
        box_width = box_bbox[2] - box_bbox[0]

        start_x = (self.width - (k_width + box_width)) // 2
        draw.text((start_x, y), "k", font=font, fill=ACCENT_COLOR)
        draw.text((start_x + k_width, y), "box", font=font, fill=WORDMARK_PINK)

    def _fit_names(self, draw: Any, names: Sequence[str], font: Any, max_width: int) -> str:
        """
        Join names with " · ", dropping names from the end into "+N more"
        until the line fits in max_width. Always keeps at least one name.
        """
        for shown in range(len(names), 0, -1):
            text = " · ".join(names[:shown])
            hidden = len(names) - shown
            if hidden:
                text += f" · +{hidden} more"
            bbox = draw.textbbox((0, 0), text, font=font)
            if bbox[2] - bbox[0] <= max_width or shown == 1:
                return text
        return ""

    def generate_idle_screen(
        self, party_theme: Optional[str] = None, queue: Optional[QueueSummary] = None
    ) -> str:
        """
        Generate the idle screen (before playback starts).

        Instructs the audience to scan the QR code overlay to pick songs, and
        shows who is already in line, so the room can see the party filling
        up and the rest are nudged to add theirs.

        Args:
            party_theme: Optional party theme to display below the QR instruction.
            queue: Summary of the songs waiting to be sung. None or empty
                invites someone to go first.

        Returns:
            Path to the generated image file
        """
        if not PIL_AVAILABLE:
            return self._generate_fallback_image("idle")

        img, draw = self._create_base_image()

        # Title (kbox wordmark: k in violet, box in pink)
        title_font = self._get_wordmark_font(120)
        self._center_wordmark(draw, self.height // 4 - 40, title_font)

        # Subtitle
        subtitle_font = self._get_font(48)
        self._center_text(
            draw, "Karaoke", self.height // 4 + 100, subtitle_font, SECONDARY_TEXT_COLOR
        )

        # Main instruction - tell users to scan the QR code
        instruction_font = self._get_font(56, bold=True)
        y = self.height // 2 + 10
        self._center_text(
            draw,
            "Scan the QR code to pick a song",
            y,
            instruction_font,
            PRIMARY_TEXT_COLOR,
        )
        y += 80

        if party_theme:
            theme_font = self._get_font(40)
            self._center_text(
                draw,
                f"Tonight's theme: {party_theme}",
                y,
                theme_font,
                ACCENT_COLOR,
            )
            y += 70
        else:
            y += 20

        if queue is None or queue.song_count == 0:
            self._center_text(
                draw, "Be the first to sing!", y, self._get_font(40, bold=True), WORDMARK_PINK
            )
        else:
            self._center_text(draw, queue.stats_text(), y, self._get_font(34), SECONDARY_TEXT_COLOR)
            names_font = self._get_font(40, bold=True)
            names = self._fit_names(
                draw, queue.singer_names, names_font, max_width=int(self.width * 0.85)
            )
            self._center_text(draw, names, y + 50, names_font, WORDMARK_PINK)

        # Note: QR code is handled by the streaming overlay for consistency

        # Save and return path. Written aside and renamed into place: this
        # screen is redrawn as the queue changes, and the display must never
        # read a half-written file.
        output_path = os.path.join(self.cache_dir, "interstitial_idle.png")
        tmp_path = output_path + ".tmp"
        img.save(tmp_path, "PNG")
        os.replace(tmp_path, output_path)
        self.logger.info("Generated idle interstitial: %s", output_path)
        return output_path

    def generate_transition_screen(
        self,
        singer_name: str,
        song_title: Optional[str] = None,
        artist: Optional[str] = None,
        web_url: Optional[str] = None,
    ) -> str:
        """
        Generate the between-songs transition screen.

        Args:
            singer_name: Name of the next singer
            song_title: Optional song title (can be hidden for surprise)
            artist: Optional artist name (from extracted metadata)
            web_url: URL for the web interface (for QR code)

        Returns:
            Path to the generated image file
        """
        if not PIL_AVAILABLE:
            return self._generate_fallback_image("transition")

        img, draw = self._create_base_image()

        # kbox wordmark, small, top of screen
        self._center_wordmark(draw, 50, self._get_wordmark_font(56))

        # "Up Next" label
        label_font = self._get_font(48)
        self._center_text(draw, "UP NEXT", self.height // 3 - 60, label_font, ACCENT_COLOR)

        # Singer name (large and prominent)
        name_font = self._get_font(140, bold=True)
        self._center_text(draw, singer_name, self.height // 3 + 40, name_font, PRIMARY_TEXT_COLOR)

        # Song title and artist (optional, smaller)
        song_y = self.height // 3 + 200
        if song_title:
            # Truncate if too long
            if len(song_title) > 50:
                song_title = song_title[:47] + "..."
            title_font = self._get_font(36)
            self._center_text(draw, song_title, song_y, title_font, SECONDARY_TEXT_COLOR)
            song_y += 50  # Move down for artist

        # Show artist if available (below song title)
        if artist:
            if len(artist) > 40:
                artist = artist[:37] + "..."
            artist_font = self._get_font(28)
            self._center_text(draw, f"by {artist}", song_y, artist_font, SECONDARY_TEXT_COLOR)

        # Note: QR code is handled by the streaming overlay for consistency

        # Save and return path
        output_path = os.path.join(self.cache_dir, "interstitial_transition.png")
        img.save(output_path, "PNG")
        self.logger.info("Generated transition interstitial for: %s", singer_name)
        return output_path

    def generate_end_of_queue_screen(self, message: str = "That's all for now!") -> str:
        """
        Generate the end-of-queue screen.

        Args:
            message: Message to display

        Returns:
            Path to the generated image file
        """
        if not PIL_AVAILABLE:
            return self._generate_fallback_image("end")

        img, draw = self._create_base_image()

        # kbox wordmark, small, top of screen
        self._center_wordmark(draw, 50, self._get_wordmark_font(56))

        # Main message
        message_font = self._get_font(72, bold=True)
        self._center_text(draw, message, self.height // 3, message_font, PRIMARY_TEXT_COLOR)

        # Instruction to keep going
        sub_font = self._get_font(48)
        self._center_text(
            draw,
            "Scan the QR code to add more songs",
            self.height // 2,
            sub_font,
            SECONDARY_TEXT_COLOR,
        )

        # Note: QR code is handled by the streaming overlay for consistency

        # Save and return path
        output_path = os.path.join(self.cache_dir, "interstitial_end.png")
        img.save(output_path, "PNG")
        self.logger.info("Generated end-of-queue interstitial: %s", output_path)
        return output_path

    def generate_message_screen(self, message: str) -> str:
        """
        Generate a generic centered-message screen (e.g. "Loading new video for X...").

        Used for transient operator/system messages where a blank pipeline
        state would otherwise leave the screen empty - e.g. while replacing
        the currently-playing song's content.

        Args:
            message: Message to display

        Returns:
            Path to the generated image file
        """
        if not PIL_AVAILABLE:
            return self._generate_fallback_image("message")

        img, draw = self._create_base_image()

        # kbox wordmark, small, top of screen
        self._center_wordmark(draw, 50, self._get_wordmark_font(56))

        message_font = self._get_font(64, bold=True)
        self._center_text(draw, message, self.height // 2 - 40, message_font, PRIMARY_TEXT_COLOR)

        # Save and return path
        output_path = os.path.join(self.cache_dir, "interstitial_message.png")
        img.save(output_path, "PNG")
        self.logger.info("Generated message interstitial: %s", message)
        return output_path

    def _generate_fallback_image(self, screen_type: str) -> str:
        """Generate a simple fallback image when PIL is not available."""
        # Create a minimal 1x1 black image as fallback
        # This shouldn't happen in practice since PIL is a dependency
        os.path.join(self.cache_dir, f"interstitial_{screen_type}.png")

        # If PIL not available, we can't generate images
        # Return an empty path - caller should handle this gracefully
        self.logger.warning("Cannot generate interstitial without PIL")
        return ""
