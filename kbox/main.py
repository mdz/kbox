import logging
import threading

from .audio import AudioController
from .config import Config
from .midi import MidiController
from .server import Server

logging.basicConfig(level=logging.DEBUG)

config = Config()
midi = MidiController(config)
audio = AudioController(config)
server = Server(config, midi, audio)

server.run()