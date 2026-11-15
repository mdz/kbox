"""
The interstitial screens shown between songs and while idle.

ScreenPresenter owns the lazily-created InterstitialGenerator -- the only
state here -- and takes the config manager and streaming controller it needs
to render and display. Generation failure is never fatal: every method logs
and returns, because a missing interstitial is a blank screen, not a broken
party.
"""

import logging
from typing import Optional


class ScreenPresenter:
    """Renders interstitial screens and puts them on the display."""

    def __init__(self, config_manager, streaming_controller):
        """
        Args:
            config_manager: ConfigManager instance, for the cache directory
                and the party theme
            streaming_controller: StreamingController instance; only
                display_image() is used, plus its optional `server`
                attribute for the QR code URL.
        """
        self.config_manager = config_manager
        self.streaming_controller = streaming_controller
        self.logger = logging.getLogger(__name__)

        # Interstitial generator (lazy-initialized)
        self._interstitial_generator = None

    def _get_interstitial_generator(self):
        """Get or create the interstitial generator (lazy initialization)."""
        if self._interstitial_generator is None:
            import os

            from ..interstitials import InterstitialGenerator

            cache_dir = os.path.join(
                str(self.config_manager.get_cache_directory()), "interstitials"
            )
            self._interstitial_generator = InterstitialGenerator(cache_dir=cache_dir)
        return self._interstitial_generator

    def _get_web_url(self) -> Optional[str]:
        """Get the web interface URL for QR codes."""
        # Access server through streaming controller
        server = getattr(self.streaming_controller, "server", None)
        if server:
            return getattr(server, "external_url", None)
        return None

    def show_idle(self):
        """Show the idle screen interstitial with QR code instructions."""
        self.logger.info("Showing idle screen")

        generator = self._get_interstitial_generator()
        party_theme = self.config_manager.get("suggestion_theme") or None
        image_path = generator.generate_idle_screen(party_theme=party_theme)

        if image_path:
            self.streaming_controller.display_image(image_path)
        else:
            self.logger.warning("Could not generate idle screen")

    def show_transition(
        self, singer_name: str, song_title: Optional[str] = None, artist: Optional[str] = None
    ):
        """
        Display the between-songs transition screen.

        Args:
            singer_name: Name of the next singer
            song_title: Optional song title (can be None for surprise)
            artist: Optional artist name (from extracted metadata)
        """
        self.logger.info("Showing transition screen for: %s", singer_name)

        generator = self._get_interstitial_generator()
        web_url = self._get_web_url()

        image_path = generator.generate_transition_screen(
            singer_name=singer_name, song_title=song_title, artist=artist, web_url=web_url
        )

        if image_path:
            self.streaming_controller.display_image(image_path)
        else:
            self.logger.warning("Could not generate transition screen")

    def show_message(self, message: str):
        """
        Display a generic centered-message interstitial screen.

        Used for transient system messages (e.g. "Loading new video for X...")
        where the pipeline has no video loaded and a blank screen would
        otherwise result.

        Args:
            message: Message to display
        """
        self.logger.info("Showing message screen: %s", message)

        generator = self._get_interstitial_generator()
        image_path = generator.generate_message_screen(message)

        if image_path:
            self.streaming_controller.display_image(image_path)
        else:
            self.logger.warning("Could not generate message screen")

    def show_end_of_queue(self, message: str = "That's all for now!"):
        """
        Display the end-of-queue interstitial screen.

        Args:
            message: Message to display
        """
        self.logger.info("Showing end-of-queue screen: %s", message)

        generator = self._get_interstitial_generator()
        image_path = generator.generate_end_of_queue_screen(message=message)

        if image_path:
            self.streaming_controller.display_image(image_path)
        else:
            self.logger.warning("Could not generate end-of-queue screen")
