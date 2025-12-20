import logging
import sys
from unittest.mock import create_autospec, MagicMock

from kbox.database import Database
from kbox.config_manager import ConfigManager
from kbox.server import Server
from kbox.streaming import StreamingController

from gi.repository import Gst

logging.basicConfig(level=logging.DEBUG)

def test_streaming():
    # Create a mock database and ConfigManager
    db = create_autospec(Database, instance=True)
    config_manager = ConfigManager(db)
    
    # Override test-specific settings
    config_manager.set('gstreamer_source', 'filesrc')
    config_manager.set('video_input_device', '/Users/zero/src/mine/kbox/test/fixtures/once-in-a-lifetime.mp4')
    config_manager.set('gstreamer_sink', 'osxaudiosink')
    config_manager.set('audio_output_device', None)
    config_manager.set('rubberband_plugin', 'ladspa-ladspa-rubberband-dylib-rubberband-r3-pitchshifter-stereo')

    server = create_autospec(Server, instance=True)
    streaming = StreamingController(config_manager, server)
    streaming.run()
