import logging
import sys
from unittest.mock import create_autospec

from kbox.config import Config
from kbox.server import Server
from kbox.streaming import StreamingController

from gi.repository import Gst

logging.basicConfig(level=logging.DEBUG)

def test_streaming():
    config = create_autospec(Config, instance=True)
    config.GSTREAMER_SOURCE = 'filesrc'
    config.video_input = '/Users/zero/src/mine/kbox/test/fixtures/once-in-a-lifetime.mp4'

    config.GSTREAMER_SINK = 'osxaudiosink'
    config.audio_output = None

    config.RUBBERBAND_PLUGIN = 'ladspa-ladspa-rubberband-dylib-rubberband-r3-pitchshifter-stereo'


    server = create_autospec(Server, instance=True)
    streaming = StreamingController(config, server)
    streaming.run()
