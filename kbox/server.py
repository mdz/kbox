import logging
import signal
import threading

from .audio import AudioController
from .midi import MidiController

class Server:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.audio_controller = AudioController(config, self)
        self.midi_controller = MidiController(config, self)
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

    
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
    
    def signal_handler(self, _signum, _frame):
        self.logger.debug('Received signal %s', _signum)
        if _signum in (signal.SIGINT, signal.SIGTERM):
            self.stop()
    
    def stop(self, _signum, _frame):
        self.logger.info('Stopping server...')
        self.audio_controller.stop()
        self.midi_controller.stop()
