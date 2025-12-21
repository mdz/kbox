"""
GStreamer-based streaming controller for audio/video playback.

Uses a persistent playbin pipeline with custom sink bins for pitch shifting.
The pipeline is created at initialization and stays alive, switching between
READY (idle) and PLAYING (song) states.
"""

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
    """Controls GStreamer pipeline for audio/video playback."""
    
    def __init__(self, config_manager, server, test_mode: bool = False):
        """
        Initialize StreamingController with persistent pipeline.
        
        Args:
            config_manager: Configuration manager instance
            server: Server instance
            test_mode: If True, use fakesinks for headless testing
        """
        self.config_manager = config_manager
        self.server = server
        self.test_mode = test_mode
        self.logger = logging.getLogger(__name__)
        
        # State tracking
        self.state = 'idle'  # 'idle', 'playing', 'paused'
        self.current_file = None
        self.pitch_shift_semitones = 0
        self.eos_callback = None
        
        # Pipeline components (set by _create_persistent_pipeline)
        self.playbin = None
        self.audio_bin = None
        self.video_bin = None
        self.pitch_shift_element = None
        
        # GStreamer initialization state
        self._gst_initialized = False
        
        # Create the persistent pipeline
        self.logger.info('StreamingController initializing with %s', 
                        'test sinks' if test_mode else 'hardware sinks')
        self._create_persistent_pipeline()
        self.logger.info('StreamingController initialized, pipeline ready in idle state')
    
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
    # Pipeline Creation
    # =========================================================================
    
    def _create_persistent_pipeline(self):
        """Create the persistent playbin pipeline with custom sink bins."""
        self._ensure_gst_initialized()
        
        Gst = _get_gst()
        self.playbin = Gst.ElementFactory.make('playbin', 'playbin')
        if self.playbin is None:
            raise RuntimeError('Failed to create playbin element')
        
        # Create and attach custom audio sink bin (with pitch shift)
        self.audio_bin = self._create_audio_sink_bin()
        self.playbin.set_property('audio-sink', self.audio_bin)
        
        # Create and attach custom video sink bin
        self.video_bin = self._create_video_sink_bin()
        self.playbin.set_property('video-sink', self.video_bin)
        
        # Connect bus handlers for EOS and errors
        bus = self.playbin.get_bus()
        bus.add_signal_watch()
        bus.connect('message::eos', self._on_eos)
        bus.connect('message::error', self._on_error)
        
        # Start bus polling thread for EOS/error handling
        # (signal watch requires GLib main loop which may not be running)
        self._start_bus_polling()
        
        # Start in READY state (idle, no output)
        ret = self.playbin.set_state(Gst.State.READY)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Failed to set pipeline to READY state')
        
        self.logger.info('Persistent pipeline created successfully')
    
    def _create_audio_sink_bin(self):
        """Create audio sink bin with pitch shift element."""
        Gst = _get_gst()
        audio_bin = Gst.Bin.new('audio_sink_bin')
        
        # Create elements: audioconvert -> pitch_shift -> audioconvert -> sink
        ac1 = Gst.ElementFactory.make('audioconvert', 'ac1')
        if ac1 is None:
            raise RuntimeError('Failed to create audioconvert element')
        
        # Create pitch shift element or identity passthrough
        self.pitch_shift_element = self._create_pitch_shift_or_identity()
        
        ac2 = Gst.ElementFactory.make('audioconvert', 'ac2')
        if ac2 is None:
            raise RuntimeError('Failed to create audioconvert element')
        
        # Create platform-appropriate audio sink
        from .platform import create_audio_sink
        audio_output_device = self.config_manager.get('audio_output_device')
        sink = create_audio_sink(test_mode=self.test_mode, device=audio_output_device)
        
        # Add all elements to bin
        for elem in [ac1, self.pitch_shift_element, ac2, sink]:
            audio_bin.add(elem)
        
        # Link elements
        if not ac1.link(self.pitch_shift_element):
            raise RuntimeError('Failed to link audioconvert to pitch_shift')
        if not self.pitch_shift_element.link(ac2):
            raise RuntimeError('Failed to link pitch_shift to audioconvert')
        if not ac2.link(sink):
            raise RuntimeError('Failed to link audioconvert to sink')
        
        # Create ghost pad pointing to first element's sink pad
        sink_pad = ac1.get_static_pad('sink')
        ghost_pad = Gst.GhostPad.new('sink', sink_pad)
        audio_bin.add_pad(ghost_pad)
        
        self.logger.info('Audio sink bin created with pitch shift')
        return audio_bin
    
    def _create_video_sink_bin(self):
        """Create video sink bin with scaling and format conversion."""
        Gst = _get_gst()
        video_bin = Gst.Bin.new('video_sink_bin')
        
        # Create elements: videoconvert -> videoscale -> sink
        vc = Gst.ElementFactory.make('videoconvert', 'videoconvert')
        if vc is None:
            raise RuntimeError('Failed to create videoconvert element')
        
        vs = Gst.ElementFactory.make('videoscale', 'videoscale')
        if vs is None:
            raise RuntimeError('Failed to create videoscale element')
        
        # Create platform-appropriate video sink
        from .platform import create_video_sink
        sink = create_video_sink(test_mode=self.test_mode)
        
        # Add all elements to bin
        for elem in [vc, vs, sink]:
            video_bin.add(elem)
        
        # Link elements
        if not vc.link(vs):
            raise RuntimeError('Failed to link videoconvert to videoscale')
        if not vs.link(sink):
            raise RuntimeError('Failed to link videoscale to sink')
        
        # Create ghost pad pointing to first element's sink pad
        sink_pad = vc.get_static_pad('sink')
        ghost_pad = Gst.GhostPad.new('sink', sink_pad)
        video_bin.add_pad(ghost_pad)
        
        self.logger.info('Video sink bin created')
        return video_bin
    
    def _create_pitch_shift_or_identity(self):
        """Create pitch shift element or identity passthrough if unavailable."""
        Gst = _get_gst()
        
        rubberband_plugin = self.config_manager.get('rubberband_plugin')
        if not rubberband_plugin:
            self.logger.warning('No rubberband plugin configured, using identity')
            return Gst.ElementFactory.make('identity', 'pitch_shift')
        
        try:
            elem = Gst.ElementFactory.make(rubberband_plugin, 'pitch_shift')
            if elem is None:
                import os
                self.logger.warning(
                    'Rubberband plugin "%s" not found (LADSPA_PATH=%s), using identity',
                    rubberband_plugin, os.environ.get('LADSPA_PATH', 'not set')
                )
                return Gst.ElementFactory.make('identity', 'pitch_shift')
            
            # Check if element supports semitones property
            element_type = type(elem).__name__
            if element_type == 'GstIdentity':
                self.logger.warning('Got identity element, pitch shift not available')
                return elem
            
            if hasattr(elem, 'set_property'):
                try:
                    elem.set_property('semitones', self.pitch_shift_semitones)
                    self.logger.info('Pitch shift element created successfully')
                    return elem
                except Exception as e:
                    self.logger.warning('Pitch shift element lacks semitones property: %s', e)
                    return Gst.ElementFactory.make('identity', 'pitch_shift')
            else:
                self.logger.warning('Pitch shift element lacks set_property')
                return Gst.ElementFactory.make('identity', 'pitch_shift')
                
        except Exception as e:
            self.logger.warning('Error creating pitch shift: %s, using identity', e)
            return Gst.ElementFactory.make('identity', 'pitch_shift')
    
    # =========================================================================
    # Playback Control
    # =========================================================================
    
    def load_file(self, filepath: str, start_position_seconds: int = 0):
        """
        Load and play a video file.
        
        Args:
            filepath: Path to video file
            start_position_seconds: Position to start playback from (default 0)
            
        Raises:
            RuntimeError: If playback fails to start
        """
        self.logger.info('Loading file: %s (start_position=%s)', filepath, start_position_seconds)
        
        Gst = _get_gst()
        
        self.logger.debug('[DEBUG] load_file: entry, current_state=%s', self.state)
        
        # Set to NULL to reset pipeline
        self.playbin.set_state(Gst.State.NULL)
        self.logger.debug('[DEBUG] load_file: after NULL')
        
        # Set new URI
        self.playbin.set_property('uri', f'file://{filepath}')
        
        # If we need to start at a non-zero position, go to PAUSED first,
        # seek, then go to PLAYING. This prevents audio from position 0
        # playing briefly before the seek completes.
        if start_position_seconds > 0:
            self.logger.debug('[DEBUG] load_file: going to PAUSED for pre-seek')
            ret = self.playbin.set_state(Gst.State.PAUSED)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('Failed to pause for seek')
            
            # Wait for PAUSED state
            ret, state, pending = self.playbin.get_state(5 * Gst.SECOND)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('Pipeline failed to reach PAUSED state')
            
            # Seek while paused
            position_ns = start_position_seconds * Gst.SECOND
            self.logger.debug('[DEBUG] load_file: seeking to %s while paused', start_position_seconds)
            self.playbin.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                position_ns
            )
        
        # Start playing
        ret = self.playbin.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Failed to start playback')
        
        self.logger.debug('[DEBUG] load_file: after PLAYING request, ret=%s', ret)
        
        # Wait for state change to complete or error
        ret, state, pending = self.playbin.get_state(5 * Gst.SECOND)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Pipeline failed to reach PLAYING state')
        
        self.logger.debug('[DEBUG] load_file: state reached %s', state)
        
        self.state = 'playing'
        self.current_file = filepath
        self.logger.info('Playback started successfully')
    
    def stop_playback(self):
        """Stop current playback and return to idle state."""
        self.logger.info('Stopping playback')
        
        Gst = _get_gst()
        self.logger.debug('[DEBUG] stop_playback: before READY, state=%s', self.state)
        self.playbin.set_state(Gst.State.READY)
        self.logger.debug('[DEBUG] stop_playback: after READY')
        
        self.state = 'idle'
        self.current_file = None
        self.logger.info('Returned to idle state')
    
    def pause(self):
        """Pause playback."""
        if self.state != 'playing':
            self.logger.warning('Cannot pause: not currently playing')
            raise RuntimeError('Cannot pause: not currently playing')
        
        Gst = _get_gst()
        ret = self.playbin.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Failed to pause playback')
        
        # Wait for state change to complete (up to 5 seconds)
        ret, state, pending = self.playbin.get_state(5 * Gst.SECOND)
        if state != Gst.State.PAUSED:
            self.logger.warning('Pause state change: ret=%s, state=%s, pending=%s', 
                              ret, state, pending)
        
        self.state = 'paused'
        self.logger.info('Playback paused')
    
    def resume(self):
        """Resume playback."""
        if self.state != 'paused':
            self.logger.warning('Cannot resume: not currently paused')
            raise RuntimeError('Cannot resume: not currently paused')
        
        Gst = _get_gst()
        ret = self.playbin.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Failed to resume playback')
        
        # Wait for state change to complete
        self.playbin.get_state(Gst.SECOND)
        
        self.state = 'playing'
        self.logger.info('Playback resumed')
    
    def stop(self):
        """Stop the streaming controller and cleanup resources."""
        self.logger.info('Stopping streaming controller...')
        
        # Stop bus polling first
        self._stop_bus_polling()
        
        if self.playbin:
            try:
                Gst = _get_gst()
                self.playbin.set_state(Gst.State.NULL)
                self.playbin = None
            except Exception as e:
                self.logger.error('Error stopping pipeline: %s', e, exc_info=True)
        
        self.logger.info('Streaming controller stopped')
    
    # =========================================================================
    # Pitch Control
    # =========================================================================
    
    def set_pitch_shift(self, semitones: int):
        """
        Set pitch shift in semitones.
        
        Updates the pitch shift element if available. The setting persists
        across song changes since the element is in a persistent bin.
        
        Args:
            semitones: Pitch adjustment in semitones (-12 to +12)
        """
        if semitones == self.pitch_shift_semitones:
            self.logger.debug('Pitch shift already set to %s semitones', semitones)
            return
        
        self.logger.info('Setting pitch shift to %s semitones', semitones)
        self.pitch_shift_semitones = semitones
        
        if self.pitch_shift_element:
            try:
                element_type = type(self.pitch_shift_element).__name__
                if element_type != 'GstIdentity':
                    self.pitch_shift_element.set_property('semitones', semitones)
                    self.logger.info('Pitch shift updated in element')
                else:
                    self.logger.warning('Pitch shift element is identity, no effect')
            except Exception as e:
                self.logger.warning('Could not update pitch shift: %s', e)
    
    # =========================================================================
    # Position and Seeking
    # =========================================================================
    
    def get_position(self) -> Optional[int]:
        """Get current playback position in seconds."""
        if self.state not in ('playing', 'paused'):
            return None
        
        try:
            Gst = _get_gst()
            success, position = self.playbin.query_position(Gst.Format.TIME)
            if success:
                return position // Gst.SECOND
            return None
        except Exception as e:
            self.logger.warning('Could not get playback position: %s', e)
            return None
    
    def seek(self, position_seconds: int) -> bool:
        """
        Seek to a specific position in seconds.
        
        Args:
            position_seconds: Position to seek to
            
        Returns:
            True if successful, False otherwise
        """
        if self.state not in ('playing', 'paused'):
            self.logger.warning('Cannot seek: no active playback')
            return False
        
        try:
            Gst = _get_gst()
            position_ns = position_seconds * Gst.SECOND
            success = self.playbin.seek_simple(
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
        self.logger.error('GStreamer error: %s', err)
        self.logger.error('Debug info: %s', debug)
    
    # =========================================================================
    # Bus Polling (for environments without GLib main loop)
    # =========================================================================
    
    def _start_bus_polling(self):
        """Start a thread to poll the bus for messages."""
        import threading
        
        self._bus_poll_running = True
        
        def poll_bus():
            Gst = _get_gst()
            bus = self.playbin.get_bus()
            while self._bus_poll_running and self.playbin:
                msg = bus.timed_pop(100 * Gst.MSECOND)  # 100ms timeout
                if msg:
                    if msg.type == Gst.MessageType.EOS:
                        self._on_eos(bus, msg)
                    elif msg.type == Gst.MessageType.ERROR:
                        self._on_error(bus, msg)
        
        self._bus_poll_thread = threading.Thread(
            target=poll_bus, daemon=True, name='GstBusPoll'
        )
        self._bus_poll_thread.start()
    
    def _stop_bus_polling(self):
        """Stop the bus polling thread."""
        self._bus_poll_running = False
        if hasattr(self, '_bus_poll_thread') and self._bus_poll_thread.is_alive():
            self._bus_poll_thread.join(timeout=1)
    
    # =========================================================================
    # Testing Support
    # =========================================================================
    
    def get_pipeline_state(self) -> str:
        """
        Get current GStreamer pipeline state.
        
        Returns:
            State name: 'null', 'ready', 'paused', or 'playing'
            
        Note: This method is primarily for testing.
        """
        if not self.playbin:
            return 'null'
        
        try:
            Gst = _get_gst()
            # Use 1 second timeout instead of waiting forever
            _, state, _ = self.playbin.get_state(Gst.SECOND)
            return state.value_nick
        except Exception as e:
            self.logger.warning('Error getting pipeline state: %s', e)
            return 'unknown'
