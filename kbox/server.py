import logging
import threading

class Server:
    def __init__(self, config, midi_controller, audio_controller):
        self.config = config
        self.midi_controller = midi_controller
        self.midi_controller.server = self # TODO: This is a hack
        self.audio_controller = audio_controller
        self.logger = logging.getLogger(__name__)
    
    def set_pitch_shift(self, semitones):
        self.audio_controller.set_pitch_shift(semitones)
    
    def run(self):
        logging.debug('Starting server...')
        audio_thread = threading.Thread(target=self.audio_controller.run)
        audio_thread.start()
        midi_thread = threading.Thread(target=self.midi_controller.run)
        midi_thread.start()
        logging.debug('Server started')
        audio_thread.join()
        midi_thread.join()
