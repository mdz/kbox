import logging

import mido

class MidiController:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.port = mido.open_input(config.midi_input)
        self.server = None
    
    def run(self):
        logging.debug('Listening for MIDI messages...')
        while True:
            for msg in self.port.iter_pending():
                self.handle_message(msg)
    
    def handle_message(self, msg):
        if msg.type == 'note_on':
            self.handle_note_on(msg)
        elif msg.type == 'note_off':
            pass
        else:
            self.logger.debug('Unhandled message: %s', msg)

    def handle_note_on(self, msg):
        self.logger.debug('Note on: %s', msg.note)
        if msg.note < 48 or msg.note > 72:
            self.logger.warn('Ignoring note outside of range: %s', msg.note)
            return

        semitones = msg.note - 60
        self.logger.info('Semitones: %s', semitones)

        if self.server is not None:
            self.server.set_pitch_shift(semitones)
        else:
            self.logger.warn('Server not set')