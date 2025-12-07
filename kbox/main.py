"""
Main entry point for kbox.

Initializes all components and starts the server.
"""

import logging
import signal
import sys
import threading
import ctypes
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
    
    def __init__(self, test_mode: bool = False):
        """
        Initialize all components.
        
        Args:
            test_mode: If True, enable test mode (operator controls enabled by default)
        """
        self.test_mode = test_mode
        logger.info('Initializing kbox server...' + (' (TEST MODE)' if test_mode else ''))
        
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
            self.config_manager,
            test_mode=self.test_mode
        )
        
        # Setup signal handlers (only if we're in the main thread)
        # On macOS with gst_macos_main, signal handlers are set up differently
        try:
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
        except ValueError:
            # Signal handlers can only be set in main thread
            # On macOS with gst_macos_main, we'll handle this differently
            logger.debug('Could not set signal handlers (may be in non-main thread)')
        
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
        
        # On macOS, run uvicorn in a thread so main thread can run NSRunLoop
        # On other platforms, run uvicorn normally (blocking)
        if sys.platform == 'darwin':
            # Run uvicorn in a background thread
            def run_server():
                uvicorn.run(
                    self.web_app,
                    host='0.0.0.0',
                    port=8000,
                    log_level='info'
                )
            
            server_thread = threading.Thread(target=run_server, daemon=False)
            server_thread.start()
            
            # On macOS, we need to run NSRunLoop on main thread
            # This will be handled by gst_macos_main() wrapper
            # For now, just wait for the server thread
            try:
                server_thread.join()
            except KeyboardInterrupt:
                logger.info('Interrupted by user')
        else:
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

def actual_main():
    """Actual main function that runs the server."""
    import argparse
    parser = argparse.ArgumentParser(description='kbox - Self-contained karaoke system')
    parser.add_argument('--test-mode', '-t', action='store_true',
                        help='Enable test mode (operator controls enabled by default)')
    args = parser.parse_args()
    
    server = KboxServer(test_mode=args.test_mode)
    try:
        server.run()
    except KeyboardInterrupt:
        logger.info('Interrupted by user')
        server.stop()
    except Exception as e:
        logger.error('Fatal error: %s', e, exc_info=True)
        server.stop()
        sys.exit(1)
    return 0

def main():
    """Main entry point. Uses gst_macos_main() on macOS for proper video support."""
    if sys.platform == 'darwin':
        try:
            # Load GStreamer library
            import os
            gst_lib_path = os.path.join(os.path.expanduser('~/.homebrew'), 'lib/libgstreamer-1.0.dylib')
            if not os.path.exists(gst_lib_path):
                # Try Homebrew default location
                gst_lib_path = '/opt/homebrew/lib/libgstreamer-1.0.dylib'
            if not os.path.exists(gst_lib_path):
                # Fallback to system library path
                import subprocess
                result = subprocess.run(['brew', '--prefix', 'gstreamer'], 
                                      capture_output=True, text=True)
                if result.returncode == 0:
                    gst_prefix = result.stdout.strip()
                    gst_lib_path = f'{gst_prefix}/lib/libgstreamer-1.0.dylib'
            
            if os.path.exists(gst_lib_path):
                # Load GStreamer library
                gst_lib = ctypes.CDLL(gst_lib_path)
                
                # Define GstMainFunc type
                GstMainFunc = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p))
                
                # Get gst_macos_main function
                gst_macos_main = gst_lib.gst_macos_main
                gst_macos_main.argtypes = [GstMainFunc, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p), ctypes.c_void_p]
                gst_macos_main.restype = ctypes.c_int
                
                # Convert argv to ctypes format
                argc = len(sys.argv)
                argv = (ctypes.c_char_p * (argc + 1))()
                for i, arg in enumerate(sys.argv):
                    argv[i] = arg.encode('utf-8')
                argv[argc] = None
                
                # Create wrapper function
                def wrapper(argc, argv):
                    return actual_main()
                
                # Call gst_macos_main
                return gst_macos_main(GstMainFunc(wrapper), argc, argv, None)
            else:
                logger.warning('Could not find libgstreamer-1.0.dylib, falling back to regular main')
                return actual_main()
        except Exception as e:
            logger.warning('Could not use gst_macos_main: %s, falling back to regular main', e)
            return actual_main()
    else:
        return actual_main()

if __name__ == '__main__':
    main()
