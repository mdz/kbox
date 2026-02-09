"""
Interstitial screen generation for kbox.

Generates images for display between songs, during idle, and at end of queue.
"""

import logging
import os
import tempfile
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

# Try to import PIL, but gracefully handle if not available
try:
    from PIL import Image, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("PIL/Pillow not installed, interstitials will be text-only")


# Default colors (dark theme to match karaoke aesthetic)
BACKGROUND_COLOR = (20, 20, 30)  # Dark blue-gray
PRIMARY_TEXT_COLOR = (255, 255, 255)  # White
ACCENT_COLOR = (74, 158, 255)  # Blue accent (matches UI)
SECONDARY_TEXT_COLOR = (150, 150, 160)  # Muted gray


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

    def generate_idle_screen(self) -> str:
        """
        Generate the idle screen (before playback starts).

        Instructs the audience to scan the QR code overlay to pick songs.

        Returns:
            Path to the generated image file
        """
        if not PIL_AVAILABLE:
            return self._generate_fallback_image("idle")

        img, draw = self._create_base_image()

        # Title
        title_font = self._get_font(120, bold=True)
        self._center_text(draw, "kbox", self.height // 4, title_font, ACCENT_COLOR)

        # Subtitle
        subtitle_font = self._get_font(48)
        self._center_text(
            draw, "Karaoke", self.height // 4 + 140, subtitle_font, SECONDARY_TEXT_COLOR
        )

        # Main instruction - tell users to scan the QR code
        instruction_font = self._get_font(56, bold=True)
        self._center_text(
            draw,
            "Scan the QR code to pick a song",
            self.height // 2 + 50,
            instruction_font,
            PRIMARY_TEXT_COLOR,
        )

        # Arrow hint pointing toward QR code location (top-left by default)
        hint_font = self._get_font(32)
        self._center_text(
            draw,
            "\u2196  use your phone\u2019s camera",
            self.height // 2 + 130,
            hint_font,
            SECONDARY_TEXT_COLOR,
        )

        # Note: QR code is handled by the streaming overlay for consistency

        # Save and return path
        output_path = os.path.join(self.cache_dir, "interstitial_idle.png")
        img.save(output_path, "PNG")
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

        # "Get ready!" message
        ready_font = self._get_font(48)
        self._center_text(draw, "Get ready!", self.height * 2 // 3, ready_font, ACCENT_COLOR)

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

    def _generate_fallback_image(self, screen_type: str) -> str:
        """Generate a simple fallback image when PIL is not available."""
        # Create a minimal 1x1 black image as fallback
        # This shouldn't happen in practice since PIL is a dependency
        os.path.join(self.cache_dir, f"interstitial_{screen_type}.png")

        # If PIL not available, we can't generate images
        # Return an empty path - caller should handle this gracefully
        self.logger.warning("Cannot generate interstitial without PIL")
        return ""
