"""
GStreamer-based streaming controller for audio/video playback.

Handles YouTube video playback with optional pitch shifting via rubberband.
"""

import logging
import sys
from typing import Optional, Tuple

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
    """Controls GStreamer pipelines for audio/video playback."""
    
    def __init__(self, config_manager, server):
        self.config_manager = config_manager
        self.server = server
        self.logger = logging.getLogger(__name__)
        self.pitch_shift_semitones = 0
        self.pipeline = None
        self.mode = 'youtube'
        self.current_file = None
        self.eos_callback = None
        self._gst_initialized = False
        self.logger.info('StreamingController initialized (GStreamer will be initialized on demand)')
    
    # =========================================================================
    # GStreamer Initialization
    # =========================================================================
    
    def _ensure_gst_initialized(self):
        """Initialize GStreamer if not already done."""
        Gst = _get_gst()
        
        if self._gst_initialized:
            return
        
        if not Gst.is_initialized():
            self.logger.info('Initializing GStreamer...')
            try:
                argv = [
                    'kbox',
                    '--gst-disable-segtrap',
                    '--gst-disable-registry-fork',
                    '--gst-disable-registry-update',
                ]
                if sys.platform == 'darwin':
                    import os
                    os.environ.setdefault('GST_PLUGIN_SCANNER', '')
                    os.environ.setdefault('GST_REGISTRY_FORK', 'no')
                    if 'LADSPA_PATH' not in os.environ:
                        ladspa_path = os.path.expanduser('~/.ladspa')
                        if os.path.exists(ladspa_path):
                            os.environ['LADSPA_PATH'] = ladspa_path
                
                Gst.init(argv)
                self.logger.info('GStreamer initialized successfully')
            except Exception as e:
                self.logger.error('Failed to initialize GStreamer: %s', e, exc_info=True)
                if sys.platform == 'darwin':
                    self.logger.warning('GStreamer init had issues, but continuing anyway')
                else:
                    raise
        self._gst_initialized = True
    
    # =========================================================================
    # Element Creation Helpers
    # =========================================================================
    
    def _make_element(self, element_type: str, name: str):
        """Create a GStreamer element, with fallback for rubberband plugin."""
        import os
        Gst = _get_gst()
        element = Gst.ElementFactory.make(element_type, name)
        
        if element is None:
            rubberband_plugin = self.config_manager.get('rubberband_plugin')
            if element_type == rubberband_plugin:
                self.logger.warning(
                    'Rubberband plugin "%s" not found, pitch shifting will be disabled. '
                    'LADSPA_PATH is: %s', element_type, os.environ.get('LADSPA_PATH', 'not set')
                )
                return Gst.ElementFactory.make('identity', name)
            raise ValueError(f'Unable to create GStreamer element {element_type} as {name}')
        return element
    
    # Legacy alias for compatibility
    make_element = _make_element
    
    def _set_device(self, element, device: Optional[str]):
        """Set device property on an element if device is specified."""
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
            raise NotImplementedError(f'set_device not implemented for {element_type}')
    
    # Legacy alias for compatibility
    set_device = _set_device
    
    def _create_pitch_shift_element(self, pipeline) -> Tuple[Optional[object], bool]:
        """
        Create and configure a pitch shift element.
        
        Returns:
            Tuple of (pitch_shift_element, use_pitch_shift_flag)
            If pitch shift unavailable, returns (None, False)
        """
        try:
            rubberband_plugin = self.config_manager.get('rubberband_plugin')
            if not rubberband_plugin:
                self.logger.warning('No rubberband plugin configured')
                return None, False
            
            pitch_shift = self._make_element(rubberband_plugin, 'pitch_shift')
            if pitch_shift is None:
                return None, False
            
            # Check if this is actually a pitch shift element (not identity fallback)
            element_type = type(pitch_shift).__name__
            if element_type == 'GstIdentity':
                self.logger.warning('Pitch shift plugin not found, using identity (passthrough)')
                return None, False
            
            if not hasattr(pitch_shift, 'set_property'):
                self.logger.warning('Pitch shift element created but not usable (no set_property)')
                return None, False
            
            # Try to set the semitones property
            try:
                pitch_shift.set_property('semitones', self.pitch_shift_semitones)
                pipeline.add(pitch_shift)
                self.logger.info('Pitch shift enabled')
                return pitch_shift, True
            except Exception as prop_error:
                self.logger.warning(
                    'Pitch shift element does not support semitones property: %s. Element type: %s',
                    prop_error, element_type
                )
                return None, False
                
        except Exception as e:
            self.logger.warning('Could not create pitch shift element: %s. Continuing without pitch shift.', e)
            return None, False
    
    def _create_audio_sink(self, pipeline) -> object:
        """Create and configure the audio sink element."""
        gstreamer_sink = self.config_manager.get('gstreamer_sink')
        audio_sink = self._make_element(gstreamer_sink, 'audio_sink')
        audio_output = self.config_manager.get('audio_output_device')
        if audio_output:
            self._set_device(audio_sink, audio_output)
        pipeline.add(audio_sink)
        return audio_sink
    
    def _create_video_sink(self, pipeline) -> object:
        """Create and configure the video sink element."""
        if sys.platform == 'linux':
            video_sink = self._make_element('kmssink', 'video_sink')
        else:
            # Try autovideosink first, fallback to osxvideosink, then fakesink
            try:
                video_sink = self._make_element('autovideosink', 'video_sink')
            except ValueError:
                try:
                    video_sink = self._make_element('osxvideosink', 'video_sink')
                except ValueError:
                    self.logger.warning('No video sink available, using fakesink')
                    video_sink = self._make_element('fakesink', 'video_sink')
        pipeline.add(video_sink)
        return video_sink
    
    # =========================================================================
    # Pipeline Creation
    # =========================================================================
    
    def _create_youtube_pipeline(self, filepath: str):
        """Create pipeline for YouTube file playback with audio/video."""
        self._ensure_gst_initialized()
        self.logger.info('Creating YouTube playback pipeline for: %s', filepath)
        
        Gst = _get_gst()
        pipeline = Gst.Pipeline.new('YouTubePlayback')
        
        # File source and decoder
        filesrc = self._make_element('filesrc', 'filesrc')
        filesrc.set_property('location', filepath)
        pipeline.add(filesrc)
        
        decodebin = self._make_element('decodebin', 'decodebin')
        pipeline.add(decodebin)
        
        # Audio pipeline: audioconvert -> [pitch_shift] -> audioconvert -> audio_sink
        audioconvert_input = self._make_element('audioconvert', 'audioconvert_input')
        pipeline.add(audioconvert_input)
        
        pitch_shift, use_pitch_shift = self._create_pitch_shift_element(pipeline)
        
        audioconvert_output = self._make_element('audioconvert', 'audioconvert_output')
        pipeline.add(audioconvert_output)
        
        audio_sink = self._create_audio_sink(pipeline)
        
        # Video pipeline: videoconvert -> videoscale -> video_sink
        videoconvert = self._make_element('videoconvert', 'videoconvert')
        pipeline.add(videoconvert)
        
        videoscale = self._make_element('videoscale', 'videoscale')
        pipeline.add(videoscale)
        
        video_sink = self._create_video_sink(pipeline)
        
        # Link static audio elements
        if use_pitch_shift and pitch_shift:
            audioconvert_input.link(pitch_shift)
            pitch_shift.link(audioconvert_output)
        else:
            audioconvert_input.link(audioconvert_output)
        audioconvert_output.link(audio_sink)
        
        # Link static video elements
        videoconvert.link(videoscale)
        videoscale.link(video_sink)
        
        # Handle dynamic pads from decodebin
        def on_pad_added(element, pad):
            caps = pad.query_caps(None)
            caps_string = caps.to_string()
            self.logger.info('Decodebin pad added: %s', caps_string)
            
            if caps_string.startswith('audio/'):
                self.logger.info('Linking audio pad to audioconvert_input')
                sink_pad = audioconvert_input.get_static_pad('sink')
                ret = pad.link(sink_pad)
                if ret == Gst.PadLinkReturn.OK:
                    self.logger.info('Audio pad linked successfully')
                else:
                    self.logger.error('Failed to link audio pad: %s', ret)
            elif caps_string.startswith('video/'):
                self.logger.info('Linking video pad to videoconvert')
                pad.link(videoconvert.get_static_pad('sink'))
        
        decodebin.connect('pad-added', on_pad_added)
        filesrc.link(decodebin)
        
        self.pipeline = pipeline
    
    # =========================================================================
    # Playback Control
    # =========================================================================
    
    def load_file(self, filepath: str):
        """
        Load a video file for playback.
        
        Args:
            filepath: Path to video file
            
        Raises:
            RuntimeError: If playback fails to start
        """
        self.logger.info('Loading file: %s', filepath)
        
        # Stop current pipeline if running
        if self.pipeline:
            try:
                Gst = _get_gst()
                self.pipeline.set_state(Gst.State.NULL)
            except Exception as e:
                self.logger.warning('Error stopping previous pipeline: %s', e)
        
        self.current_file = filepath
        self.mode = 'youtube'
        self._create_youtube_pipeline(filepath)
        
        # Start playback and wait for async completion
        Gst = _get_gst()
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        
        # Check bus for error or state change messages
        bus = self.pipeline.get_bus()
        error_msg = None
        
        # Wait up to 5 seconds for error or successful state change
        for _ in range(5):
            bus_msg = bus.timed_pop(Gst.SECOND)
            if bus_msg:
                if bus_msg.type == Gst.MessageType.ERROR:
                    err, debug = bus_msg.parse_error()
                    error_msg = f'{err.message}'
                    self.logger.error('GStreamer error: %s', err)
                    self.logger.error('Debug info: %s', debug)
                    break
                elif bus_msg.type == Gst.MessageType.STATE_CHANGED:
                    if bus_msg.src == self.pipeline:
                        old_state, new_state, pending_state = bus_msg.parse_state_changed()
                        self.logger.debug('Pipeline state: %s -> %s', old_state.value_name, new_state.value_name)
                        if new_state == Gst.State.PLAYING:
                            self.logger.info('Pipeline started successfully')
                            return
                        elif new_state == Gst.State.NULL and ret != Gst.StateChangeReturn.FAILURE:
                            error_msg = 'Pipeline returned to NULL state (failed to start)'
                            break
        
        if error_msg:
            raise RuntimeError(f'Failed to start playback: {error_msg}')
        elif ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Failed to start playback (immediate failure)')
    
    def pause(self):
        """Pause playback."""
        if not self.pipeline:
            raise RuntimeError('No active pipeline to pause')
        
        Gst = _get_gst()
        ret = self.pipeline.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Failed to pause playback')
        self.logger.info('Playback paused')
    
    def resume(self):
        """Resume playback."""
        if not self.pipeline:
            raise RuntimeError('No active pipeline to resume')
        
        Gst = _get_gst()
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Failed to resume playback')
        self.logger.info('Playback resumed')
    
    def stop(self):
        """Stop the streaming controller and cleanup resources."""
        self.logger.info('Stopping streaming controller...')
        
        if self.pipeline:
            try:
                Gst = _get_gst()
                result = self.pipeline.set_state(Gst.State.NULL)
                if result == Gst.StateChangeReturn.FAILURE:
                    self.logger.warning('Failed to change pipeline state to NULL')
                self.pipeline = None
            except Exception as e:
                self.logger.error('Error stopping pipeline: %s', e, exc_info=True)
        
        self.logger.info('Streaming controller stopped')
    
    # =========================================================================
    # Pitch Control
    # =========================================================================
    
    def set_pitch_shift(self, semitones: int):
        """Set pitch shift in semitones (updates live if pipeline is running)."""
        if semitones == self.pitch_shift_semitones:
            self.logger.debug('Pitch shift already set to %s semitones', semitones)
            return
        
        self.logger.info('Setting pitch shift to %s semitones', semitones)
        self.pitch_shift_semitones = semitones
        
        if self.pipeline:
            try:
                pitch_shift = self.pipeline.get_by_name('pitch_shift')
                if pitch_shift:
                    pitch_shift.set_property('semitones', semitones)
                    self.logger.info('Pitch shift updated in pipeline')
                else:
                    self.logger.warning('Pitch shift element not found in pipeline')
            except Exception as e:
                self.logger.warning('Could not update pitch shift: %s', e)
    
    # =========================================================================
    # Position and Seeking
    # =========================================================================
    
    def get_position(self) -> Optional[int]:
        """Get current playback position in seconds."""
        if not self.pipeline:
            return None
        
        try:
            Gst = _get_gst()
            success, position = self.pipeline.query_position(Gst.Format.TIME)
            if success:
                return position // Gst.SECOND
            return None
        except Exception as e:
            self.logger.warning('Could not get playback position: %s', e)
            return None
    
    def seek(self, position_seconds: int) -> bool:
        """Seek to a specific position in seconds."""
        if not self.pipeline:
            self.logger.warning('Cannot seek: no pipeline')
            return False
        
        try:
            Gst = _get_gst()
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
    
    # =========================================================================
    # Callbacks
    # =========================================================================
    
    def set_eos_callback(self, callback):
        """Set callback for end-of-stream events."""
        self.eos_callback = callback
    
    def _on_eos(self, bus, message):
        """Handle end-of-stream message."""
        self.logger.info('End of stream reached')
        if self.eos_callback:
            self.eos_callback()
    
    def _on_error(self, bus, message):
        """Handle error message."""
        err, debug = message.parse_error()
        self.logger.error('GStreamer error: %s: %s', err, debug)
    
    # =========================================================================
    # Legacy Methods (for test compatibility)
    # =========================================================================
    
    def run(self):
        """
        Run the streaming controller in passthrough mode.
        
        Note: This is a legacy method kept for test compatibility.
        For YouTube playback, use load_file() instead.
        """
        self._ensure_gst_initialized()
        
        if self.pipeline is None:
            self.logger.info('Creating passthrough pipeline...')
            self._create_passthrough_pipeline()
        
        Gst = _get_gst()
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error('Failed to start pipeline')
            return
        
        # Wait for messages
        bus = self.pipeline.get_bus()
        while True:
            msg = bus.timed_pop_filtered(
                Gst.CLOCK_TIME_NONE,
                Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.STATE_CHANGED
            )
            
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                self.logger.error('GStreamer error: %s: %s', err, debug)
                break
            elif msg.type == Gst.MessageType.STATE_CHANGED:
                if msg.src == self.pipeline:
                    change = msg.parse_state_changed()
                    if change.newstate == Gst.State.NULL:
                        break
            elif msg.type == Gst.MessageType.EOS:
                self.logger.debug('End of stream')
                if self.eos_callback:
                    self.eos_callback()
                break
    
    def _create_passthrough_pipeline(self):
        """Create a passthrough pipeline for live audio processing."""
        Gst = _get_gst()
        pipeline = Gst.Pipeline.new('PassthroughPipeline')
        
        # Source
        gstreamer_source = self.config_manager.get('gstreamer_source')
        source = self._make_element(gstreamer_source, 'source')
        video_input = self.config_manager.get('video_input_device')
        self._set_device(source, video_input)
        pipeline.add(source)
        
        # Decoder
        decode = self._make_element('decodebin', 'decode')
        pipeline.add(decode)
        
        # Audio pipeline
        convert_audio_input = self._make_element('audioconvert', 'convert_audio_input')
        pipeline.add(convert_audio_input)
        
        pitch_shift, use_pitch_shift = self._create_pitch_shift_element(pipeline)
        
        convert_audio_output = self._make_element('audioconvert', 'convert_audio_output')
        pipeline.add(convert_audio_output)
        
        audio_sink = self._create_audio_sink(pipeline)
        
        # Video (fakesink for passthrough mode)
        video_sink = self._make_element('fakesink', 'video_sink')
        pipeline.add(video_sink)
        
        # Link audio elements
        if use_pitch_shift and pitch_shift:
            convert_audio_input.link(pitch_shift)
            pitch_shift.link(convert_audio_output)
        else:
            convert_audio_input.link(convert_audio_output)
        convert_audio_output.link(audio_sink)
        
        # Handle dynamic pads from decodebin
        def decodebin_pad_added(element, pad):
            caps_string = pad.query_caps(None).to_string()
            if caps_string.startswith('audio/x-raw'):
                pad.link(convert_audio_input.get_static_pad('sink'))
            elif caps_string.startswith('video/x-raw'):
                pad.link(video_sink.get_static_pad('sink'))
        
        decode.connect("pad-added", decodebin_pad_added)
        source.link(decode)
        
        self.pipeline = pipeline
        return pipeline
