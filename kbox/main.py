"""
Main entry point for kbox.

Initializes all components and starts the server.
"""

import logging
import secrets

import uvicorn

from .config_manager import ConfigManager
from .database import Database
from .history import HistoryManager
from .llm import LLMClient
from .overlay import generate_qr_code
from .platform import is_macos, run_uvicorn_in_thread, run_with_gst_macos_main
from .playback import PlaybackController
from .queue import QueueManager
from .song_metadata import SongMetadataExtractor
from .streaming import StreamingController
from .suggestions import SuggestionEngine
from .user import UserManager
from .video_library import VideoLibrary
from .web.server import create_app
from .youtube import YouTubeSource

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


class KboxServer:
    """Main server class that orchestrates all components."""

    def __init__(self):
        """
        Initialize all components.
        """
        logger.info("Initializing kbox server...")

        # Initialize database
        self.database = Database()

        # Initialize configuration manager
        self.config_manager = ConfigManager(self.database)

        # Get or create access token (persists across restarts)
        self.access_token = self.config_manager.get("access_token")
        if not self.access_token:
            self.access_token = secrets.token_urlsafe(16)
            self.config_manager.set("access_token", self.access_token)
            logger.info("Generated new access token")
        else:
            logger.info("Using existing access token from database")

        # Get or create session secret (persists across restarts)
        self.session_secret = self.config_manager.get("session_secret")
        if not self.session_secret:
            self.session_secret = secrets.token_urlsafe(32)
            self.config_manager.set("session_secret", self.session_secret)
            logger.info("Generated new session secret")

        # Initialize video library and register sources
        self.video_library = VideoLibrary(self.config_manager)
        self.video_library.register_source(YouTubeSource(self.config_manager))

        if not self.video_library.is_source_configured("youtube"):
            logger.warning(
                "YouTube API key not configured. YouTube search will be unavailable. "
                "Please set the API key via the web UI (/config)."
            )

        # LLM client for AI features (suggestions, metadata extraction)
        self.llm_client = LLMClient(self.config_manager)

        # SongMetadataExtractor for extracting artist/song from video titles
        self.metadata_extractor = SongMetadataExtractor(
            self.database,
            llm_client=self.llm_client,
        )

        # Initialize queue manager with video library and metadata extractor
        self.queue_manager = QueueManager(
            self.database,
            video_library=self.video_library,
            metadata_extractor=self.metadata_extractor,
        )
        self.user_manager = UserManager(self.database)
        self.history_manager = HistoryManager(self.database)

        # StreamingController uses ConfigManager for configuration
        self.streaming_controller = StreamingController(self.config_manager, self)

        # PlaybackController
        self.playback_controller = PlaybackController(
            self.queue_manager,
            self.streaming_controller,
            self.config_manager,
            self.history_manager,
        )

        # SuggestionEngine for AI-powered song recommendations
        self.suggestion_engine = SuggestionEngine(
            self.config_manager,
            self.history_manager,
            self.queue_manager,
            self.video_library,
            llm_client=self.llm_client,
        )

        # Web server
        self.web_app = create_app(
            self.queue_manager,
            self.video_library,
            self.playback_controller,
            self.config_manager,
            self.user_manager,
            self.history_manager,
            suggestion_engine=self.suggestion_engine,
            streaming_controller=self.streaming_controller,
            access_token=self.access_token,
            session_secret=self.session_secret,
        )

        # Uvicorn server instance (will be created in run())
        self.uvicorn_server = None
        self.server_thread = None

        logger.info("kbox server initialized")

    def run(self):
        """Start the server."""
        logger.info("Starting kbox server...")

        # Start streaming controller in background thread
        # Note: On macOS, GStreamer may crash - defer until actually needed
        # For now, don't start it automatically to avoid crashes during web server startup
        logger.info("Streaming controller ready (will start when needed)")

        # Determine external URL for QR code
        # Priority: 1) KBOX_EXTERNAL_URL env var, 2) external_url config, 3) auto-detect
        import os
        import socket

        external_url = os.environ.get("KBOX_EXTERNAL_URL")
        if external_url:
            web_url = external_url.rstrip("/")
            logger.info("Using external URL from environment: %s", web_url)
        else:
            external_url = self.config_manager.get("external_url")
            if external_url:
                web_url = external_url.rstrip("/")
                logger.info("Using external URL from config: %s", web_url)
            else:
                # Fall back to auto-detection
                hostname = socket.gethostname()
                try:
                    local_ip = socket.gethostbyname(hostname)
                except OSError:
                    local_ip = "localhost"
                web_url = f"http://{local_ip}:8000"
                logger.info("Using auto-detected URL: %s", web_url)

        # Generate QR code for the web UI URL (with access token for remote access)
        qr_url = f"{web_url}?key={self.access_token}"
        cache_dir = self.config_manager.get("cache_directory")
        qr_path = generate_qr_code(qr_url, size=100, cache_dir=cache_dir)
        if qr_path:
            self.streaming_controller.update_qr_overlay(qr_path)
            logger.info("QR code overlay configured")
        else:
            logger.warning("QR code generation failed, overlay disabled")

        # Show initial idle screen
        self.playback_controller.show_idle_screen()

        logger.info("=" * 60)
        logger.info("kbox is running!")
        logger.info("Web UI: %s", qr_url)
        logger.info("=" * 60)

        # Use uvicorn Server API for better control over shutdown
        config = uvicorn.Config(self.web_app, host="0.0.0.0", port=8000, log_level="info")
        self.uvicorn_server = uvicorn.Server(config)

        # On macOS, run uvicorn in a thread so main thread can run NSRunLoop
        # On other platforms, run uvicorn normally (blocking)
        if is_macos():
            self.server_thread, wait_func = run_uvicorn_in_thread(self.uvicorn_server)
            wait_func()
        else:
            self.uvicorn_server.run()

    def stop(self):
        """Stop all components."""
        logger.info("Stopping kbox server...")

        if self.uvicorn_server:
            self.uvicorn_server.should_exit = True

        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=2.0)

        if self.playback_controller:
            self.playback_controller.shutdown()

        if self.queue_manager:
            self.queue_manager.stop_download_monitor()

        if self.streaming_controller:
            self.streaming_controller.stop()

        if self.database:
            self.database.close()

        logger.info("kbox server stopped")


def actual_main():
    """Actual main function that runs the server."""
    import argparse

    parser = argparse.ArgumentParser(description="kbox - Self-contained karaoke system")
    parser.parse_args()

    server = KboxServer()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


def main():
    """Main entry point. Uses gst_macos_main() on macOS for proper video support."""
    return run_with_gst_macos_main(actual_main)


if __name__ == "__main__":
    main()
