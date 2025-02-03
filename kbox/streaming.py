import logging
import sys

import gi

gi.require_version('GLib', '2.0')
gi.require_version('GObject', '2.0')
gi.require_version('Gst', '1.0')

from gi.repository import Gst

class StreamingController:
    def __init__(self, config, server):
        self.config = config
        self.server = server
        self.logger = logging.getLogger(__name__)
        self.pitch_shift_semitones = 0
        self.pipeline = self.create_pipeline()
    
    def create_pipeline(self):
        if not Gst.is_initialized():
            self.logger.debug('Initializing gstreamer...')
            Gst.init(None)

        pipeline = Gst.Pipeline.new('StreamingController')

        source = self.make_element(self.config.GSTREAMER_SOURCE, 'source')
        self.set_device(source, self.config.video_input)
        pipeline.add(source)

        #video_demux = self.make_element('matroskademux', 'demux')
        #pipeline.add(video_demux)

        decode = self.make_element('decodebin', 'decode')
        pipeline.add(decode)


        convert_audio_input = self.make_element('audioconvert', 'convert_audio_input')
        pipeline.add(convert_audio_input)

        pitch_shift = self.make_element(self.config.RUBBERBAND_PLUGIN, 'pitch_shift')
        pitch_shift.set_property('semitones', self.pitch_shift_semitones)
        pipeline.add(pitch_shift)

        convert_audio_output = self.make_element('audioconvert', 'convert_audio_output')
        pipeline.add(convert_audio_output)

        audio_sink = self.make_element(self.config.GSTREAMER_SINK, 'audio_sink')
        self.set_device(audio_sink, self.config.audio_output)
        pipeline.add(audio_sink)

        video_sink = self.make_element('fakesink', 'video_sink')
        pipeline.add(video_sink)

        # decodebin uses dynamic pads, so we need to link them
        # via this callback
        def decodebin_pad_added(element, pad):
            string = pad.query_caps(None).to_string()
            self.logger.debug('Found stream: %s' % string)
            if string.startswith('audio/x-raw'):
                pad.link(convert_audio_input.get_static_pad('sink'))
            elif string.startswith('video/x-raw'):
                pad.link(video_sink.get_static_pad('sink'))

        decode.connect("pad-added", decodebin_pad_added)

        source.link(decode)
        #video_demux.link(audio_decode)
        #print(video_demux)
        #video_demux.link_pads('audio_0', audio_decode, None)
        #video_demux.link_pads('video_0', video_sink, None)
        #decode.link(convert_audio_input)
        #convert_audio_input.link(pitch_shift)
        #pitch_shift.link(convert_audio_output)
        convert_audio_input.link(convert_audio_output)
        convert_audio_output.link(audio_sink)


        #decode.link(audio_sink)

        return pipeline
    
    def make_element(self, element_type, name):
        element = Gst.ElementFactory.make(element_type, name)
        if element is None:
            raise ValueError('Unable to initialize gstreamer element %s as %s' % (element_type, name))
        return element
    
    def set_device(self, element, device):
        if device is None:
            return
        
        element_type = type(element).__name__
        if element_type in ('GstAlsaSrc', 'GstAlsaSink'):
            element.set_property('device', device)
        elif element_type == 'GstURIDecodeBin':
            element.set_property('uri', device)
        elif element_type == 'GstFileSrc':
            element.set_property('location', device)
        else:
            raise NotImplementedError('set_device not implemented for %s' % element_type)
    
    def set_pitch_shift(self, semitones):
        if semitones == self.pitch_shift_semitones:
            self.logger.debug('Pitch shift already set to %s semitones', semitones)
            return
        
        self.logger.info('Setting pitch shift to %s semitones', semitones)
        pitch_shift = self.pipeline.get_by_name('pitch_shift')
        pitch_shift.set_property('semitones', semitones)
        self.pitch_shift_semitones = semitones

    def run(self):
        self.logger.debug('Starting gstreamer pipeline...')
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error('Failed to start pipeline')
            return
        elif ret == Gst.StateChangeReturn.ASYNC:
            # will be handled by message loop
            pass
        else:
            self.logger.warn('Unexpected result from set_state: %s', ret)

    
        # wait for messages
        bus = self.pipeline.get_bus()
        while True:
            msg = bus.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.STATE_CHANGED)

            self.logger.debug('Received message: %s', msg.type)
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                self.logger.error('GStreamer error: %s: %s', err, debug)
                break
            elif msg.type == Gst.MessageType.STATE_CHANGED:
                change = msg.parse_state_changed()
                self.logger.debug('State changed: %s -> %s', change.oldstate.value_name, change.newstate.value_name)
                oldstate = change.oldstate
                newstate = change.newstate
                if newstate == Gst.State.PLAYING:
                    self.logger.info('Pipeline state changed to PLAYING')
                elif newstate == Gst.State.NULL:
                    self.logger.debug('Pipeline state changed to NULL')
                    break
                elif oldstate == Gst.State.NULL and newstate == Gst.State.READY:
                    self.logger.debug('Pipeline is READY')
                elif newstate == Gst.State.READY:
                    self.logger.debug('Pipeline stopped')
                    break
                elif newstate == Gst.State.PAUSED:
                    self.logger.info('Pipeline paused')
                else:
                    self.logger.debug('Unhandled state change: %s', newstate)
            elif msg.type == Gst.MessageType.EOS:
                self.logger.debug('End of stream')
                break
            else:
                self.logger.error('Unhandled message: %s', msg.type)
                break
        self.logger.debug('AudioController.run() exiting')
    
    def stop(self):
        self.logger.debug('Stopping gstreamer pipeline...')
        result = self.pipeline.set_state(Gst.State.NULL)
        if result == Gst.StateChangeReturn.SUCCESS:
            self.logger.debug('Pipeline state changed successfully')
        elif result == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Failed to change pipeline state')
        else:
            raise RuntimeError('Unexpected result from set_state: %s', result)
