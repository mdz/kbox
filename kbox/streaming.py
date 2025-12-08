import logging
import sys
from typing import Optional

# Defer GStreamer imports until actually needed to avoid crashes on import
# On macOS, importing GStreamer can cause segfaults due to library conflicts
_Gst = None

def _get_gst():
    """Lazily import GStreamer to avoid crashes on startup."""
    global _Gst
    if _Gst is not None:
        return _Gst
    
    try:
        import gi
        gi.require_version('GLib', '2.0')
        gi.require_version('GObject', '2.0')
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst as _Gst_module
        _Gst = _Gst_module
        return _Gst
    except Exception as e:
        logging.getLogger(__name__).error('Failed to import GStreamer: %s', e)
        raise

class StreamingController:
    def __init__(self, config, server, config_manager=None):
        self.config = config
        self.server = server
        self.config_manager = config_manager
        self.logger = logging.getLogger(__name__)
        self.pitch_shift_semitones = 0
        self.pipeline = None
        self.current_file = None
        self.eos_callback = None  # Callback for end-of-stream
        # Audio mixing elements (stored for volume control)
        self.mic_dry_volume_element = None
        self.mic_reverb_volume_element = None  # Controls reverb send level (reverb effect only)
        self.backing_track_volume_element = None
        self.reverb_element = None
        # Initialize GStreamer but defer pipeline creation until needed
        # On macOS, GStreamer init can hang/crash, so we'll initialize lazily
        self._gst_initialized = False
        self.logger.info('StreamingController initialized (GStreamer will be initialized on demand)')
    
    def _ensure_gst_initialized(self):
        """Initialize GStreamer if not already done."""
        Gst = _get_gst()  # Import on first use
        
        if self._gst_initialized:
            return
        
        if not Gst.is_initialized():
            self.logger.info('Initializing GStreamer...')
            try:
                import sys
                # Use minimal initialization flags to reduce crash risk
                argv = [
                    'kbox',
                    '--gst-disable-segtrap',
                    '--gst-disable-registry-fork',
                    '--gst-disable-registry-update',
                ]
                # On macOS, try to initialize with even more conservative settings
                if sys.platform == 'darwin':
                    # Set environment variables to reduce plugin scanning
                    import os
                    os.environ.setdefault('GST_PLUGIN_SCANNER', '')
                    os.environ.setdefault('GST_REGISTRY_FORK', 'no')
                    # Ensure LADSPA path is set if not already set
                    if 'LADSPA_PATH' not in os.environ:
                        ladspa_path = os.path.expanduser('~/.ladspa')
                        if os.path.exists(ladspa_path):
                            os.environ['LADSPA_PATH'] = ladspa_path
                
                Gst.init(argv)
                self.logger.info('GStreamer initialized successfully')
            except Exception as e:
                self.logger.error('Failed to initialize GStreamer: %s', e, exc_info=True)
                # On macOS, if init fails, we might still be able to use playbin
                if sys.platform == 'darwin':
                    self.logger.warning('GStreamer init had issues, but continuing anyway')
                else:
                    raise
        self._gst_initialized = True
    
    def _create_pipeline(self):
        self._ensure_gst_initialized()
        
        self.logger.info('Creating pipeline...')
        Gst = _get_gst()
        pipeline = Gst.Pipeline.new('StreamingController')

        self.logger.info('Creating source element: %s', self.config.GSTREAMER_SOURCE)
        source = self.make_element(self.config.GSTREAMER_SOURCE, 'source')
        self.set_device(source, self.config.video_input)
        pipeline.add(source)

        #video_demux = self.make_element('matroskademux', 'demux')
        #pipeline.add(video_demux)

        decode = self.make_element('decodebin', 'decode')
        pipeline.add(decode)


        convert_audio_input = self.make_element('audioconvert', 'convert_audio_input')
        pipeline.add(convert_audio_input)

        # Try to add pitch shift, but make it optional
        use_pitch_shift = False
        pitch_shift = None
        try:
            pitch_shift = self.make_element(self.config.RUBBERBAND_PLUGIN, 'pitch_shift')
            if pitch_shift:
                # Check if this is actually a pitch shift element (not an identity fallback)
                element_type = type(pitch_shift).__name__
                if element_type == 'GstIdentity':
                    self.logger.warning('Pitch shift plugin not found in _create_pipeline, using identity (passthrough)')
                    pitch_shift = None
                elif hasattr(pitch_shift, 'set_property'):
                    # Check if the element has the semitones property
                    try:
                        pitch_shift.set_property('semitones', self.pitch_shift_semitones)
                        pipeline.add(pitch_shift)
                        use_pitch_shift = True
                        self.logger.info('Pitch shift enabled in _create_pipeline')
                    except Exception as prop_error:
                        self.logger.warning('Pitch shift element does not support semitones property: %s. Element type: %s', prop_error, element_type)
                        pitch_shift = None
                else:
                    self.logger.warning('Pitch shift element created but not usable (no set_property)')
        except Exception as e:
            self.logger.warning('Could not create pitch shift element in _create_pipeline: %s. Continuing without pitch shift.', e)

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
                if use_pitch_shift and pitch_shift:
                    pad.link(convert_audio_input.get_static_pad('sink'))
                else:
                    pad.link(convert_audio_input.get_static_pad('sink'))
            elif string.startswith('video/x-raw'):
                pad.link(video_sink.get_static_pad('sink'))

        decode.connect("pad-added", decodebin_pad_added)

        source.link(decode)
        
        # Link audio pipeline - handle optional pitch shift
        if use_pitch_shift and pitch_shift:
            convert_audio_input.link(pitch_shift)
            pitch_shift.link(convert_audio_output)
        else:
            convert_audio_input.link(convert_audio_output)
        
        convert_audio_output.link(audio_sink)


        #decode.link(audio_sink)

        return pipeline
    
    def make_element(self, element_type, name):
        import os
        Gst = _get_gst()
        element = Gst.ElementFactory.make(element_type, name)
        if element is None:
            # Try to find alternative elements
            if element_type == self.config.RUBBERBAND_PLUGIN:
                self.logger.warning('Rubberband plugin "%s" not found, pitch shifting will be disabled', element_type)
                self.logger.info('LADSPA_PATH is: %s', os.environ.get('LADSPA_PATH', 'not set'))
                # Return a passthrough element instead
                return self.make_element('identity', name)
            raise ValueError('Unable to initialize gstreamer element %s as %s. Available plugins may be missing.' % (element_type, name))
        return element
    
    def _get_mic_source_type(self):
        """Get the appropriate mic source element type for the current platform."""
        if self.config_manager:
            source = self.config_manager.get('audio_input_source')
            if source and source != '':
                return source
        
        # Platform defaults
        if sys.platform == 'darwin':
            # Prefer avfaudiosrc for better latency on macOS
            return 'avfaudiosrc'
        elif sys.platform == 'linux':
            # Try pipewiresrc first (modern), fallback to alsasrc
            return 'pipewiresrc'
        else:
            return 'autoaudiosrc'
    
    def _get_audio_sink_type(self):
        """Get the appropriate audio sink element type for the current platform."""
        if self.config_manager:
            sink = self.config_manager.get('audio_output_sink')
            if sink and sink != '':
                return sink
        
        # Platform defaults
        if sys.platform == 'darwin':
            return 'osxaudiosink'
        elif sys.platform == 'linux':
            # Try pipewiresink first (modern), fallback to alsasink
            return 'pipewiresink'
        else:
            return 'autoaudiosink'
    
    def _get_sample_rate(self):
        """Get configured sample rate, default 48000 Hz."""
        if self.config_manager:
            return self.config_manager.get_int('audio_sample_rate', 48000)
        return 48000
    
    def _get_latency_ns(self):
        """Get configured latency in nanoseconds."""
        latency_ms = 10
        if self.config_manager:
            latency_ms = self.config_manager.get_int('audio_latency_ms', 10)
        return latency_ms * 1000000  # Convert ms to nanoseconds
    
    def _get_latency_us(self):
        """Get configured latency in microseconds (for ALSA)."""
        latency_ms = 10
        if self.config_manager:
            latency_ms = self.config_manager.get_int('audio_latency_ms', 10)
        return latency_ms * 1000  # Convert ms to microseconds
    
    def _is_hardware_monitor_mode(self):
        """Check if hardware monitor mode is enabled."""
        if self.config_manager:
            return self.config_manager.get_bool('hardware_monitor_mode', False)
        return False
    
    def set_device(self, element, device):
        if device is None:
            return
        
        element_type = type(element).__name__
        if element_type in ('GstAlsaSrc', 'GstAlsaSink', 'GstPipewireSrc', 'GstPipewireSink', 
                           'GstAvfAudioSrc', 'GstOsxAudioSink'):
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
        self.pitch_shift_semitones = semitones
        
        # Try to get pitch shift element from pipeline
        if self.pipeline:
            try:
                pitch_shift = self.pipeline.get_by_name('pitch_shift')
                if pitch_shift:
                    pitch_shift.set_property('semitones', semitones)
                    self.logger.info('Pitch shift updated in pipeline')
                else:
                    self.logger.warning('Pitch shift element not found in pipeline - may not be supported')
            except Exception as e:
                self.logger.warning('Could not update pitch shift: %s', e)
    
    def set_mic_dry_level(self, level: float):
        """
        Set dry mic level (original signal, no effects).
        
        Args:
            level: Level (0.0 to 1.0)
        """
        if level < 0.0 or level > 1.0:
            self.logger.warning('Mic dry level out of range: %s (clamping to 0.0-1.0)', level)
            level = max(0.0, min(1.0, level))
        
        self.logger.info('Setting mic dry level to %s', level)
        
        # Update stored element if available
        if self.mic_dry_volume_element:
            try:
                self.mic_dry_volume_element.set_property('volume', level)
                self.logger.info('Mic dry level updated in pipeline')
            except Exception as e:
                self.logger.warning('Could not update mic dry level: %s', e)
        else:
            # Try to get from pipeline
            if self.pipeline:
                try:
                    mic_dry_volume = self.pipeline.get_by_name('mic_dry_volume')
                    if mic_dry_volume:
                        mic_dry_volume.set_property('volume', level)
                        self.mic_dry_volume_element = mic_dry_volume
                        self.logger.info('Mic dry level updated in pipeline')
                    else:
                        self.logger.warning('Mic dry volume element not found in pipeline')
                except Exception as e:
                    self.logger.warning('Could not update mic dry level: %s', e)
        
        # Update config if config_manager is available
        if self.config_manager:
            self.config_manager.set('default_mic_dry_level', str(level))
    
    def set_mic_reverb_level(self, level: float):
        """
        Set reverb send level (reverb effect only, no original signal).
        
        Args:
            level: Level (0.0 to 1.0)
        """
        if level < 0.0 or level > 1.0:
            self.logger.warning('Mic reverb level out of range: %s (clamping to 0.0-1.0)', level)
            level = max(0.0, min(1.0, level))
        
        self.logger.info('Setting mic reverb send level to %s', level)
        
        # Update stored element if available
        if self.mic_reverb_volume_element:
            try:
                self.mic_reverb_volume_element.set_property('volume', level)
                self.logger.info('Mic reverb send level updated in pipeline')
            except Exception as e:
                self.logger.warning('Could not update mic reverb send level: %s', e)
        else:
            # Try to get from pipeline
            if self.pipeline:
                try:
                    mic_reverb_volume = self.pipeline.get_by_name('mic_reverb_volume')
                    if mic_reverb_volume:
                        mic_reverb_volume.set_property('volume', level)
                        self.mic_reverb_volume_element = mic_reverb_volume
                        self.logger.info('Mic reverb send level updated in pipeline')
                    else:
                        self.logger.warning('Mic reverb volume element not found in pipeline')
                except Exception as e:
                    self.logger.warning('Could not update mic reverb send level: %s', e)
        
        # Update config if config_manager is available
        if self.config_manager:
            self.config_manager.set('default_mic_reverb_level', str(level))
    
    def set_backing_track_volume(self, volume: float):
        """
        Set backing track audio volume.
        
        Args:
            volume: Volume level (0.0 to 1.0)
        """
        if volume < 0.0 or volume > 1.0:
            self.logger.warning('Backing track volume out of range: %s (clamping to 0.0-1.0)', volume)
            volume = max(0.0, min(1.0, volume))
        
        self.logger.info('Setting backing track volume to %s', volume)
        
        # Update stored element if available
        if self.backing_track_volume_element:
            try:
                self.backing_track_volume_element.set_property('volume', volume)
                self.logger.info('Backing track volume updated in pipeline')
            except Exception as e:
                self.logger.warning('Could not update backing track volume: %s', e)
        else:
            # Try to get from pipeline
            if self.pipeline:
                try:
                    backing_track_volume = self.pipeline.get_by_name('backing_track_volume')
                    if backing_track_volume:
                        backing_track_volume.set_property('volume', volume)
                        self.backing_track_volume_element = backing_track_volume
                        self.logger.info('Backing track volume updated in pipeline')
                    else:
                        self.logger.warning('Backing track volume element not found in pipeline')
                except Exception as e:
                    self.logger.warning('Could not update backing track volume: %s', e)
        
        # Update config if config_manager is available
        if self.config_manager:
            self.config_manager.set('default_backing_track_volume', str(volume))
    
    def set_reverb_params(self, room_size: Optional[float] = None, damping: Optional[float] = None, 
                        width: Optional[float] = None, level: Optional[float] = None):
        """
        Set reverb parameters (freeverb-specific).
        
        Args:
            room_size: Room size (0.0-1.0, default: 0.5)
            damping: Damping of high frequencies (0.0-1.0, default: 0.2)
            width: Stereo panorama width (0.0-1.0, default: 1.0)
            level: dry/wet level (0.0-1.0, default: 1.0 for reverb-only)
                   Note: Should be 1.0 for reverb-only output (no dry signal)
        """
        if not self.reverb_element:
            self.logger.warning('No reverb element available')
            return
        
        try:
            # freeverb properties
            if room_size is not None:
                self.reverb_element.set_property('room-size', room_size)
            
            if damping is not None:
                self.reverb_element.set_property('damping', damping)
            
            if width is not None:
                self.reverb_element.set_property('width', width)
            
            if level is not None:
                # Warn if level is not 1.0 (we want reverb-only output)
                if level != 1.0:
                    self.logger.warning('Reverb level set to %s (expected 1.0 for reverb-only output)', level)
                self.reverb_element.set_property('level', level)
            
            self.logger.info('Updated reverb parameters: room_size=%s, damping=%s, width=%s, level=%s',
                           room_size, damping, width, level)
        except Exception as e:
            self.logger.error('Could not set reverb parameters: %s', e)
            raise

    def run(self):
        """Run the streaming controller (start pipeline if not already started)."""
        try:
            if self.pipeline is None:
                self.logger.info('Creating pipeline for first time...')
                self._create_pipeline()
            
            self.logger.debug('Starting gstreamer pipeline...')
        except Exception as e:
            self.logger.error('Failed to create/start pipeline: %s', e, exc_info=True)
            self.logger.warning('Streaming controller will not function, but server will continue')
            return
        Gst = _get_gst()
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
        Gst = _get_gst()  # Get Gst for message types
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
                if self.eos_callback:
                    self.eos_callback()
                break
            else:
                self.logger.error('Unhandled message: %s', msg.type)
                break
        self.logger.debug('AudioController.run() exiting')
    
    def load_file(self, filepath: str):
        """
        Load a video file for backing track playback mode.
        
        Args:
            filepath: Path to video file
        """
        self.logger.info('Loading file: %s', filepath)
        
        try:
            # Stop current pipeline if running
            if self.pipeline:
                try:
                    Gst = _get_gst()
                    self.pipeline.set_state(Gst.State.NULL)
                except Exception as e:
                    self.logger.warning('Error stopping previous pipeline: %s', e)
            
            self.current_file = filepath
            self._create_backing_track_pipeline(filepath)
            
            # Start playback
            Gst = _get_gst()
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                self.logger.error('Failed to start backing track playback')
                raise RuntimeError('Failed to start playback')
        except Exception as e:
            self.logger.error('Error loading file %s: %s', filepath, e, exc_info=True)
            # Don't crash - just log the error
            raise
    
    def _create_backing_track_pipeline(self, filepath: str):
        """
        Create pipeline for backing track file playback with optional audio mixing.
        
        Signal flow:
        - Mic source → tee → [dry branch] + [reverb branch]
        - Backing track → volume
        - All branches → audiomixer → audioconvert → audioresample → sink
        """
        self._ensure_gst_initialized()
        
        self.logger.info('Creating backing track playback pipeline for: %s', filepath)
        Gst = _get_gst()
        pipeline = Gst.Pipeline.new('BackingTrackPlayback')
        
        # Get configuration
        sample_rate = self._get_sample_rate()
        latency_ns = self._get_latency_ns()
        latency_us = self._get_latency_us()
        hardware_monitor = self._is_hardware_monitor_mode()
        
        # Get audio input source configuration
        # Audio mixing is OFF by default - must be explicitly enabled via configuration
        audio_input_source = None
        audio_input_device = None
        if self.config_manager:
            audio_input_source = self.config_manager.get('audio_input_source')
            audio_input_device = self.config_manager.get('audio_input_source_device')
        
        # Only enable mixing if explicitly configured (not None and not empty string)
        use_audio_mixing = audio_input_source is not None and audio_input_source != ''
        
        # Audio caps: 48kHz, F32LE, stereo (or match input)
        audio_caps_string = f'audio/x-raw, format=F32LE, rate={sample_rate}, channels=2'
        audio_caps = Gst.Caps.from_string(audio_caps_string)
        
        # File source
        filesrc = self.make_element('filesrc', 'filesrc')
        filesrc.set_property('location', filepath)
        pipeline.add(filesrc)
        
        # Decodebin for audio/video
        decodebin = self.make_element('decodebin', 'decodebin')
        pipeline.add(decodebin)
        
        # Backing track audio pipeline
        backing_track_audioconvert_input = self.make_element('audioconvert', 'backing_track_audioconvert_input')
        pipeline.add(backing_track_audioconvert_input)
        
        # Backing track volume control
        backing_track_volume = self.make_element('volume', 'backing_track_volume')
        if self.config_manager:
            default_backing_track_volume = self.config_manager.get_float('default_backing_track_volume', 0.8)
            backing_track_volume.set_property('volume', default_backing_track_volume)
        else:
            backing_track_volume.set_property('volume', 0.8)
        pipeline.add(backing_track_volume)
        self.backing_track_volume_element = backing_track_volume
        
        # Try to add pitch shift, but make it optional
        use_pitch_shift = False
        pitch_shift = None
        try:
            pitch_shift = self.make_element(self.config.RUBBERBAND_PLUGIN, 'pitch_shift')
            if pitch_shift:
                # Check if this is actually a pitch shift element (not an identity fallback)
                element_type = type(pitch_shift).__name__
                if element_type == 'GstIdentity':
                    self.logger.warning('Pitch shift plugin not found, using identity (passthrough)')
                    pitch_shift = None
                elif hasattr(pitch_shift, 'set_property'):
                    # Check if the element has the semitones property
                    try:
                        pitch_shift.set_property('semitones', self.pitch_shift_semitones)
                        pipeline.add(pitch_shift)
                        use_pitch_shift = True
                        self.logger.info('Pitch shift enabled')
                    except Exception as prop_error:
                        self.logger.warning('Pitch shift element does not support semitones property: %s. Element type: %s', prop_error, element_type)
                        pitch_shift = None
                else:
                    self.logger.warning('Pitch shift element created but not usable (no set_property)')
            else:
                self.logger.warning('Could not create pitch shift element')
        except Exception as e:
            self.logger.warning('Could not create pitch shift element: %s. Continuing without pitch shift.', e)
        
        backing_track_audioconvert_output = self.make_element('audioconvert', 'backing_track_audioconvert_output')
        pipeline.add(backing_track_audioconvert_output)
        
        # Audio mixing setup
        if use_audio_mixing:
            self.logger.info('Audio mixing enabled with source: %s (hardware monitor: %s)', 
                           audio_input_source, hardware_monitor)
            
            # Get mic source type (platform-specific)
            mic_source_type = self._get_mic_source_type()
            
            # Microphone input source
            mic_source = self.make_element(mic_source_type, 'mic_source')
            if audio_input_device:
                self.set_device(mic_source, audio_input_device)
            
            # Configure low latency for audio source
            try:
                if sys.platform == 'darwin':
                    # avfaudiosrc/osxaudiosrc: buffer-time in nanoseconds
                    mic_source.set_property('buffer-time', latency_ns)
                    self.logger.info('Set mic source buffer-time to %d ns', latency_ns)
                elif sys.platform == 'linux':
                    # alsasrc/pipewiresrc: buffer-time and latency-time in microseconds
                    mic_source.set_property('buffer-time', latency_us)
                    mic_source.set_property('latency-time', latency_us)
                    self.logger.info('Set mic source buffer-time and latency-time to %d us', latency_us)
            except Exception as e:
                self.logger.warning('Could not set low latency properties on mic source: %s', e)
            
            pipeline.add(mic_source)
            
            # Mic: convert → resample → tee (splits into dry and reverb branches)
            mic_audioconvert = self.make_element('audioconvert', 'mic_audioconvert')
            pipeline.add(mic_audioconvert)
            
            mic_audioresample = self.make_element('audioresample', 'mic_audioresample')
            pipeline.add(mic_audioresample)
            
            mic_tee = self.make_element('tee', 'mic_tee')
            pipeline.add(mic_tee)
            
            # Link mic source chain
            mic_source.link(mic_audioconvert)
            mic_audioconvert.link(mic_audioresample)
            mic_audioresample.link(mic_tee)
            
            # === DRY BRANCH (original mic signal) ===
            # Only include if hardware monitor mode is disabled
            if not hardware_monitor:
                mic_dry_queue = self.make_element('queue', 'mic_dry_queue')
                # Small queue for low latency: ~10ms max
                mic_dry_queue.set_property('max-size-buffers', 0)
                mic_dry_queue.set_property('max-size-bytes', 0)
                mic_dry_queue.set_property('max-size-time', latency_ns)
                pipeline.add(mic_dry_queue)
                
                mic_dry_audioconvert = self.make_element('audioconvert', 'mic_dry_audioconvert')
                pipeline.add(mic_dry_audioconvert)
                
                mic_dry_audioresample = self.make_element('audioresample', 'mic_dry_audioresample')
                pipeline.add(mic_dry_audioresample)
                
                mic_dry_volume = self.make_element('volume', 'mic_dry_volume')
                if self.config_manager:
                    default_dry = self.config_manager.get_float('default_mic_dry_level', 0.8)
                    mic_dry_volume.set_property('volume', default_dry)
                else:
                    mic_dry_volume.set_property('volume', 0.8)
                pipeline.add(mic_dry_volume)
                self.mic_dry_volume_element = mic_dry_volume
                
                # Link dry branch
                mic_tee_dry_pad = mic_tee.get_request_pad('src_%u')
                mic_dry_queue_pad = mic_dry_queue.get_static_pad('sink')
                mic_tee_dry_pad.link(mic_dry_queue_pad)
                mic_dry_queue.link(mic_dry_audioconvert)
                mic_dry_audioconvert.link(mic_dry_audioresample)
                mic_dry_audioresample.link(mic_dry_volume)
            else:
                self.logger.info('Hardware monitor mode: dry mic branch disabled')
                self.mic_dry_volume_element = None
            
            # === REVERB BRANCH (reverb effect only) ===
            mic_reverb_queue = self.make_element('queue', 'mic_reverb_queue')
            # Small queue for low latency: ~10ms max
            mic_reverb_queue.set_property('max-size-buffers', 0)
            mic_reverb_queue.set_property('max-size-bytes', 0)
            mic_reverb_queue.set_property('max-size-time', latency_ns)
            pipeline.add(mic_reverb_queue)
            
            mic_reverb_audioconvert = self.make_element('audioconvert', 'mic_reverb_audioconvert')
            pipeline.add(mic_reverb_audioconvert)
            
            mic_reverb_audioresample = self.make_element('audioresample', 'mic_reverb_audioresample')
            pipeline.add(mic_reverb_audioresample)
            
            # Reverb element (outputs reverb effect only, no original signal)
            reverb = None
            reverb_plugin = None
            if self.config_manager:
                reverb_plugin = self.config_manager.get('reverb_plugin')
            
            # Try to create reverb element
            if reverb_plugin:
                try:
                    reverb = self.make_element(reverb_plugin, 'reverb')
                    pipeline.add(reverb)
                    self.reverb_element = reverb
                    self.logger.info('Using configured reverb plugin: %s', reverb_plugin)
                except Exception as e:
                    self.logger.error('Could not create reverb plugin %s: %s', reverb_plugin, e)
                    raise
            
            # Use freeverb (available in gst-plugins-bad)
            if reverb is None:
                try:
                    reverb = self.make_element('freeverb', 'reverb')
                    # Configure freeverb for reverb-only output (no dry signal)
                    # level property: 0.0 = all dry, 1.0 = all wet (reverb only)
                    reverb.set_property('level', 1.0)  # 100% wet = reverb effect only
                    pipeline.add(reverb)
                    self.reverb_element = reverb
                    self.logger.info('Using freeverb reverb element (configured for reverb-only output)')
                except Exception as e:
                    self.logger.error('Could not create freeverb reverb element: %s', e)
                    raise
            
            mic_reverb_volume = self.make_element('volume', 'mic_reverb_volume')
            if self.config_manager:
                default_reverb = self.config_manager.get_float('default_mic_reverb_level', 0.3)
                mic_reverb_volume.set_property('volume', default_reverb)
            else:
                mic_reverb_volume.set_property('volume', 0.3)
            pipeline.add(mic_reverb_volume)
            self.mic_reverb_volume_element = mic_reverb_volume
            
            # Link reverb branch
            mic_tee_reverb_pad = mic_tee.get_request_pad('src_%u')
            mic_reverb_queue_pad = mic_reverb_queue.get_static_pad('sink')
            mic_tee_reverb_pad.link(mic_reverb_queue_pad)
            mic_reverb_queue.link(mic_reverb_audioconvert)
            mic_reverb_audioconvert.link(mic_reverb_audioresample)
            mic_reverb_audioresample.link(reverb)
            reverb.link(mic_reverb_volume)
            
            # === BACKING TRACK PIPELINE ===
            # Add audioresample to backing track
            backing_track_audioresample = self.make_element('audioresample', 'backing_track_audioresample')
            pipeline.add(backing_track_audioresample)
            
            # Link backing track with pitch shift if enabled
            if use_pitch_shift and pitch_shift:
                backing_track_audioconvert_input.link(backing_track_volume)
                backing_track_volume.link(pitch_shift)
                pitch_shift.link(backing_track_audioconvert_output)
                backing_track_audioconvert_output.link(backing_track_audioresample)
            else:
                backing_track_audioconvert_input.link(backing_track_volume)
                backing_track_volume.link(backing_track_audioconvert_output)
                backing_track_audioconvert_output.link(backing_track_audioresample)
            
            # === AUDIO MIXER ===
            audiomixer = self.make_element('audiomixer', 'audiomixer')
            try:
                audiomixer.set_property('latency', latency_ns)
                self.logger.info('Set audiomixer latency to %d ns', latency_ns)
            except Exception as e:
                self.logger.warning('Could not set latency property on audiomixer: %s', e)
            pipeline.add(audiomixer)
            
            # Request mixer sink pads
            mixer_sink_pads = []
            
            # Dry mic branch (if enabled)
            if not hardware_monitor:
                mic_dry_sink_pad = audiomixer.get_request_pad('sink_%u')
                mixer_sink_pads.append(('dry', mic_dry_sink_pad))
                mic_dry_volume.get_static_pad('src').link(mic_dry_sink_pad)
            
            # Reverb branch
            mic_reverb_sink_pad = audiomixer.get_request_pad('sink_%u')
            mixer_sink_pads.append(('reverb', mic_reverb_sink_pad))
            mic_reverb_volume.get_static_pad('src').link(mic_reverb_sink_pad)
            
            # Backing track
            backing_track_sink_pad = audiomixer.get_request_pad('sink_%u')
            mixer_sink_pads.append(('backing', backing_track_sink_pad))
            backing_track_audioresample.get_static_pad('src').link(backing_track_sink_pad)
            
            self.logger.debug('Requested mixer sink pads: %s', 
                            ', '.join([f'{name}={pad.get_name()}' for name, pad in mixer_sink_pads]))
            
            # === FINAL OUTPUT ===
            mixer_audioconvert = self.make_element('audioconvert', 'mixer_audioconvert')
            pipeline.add(mixer_audioconvert)
            
            mixer_audioresample = self.make_element('audioresample', 'mixer_audioresample')
            pipeline.add(mixer_audioresample)
            
            # Audio sink
            audio_sink_type = self._get_audio_sink_type()
            audio_sink = self.make_element(audio_sink_type, 'audio_sink')
            
            # Get output device
            audio_output_device = None
            if self.config_manager:
                audio_output_device = self.config_manager.get('audio_output_device')
            if audio_output_device:
                self.set_device(audio_sink, audio_output_device)
            
            # Configure low latency for audio sink
            try:
                if sys.platform == 'darwin':
                    audio_sink.set_property('buffer-time', latency_ns)
                elif sys.platform == 'linux':
                    audio_sink.set_property('buffer-time', latency_us)
                    audio_sink.set_property('latency-time', latency_us)
                self.logger.info('Set audio sink latency to %d ms', latency_ns // 1000000)
            except Exception as e:
                self.logger.warning('Could not set low latency properties on audio sink: %s', e)
            
            pipeline.add(audio_sink)
            
            # Link mixer output
            audiomixer.get_static_pad('src').link(mixer_audioconvert.get_static_pad('sink'))
            mixer_audioconvert.link(mixer_audioresample)
            mixer_audioresample.link(audio_sink)
        else:
            self.logger.info('Audio mixing disabled - backing track audio only')
            
            # Add audioresample to backing track
            backing_track_audioresample = self.make_element('audioresample', 'backing_track_audioresample')
            pipeline.add(backing_track_audioresample)
            
            # Link backing track - handle optional pitch shift
            if use_pitch_shift and pitch_shift:
                backing_track_audioconvert_input.link(backing_track_volume)
                backing_track_volume.link(pitch_shift)
                pitch_shift.link(backing_track_audioconvert_output)
                backing_track_audioconvert_output.link(backing_track_audioresample)
            else:
                backing_track_audioconvert_input.link(backing_track_volume)
                backing_track_volume.link(backing_track_audioconvert_output)
                backing_track_audioconvert_output.link(backing_track_audioresample)
            
            # Audio sink
            audio_sink_type = self._get_audio_sink_type()
            audio_sink = self.make_element(audio_sink_type, 'audio_sink')
            
            # Get output device
            audio_output_device = None
            if self.config_manager:
                audio_output_device = self.config_manager.get('audio_output_device')
            if audio_output_device:
                self.set_device(audio_sink, audio_output_device)
            
            # Configure low latency for audio sink
            try:
                if sys.platform == 'darwin':
                    audio_sink.set_property('buffer-time', latency_ns)
                elif sys.platform == 'linux':
                    audio_sink.set_property('buffer-time', latency_us)
                    audio_sink.set_property('latency-time', latency_us)
                self.logger.info('Set audio sink latency to %d ms', latency_ns // 1000000)
            except Exception as e:
                self.logger.warning('Could not set low latency properties on audio sink: %s', e)
            
            pipeline.add(audio_sink)
            
            backing_track_audioresample.link(audio_sink)
        
        # Video pipeline
        videoconvert = self.make_element('videoconvert', 'videoconvert')
        pipeline.add(videoconvert)
        
        videoscale = self.make_element('videoscale', 'videoscale')
        pipeline.add(videoscale)
        
        # Video sink - use kmssink on Linux, autovideosink/osxvideosink on macOS
        if sys.platform == 'linux':
            video_sink = self.make_element('kmssink', 'video_sink')
        else:
            # Try autovideosink first (auto-detects), fallback to osxvideosink
            try:
                video_sink = self.make_element('autovideosink', 'video_sink')
            except:
                try:
                    video_sink = self.make_element('osxvideosink', 'video_sink')
                except:
                    self.logger.warning('No video sink available, using fakesink')
                    video_sink = self.make_element('fakesink', 'video_sink')
        pipeline.add(video_sink)
        
        # Link video pipeline
        videoconvert.link(videoscale)
        videoscale.link(video_sink)
        
        # Handle dynamic pads from decodebin
        def on_pad_added(element, pad):
            caps = pad.query_caps(None)
            caps_string = caps.to_string()
            self.logger.debug('Decodebin pad added: %s', caps_string)
            
            if caps_string.startswith('audio/'):
                # Link to backing track audio input converter
                pad.link(backing_track_audioconvert_input.get_static_pad('sink'))
            elif caps_string.startswith('video/'):
                pad.link(videoconvert.get_static_pad('sink'))
        
        decodebin.connect('pad-added', on_pad_added)
        
        filesrc.link(decodebin)
        
        self.pipeline = pipeline
    
    def _create_simple_macos_pipeline(self, filepath: str):
        """Create a simple pipeline for macOS.
        
        The video sink (osxvideosink/glimagesink) handles NSApplication/NSRunLoop internally.
        We just need to ensure GLib main loop is running (done separately).
        """
        self.logger.info('Creating macOS pipeline')
        
        # Use playbin which is more stable and handles everything internally
        Gst = _get_gst()
        pipeline = Gst.Pipeline.new('BackingTrackPlayback')
        
        # Use playbin3 or playbin - simpler and more stable
        try:
            playbin = self.make_element('playbin', 'playbin')
        except:
            # Fallback to playbin3 if playbin doesn't work
            try:
                playbin = self.make_element('playbin3', 'playbin')
            except:
                self.logger.error('Neither playbin nor playbin3 available')
                raise RuntimeError('No suitable playback element available')
        
        playbin.set_property('uri', f'file://{filepath}')
        
        # Set audio sink
        audio_sink = self.make_element('autoaudiosink', 'audio_sink')
        playbin.set_property('audio-sink', audio_sink)
        
        # Use autovideosink - it will auto-select the best sink (osxvideosink/glimagesink)
        # The sink handles NSApplication/NSRunLoop internally
        video_sink = self.make_element('autovideosink', 'video_sink')
        playbin.set_property('video-sink', video_sink)
        self.logger.info('Video sink configured: autovideosink (will auto-select best sink for macOS)')
        
        # Note: playbin doesn't support pitch shifting directly
        # For macOS dev, we'll skip pitch shifting to get playback working
        self.logger.warning('Pitch shifting disabled on macOS (using playbin for stability)')
        
        pipeline.add(playbin)
        self.pipeline = pipeline
        
        # Set up EOS callback with signal watch (requires GLib main loop)
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::eos', self._on_eos)
        bus.connect('message::error', self._on_error)
        
        # Start GLib main loop for signal callbacks (if not already running)
        if not hasattr(self.__class__, '_glib_loop_thread') or not self.__class__._glib_loop_thread.is_alive():
            import threading
            try:
                from gi.repository import GLib
                
                def run_glib_loop():
                    """Run GLib main loop for GStreamer signal callbacks."""
                    try:
                        self.logger.info('Starting GLib main loop for GStreamer events')
                        loop = GLib.MainLoop()
                        loop.run()
                    except Exception as e:
                        self.logger.error('Error in GLib main loop: %s', e, exc_info=True)
                
                glib_thread = threading.Thread(target=run_glib_loop, daemon=True, name='GLibMainLoop')
                glib_thread.start()
                self.__class__._glib_loop_thread = glib_thread
                self.logger.info('GLib main loop started for GStreamer signal callbacks')
            except ImportError:
                self.logger.warning('GLib not available for main loop - signal callbacks may not work')
        
        return pipeline
    
    def _on_eos(self, bus, message):
        """Handle end-of-stream message."""
        self.logger.info('End of stream reached')
        if self.eos_callback:
            self.eos_callback()
    
    def _on_error(self, bus, message):
        """Handle error message."""
        err, debug = message.parse_error()
        self.logger.error('GStreamer error: %s: %s', err, debug)
    
    def pause(self):
        """Pause playback."""
        if not self.pipeline:
            self.logger.warning('Cannot pause: no pipeline')
            raise RuntimeError('No active pipeline to pause')
        
        try:
            Gst = _get_gst()
            ret = self.pipeline.set_state(Gst.State.PAUSED)
            if ret == Gst.StateChangeReturn.FAILURE:
                self.logger.error('Failed to pause pipeline')
                raise RuntimeError('Failed to pause playback')
            self.logger.info('Playback paused')
        except Exception as e:
            self.logger.error('Error pausing playback: %s', e, exc_info=True)
            raise
    
    def resume(self):
        """Resume playback."""
        if not self.pipeline:
            self.logger.warning('Cannot resume: no pipeline')
            raise RuntimeError('No active pipeline to resume')
        
        try:
            Gst = _get_gst()
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                self.logger.error('Failed to resume pipeline')
                raise RuntimeError('Failed to resume playback')
            self.logger.info('Playback resumed')
        except Exception as e:
            self.logger.error('Error resuming playback: %s', e, exc_info=True)
            raise
    
    def set_eos_callback(self, callback):
        """Set callback for end-of-stream events."""
        self.eos_callback = callback
    
    def get_position(self) -> Optional[int]:
        """
        Get current playback position in seconds.
        
        Returns:
            Position in seconds, or None if not available
        """
        if not self.pipeline:
            return None
        
        try:
            Gst = _get_gst()
            success, position = self.pipeline.query_position(Gst.Format.TIME)
            if success:
                # Convert nanoseconds to seconds
                return position // Gst.SECOND
            return None
        except Exception as e:
            self.logger.warning('Could not get playback position: %s', e)
            return None
    
    def seek(self, position_seconds: int) -> bool:
        """
        Seek to a specific position in the stream.
        
        Args:
            position_seconds: Position to seek to in seconds
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pipeline:
            self.logger.warning('Cannot seek: no pipeline')
            return False
        
        try:
            Gst = _get_gst()
            # Convert seconds to nanoseconds
            position_ns = position_seconds * Gst.SECOND
            success = self.pipeline.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                position_ns
            )
            if success:
                self.logger.info('Seeked to position: %s seconds', position_seconds)
            else:
                self.logger.warning('Seek failed')
            return success
        except Exception as e:
            self.logger.error('Error seeking: %s', e, exc_info=True)
            return False
    
    def stop(self):
        """Stop the streaming controller and cleanup resources."""
        self.logger.info('Stopping streaming controller...')
        
        # Stop pipeline
        if self.pipeline:
            try:
                Gst = _get_gst()
                result = self.pipeline.set_state(Gst.State.NULL)
                if result == Gst.StateChangeReturn.SUCCESS:
                    self.logger.debug('Pipeline state changed successfully')
                elif result == Gst.StateChangeReturn.FAILURE:
                    self.logger.warning('Failed to change pipeline state to NULL')
                else:
                    self.logger.debug('Pipeline state change returned: %s', result)
                self.pipeline = None
            except Exception as e:
                self.logger.error('Error stopping pipeline: %s', e, exc_info=True)
        
        # Try to stop GLib main loop if it exists
        if hasattr(self.__class__, '_glib_loop_thread'):
            glib_thread = self.__class__._glib_loop_thread
            if glib_thread and glib_thread.is_alive():
                try:
                    from gi.repository import GLib
                    # Get the main loop and quit it
                    # Note: This is tricky since the loop is in another thread
                    # The daemon thread will exit when main thread exits
                    self.logger.debug('GLib main loop thread is daemon, will exit with main thread')
                except ImportError:
                    pass
        
        self.logger.info('Streaming controller stopped')
