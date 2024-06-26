import logging

import mido

class MidiController:
    def __init__(self, config, audio_controller):
        self.config = config
        self.audio_controller = audio_controller
        self.logger = logging.getLogger(__name__)
        if not self.config.enable_midi:
            return
        self.port = mido.open_input(self.find_input(config.midi_input))
        
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
        self.logger.debug('Listening for MIDI messages...')
        while True:
            self.handle_message(self.port.receive())
        
    def stop(self):
        self.logger.debug('Stopping MIDI...')
        # does nothing yet
        # runs as daemon thread so will exit when main thread exits
    
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

        self.audio_controller.set_pitch_shift(semitones)
