import logging

import mido

class MidiController:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        if not self.config.enable_midi:
            return
        self.port = mido.open_input(self.find_input(config.midi_input))
        self.server = None
    
    def register_server(self, server):
        if self.server is not None:
            self.logger.warn('Server already registered, ignoring')
            return
        self.server = server
    
    def find_input(self, name):
        all_inputs = mido.get_input_names()
        for port in all_inputs:
            if name in port:
                self.logger.info('Using MIDI input: %s', port)
                return port
        raise ValueError('MIDI input "%s" not found, available inputs: %s' % (name, all_inputs))
    
    def run(self):
        if not self.config.enable_midi:
            self.logger.warn('MIDI disabled')
            return
        logging.debug('Listening for MIDI messages...')
        while True:
            self.handle_message(self.port.receive())
    
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
        self.logger.debug('Note=%s -> semitones=%s', msg.note, semitones)

        if self.server is not None:
            self.server.set_pitch_shift(semitones)
        else:
            self.logger.warn('Server not set')