import logging
import sys

import gi

gi.require_version('GLib', '2.0')
gi.require_version('GObject', '2.0')
gi.require_version('Gst', '1.0')

from gi.repository import Gst, GObject, GLib

Gst.init(None)

class AudioController:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.pitch_shift_semitones = 0
        if not self.config.enable_audio:
            self.logger.warn('Audio disabled')
            return

        self.pipeline = self.create_pipeline()
    
    def create_pipeline(self):
        bin = Gst.Pipeline.new('audio_pipeline')

        element, device = self.config.audio_input
        source = Gst.ElementFactory.make(element, 'source')
        source.set_property('device', device)

        convert_input = Gst.ElementFactory.make('audioconvert', 'convert_input')
        pitch_shift = Gst.ElementFactory.make('ladspa-ladspa-rubberband-so-rubberband-r3-pitchshifter-stereo', 'pitch_shift')
        pitch_shift.set_property('semitones', self.pitch_shift_semitones)
        convert_output = Gst.ElementFactory.make('audioconvert', 'convert_output')

        element, device = self.config.audio_output
        self.logger.debug("Element: %s, Device: %s", element, device)
        sink = Gst.ElementFactory.make(element, 'sink')
        sink.set_property('device', device)

        bin.add(source)
        bin.add(convert_input)
        source.link(convert_input)
        bin.add(pitch_shift)
        convert_input.link(pitch_shift)
        bin.add(convert_output)
        pitch_shift.link(convert_output)
        bin.add(sink)
        convert_output.link(sink)

        return bin
    
    def set_pitch_shift(self, semitones):
        if not self.config.enable_audio:
            self.logger.warn('Audio disabled')
            return
        if semitones == self.pitch_shift_semitones:
            self.logger.debug('Pitch shift already set to %s semitones', semitones)
            return
        
        self.logger.info('Setting pitch shift to %s semitones', semitones)
        pitch_shift = self.pipeline.get_by_name('pitch_shift')
        pitch_shift.set_property('semitones', semitones)
        self.pitch_shift_semitones = semitones

    def run(self):
        if not self.config.enable_audio:
            self.logger.debug('Audio disabled')
            return
        self.logger.debug('Starting gstreamer pipeline...')
        bus = self.pipeline.get_bus()
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error('Failed to start pipeline')
            return

        # wait until EOS or error
        msg = bus.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        #self.pipeline.set_state(Gst.State.NULL)