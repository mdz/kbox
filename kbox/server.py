import logging
import threading

from .audio import AudioController
from .midi import MidiController

class Server:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.audio_controller = AudioController(config, self)
        self.midi_controller = MidiController(config, self)
    
    def set_pitch_shift(self, semitones):
        self.audio_controller.set_pitch_shift(semitones)
    
    def run(self):
        logging.debug('Starting server...')
        audio_thread = threading.Thread(target=self.audio_controller.run)
        audio_thread.start()
        if self.config.enable_midi:
            midi_thread = threading.Thread(target=self.midi_controller.run)
            midi_thread.daemon = True
            midi_thread.start()
        logging.info('Server started')
        audio_thread.join()
        logging.info('Server stopped')