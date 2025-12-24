"""
Main entry point for kbox.

Initializes all components and starts the server.
"""

import logging

import uvicorn

from .cache import CacheManager
from .config_manager import ConfigManager
from .database import Database
from .history import HistoryManager
from .overlay import generate_qr_code
from .platform import is_macos, run_uvicorn_in_thread, run_with_gst_macos_main
from .playback import PlaybackController
from .queue import QueueManager
from .streaming import StreamingController
from .user import UserManager
from .video_source import VideoManager
from .web.server import create_app

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

        # Initialize cache manager
        self.cache_manager = CacheManager(self.config_manager)

        # Initialize video manager (automatically registers all sources)
        self.video_manager = VideoManager(self.config_manager, self.cache_manager)

        if not self.video_manager.is_source_configured("youtube"):
            logger.warning(
                "YouTube API key not configured. YouTube search will be unavailable. "
                "Please set the API key via the web UI (/config)."
            )

        # Initialize queue manager with video manager
        self.queue_manager = QueueManager(
            self.database,
            video_manager=self.video_manager,
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

        # Web server
        self.web_app = create_app(
            self.queue_manager,
            self.video_manager,
            self.playback_controller,
            self.config_manager,
            self.user_manager,
            self.history_manager,
            streaming_controller=self.streaming_controller,
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

        # Generate QR code for the web UI URL
        cache_dir = self.config_manager.get("cache_directory")
        qr_path = generate_qr_code(web_url, size=100, cache_dir=cache_dir)
        if qr_path:
            self.streaming_controller.update_qr_overlay(qr_path)
            logger.info("QR code overlay configured")
        else:
            logger.warning("QR code generation failed, overlay disabled")

        # Show initial idle screen
        self.playback_controller.show_idle_screen()

        logger.info("=" * 60)
        logger.info("kbox is running!")
        logger.info("Web UI: %s", web_url)
        logger.info("API: %s/api", web_url)
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
