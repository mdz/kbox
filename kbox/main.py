"""
Main entry point for kbox.

Initializes all components and starts the server.
"""

import logging
import sys
import uvicorn

from .config import Config
from .database import Database
from .config_manager import ConfigManager
from .queue import QueueManager
from .youtube import YouTubeClient
from .streaming import StreamingController
from .playback import PlaybackController
from .web.server import create_app
from .platform import is_macos, run_with_gst_macos_main, run_uvicorn_in_thread

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


class KboxServer:
    """Main server class that orchestrates all components."""

    def __init__(self, test_mode: bool = False):
        """
        Initialize all components.

        Args:
            test_mode: If True, enable test mode (operator controls enabled by default)
        """
        self.test_mode = test_mode
        logger.info(
            "Initializing kbox server..." + (" (TEST MODE)" if test_mode else "")
        )

        # Initialize database
        self.database = Database()

        # Initialize configuration manager
        self.config_manager = ConfigManager(self.database)

        # Load YouTube API key
        youtube_api_key = self.config_manager.get("youtube_api_key")
        if not youtube_api_key:
            logger.error(
                "YouTube API key not configured. Please set it via the web UI or database."
            )
            sys.exit(1)

        # Initialize components
        self.queue_manager = QueueManager(self.database)
        self.youtube_client = YouTubeClient(
            youtube_api_key, cache_directory=self.config_manager.get("cache_directory")
        )

        # Create a minimal config object for StreamingController
        # TODO: Refactor to use ConfigManager instead
        self.config = Config()

        # StreamingController needs a server reference for callbacks
        # We'll pass self for now
        self.streaming_controller = StreamingController(self.config, self)

        # PlaybackController
        self.playback_controller = PlaybackController(
            self.queue_manager,
            self.youtube_client,
            self.streaming_controller,
            self.config_manager,
        )

        # Web server
        self.web_app = create_app(
            self.queue_manager,
            self.youtube_client,
            self.playback_controller,
            self.config_manager,
            test_mode=self.test_mode,
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

        # Get network info
        import socket

        hostname = socket.gethostname()
        try:
            local_ip = socket.gethostbyname(hostname)
        except:
            local_ip = "localhost"

        logger.info("=" * 60)
        logger.info("kbox is running!")
        logger.info("Web UI: http://%s:8000", local_ip)
        logger.info("API: http://%s:8000/api", local_ip)
        logger.info("=" * 60)

        # Use uvicorn Server API for better control over shutdown
        config = uvicorn.Config(
            self.web_app, host="0.0.0.0", port=8000, log_level="info"
        )
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
            self.playback_controller.stop()

        if self.streaming_controller:
            self.streaming_controller.stop()

        if self.database:
            self.database.close()

        logger.info("kbox server stopped")


def actual_main():
    """Actual main function that runs the server."""
    import argparse

    parser = argparse.ArgumentParser(description="kbox - Self-contained karaoke system")
    parser.add_argument(
        "--test-mode",
        "-t",
        action="store_true",
        help="Enable test mode (operator controls enabled by default)",
    )
    args = parser.parse_args()

    server = KboxServer(test_mode=args.test_mode)
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
