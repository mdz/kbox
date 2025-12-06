"""
Main entry point for kbox.

Initializes all components and starts the server.
"""

import logging
import signal
import sys
import threading
import uvicorn
from pathlib import Path

from .config import Config
from .database import Database
from .config_manager import ConfigManager
from .queue import QueueManager
from .youtube import YouTubeClient
from .streaming import StreamingController
from .playback import PlaybackController
from .midi import MidiController
from .web.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

class KboxServer:
    """Main server class that orchestrates all components."""
    
    def __init__(self):
        """Initialize all components."""
        logger.info('Initializing kbox server...')
        
        # Initialize database
        self.database = Database()
        
        # Initialize configuration manager
        self.config_manager = ConfigManager(self.database)
        
        # Load YouTube API key
        youtube_api_key = self.config_manager.get('youtube_api_key')
        if not youtube_api_key:
            logger.error('YouTube API key not configured. Please set it via the web UI or database.')
            sys.exit(1)
        
        # Initialize components
        self.queue_manager = QueueManager(self.database)
        self.youtube_client = YouTubeClient(
            youtube_api_key,
            cache_directory=self.config_manager.get('cache_directory')
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
            self.config_manager
        )
        
        # MIDI controller (only if enabled and device is configured)
        midi_input_name = self.config_manager.get('midi_input_name') or self.config.midi_input
        if self.config.enable_midi and midi_input_name:
            try:
                self.config.midi_input = midi_input_name
                self.midi_controller = MidiController(self.config, self)
                logger.info('MIDI controller initialized with device: %s', midi_input_name)
            except (ValueError, Exception) as e:
                logger.warning('Failed to initialize MIDI controller: %s. Continuing without MIDI.', e)
                self.midi_controller = None
        else:
            self.midi_controller = None
            logger.info('MIDI controller disabled (enable_midi=%s, device=%s)', 
                       self.config.enable_midi, midi_input_name)
        
        # Web server
        self.web_app = create_app(
            self.queue_manager,
            self.youtube_client,
            self.playback_controller,
            self.config_manager
        )
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        logger.info('kbox server initialized')
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info('Received signal %s, shutting down...', signum)
        self.stop()
        sys.exit(0)
    
    def set_pitch_shift(self, semitones):
        """Set pitch shift (called by MIDI controller)."""
        self.playback_controller.set_pitch(semitones)
    
    def run(self):
        """Start the server."""
        logger.info('Starting kbox server...')
        
        # Start streaming controller in background thread
        # Note: On macOS, GStreamer may crash - defer until actually needed
        # For now, don't start it automatically to avoid crashes during web server startup
        logger.info('Streaming controller ready (will start when needed)')
        # streaming_thread = threading.Thread(
        #     target=safe_streaming_run,
        #     daemon=True
        # )
        # streaming_thread.start()
        
        # Start MIDI controller if enabled
        if self.midi_controller:
            midi_thread = threading.Thread(
                target=self.midi_controller.run,
                daemon=True
            )
            midi_thread.start()
            logger.info('MIDI controller started')
        
        # Get network info
        import socket
        hostname = socket.gethostname()
        try:
            local_ip = socket.gethostbyname(hostname)
        except:
            local_ip = 'localhost'
        
        logger.info('=' * 60)
        logger.info('kbox is running!')
        logger.info('Web UI: http://%s:8000', local_ip)
        logger.info('API: http://%s:8000/api', local_ip)
        logger.info('=' * 60)
        
        # Start web server (blocking)
        uvicorn.run(
            self.web_app,
            host='0.0.0.0',
            port=8000,
            log_level='info'
        )
    
    def stop(self):
        """Stop all components."""
        logger.info('Stopping kbox server...')
        
        if self.playback_controller:
            self.playback_controller.stop()
        
        if self.streaming_controller:
            self.streaming_controller.stop()
        
        if self.midi_controller:
            self.midi_controller.stop()
        
        if self.database:
            self.database.close()
        
        logger.info('kbox server stopped')

def main():
    """Main entry point."""
    server = KboxServer()
    try:
        server.run()
    except KeyboardInterrupt:
        logger.info('Interrupted by user')
        server.stop()
    except Exception as e:
        logger.error('Fatal error: %s', e, exc_info=True)
        server.stop()
        sys.exit(1)

if __name__ == '__main__':
    main()
