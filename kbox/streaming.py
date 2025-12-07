import logging
import sys

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
    def __init__(self, config, server):
        self.config = config
        self.server = server
        self.logger = logging.getLogger(__name__)
        self.pitch_shift_semitones = 0
        self.pipeline = None
        self.mode = 'passthrough'  # 'passthrough' or 'youtube'
        self.current_file = None
        self.eos_callback = None  # Callback for end-of-stream
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
        Gst = _get_gst()
        element = Gst.ElementFactory.make(element_type, name)
        if element is None:
            # Try to find alternative elements
            if element_type == self.config.RUBBERBAND_PLUGIN:
                self.logger.warning('Rubberband plugin not found, pitch shifting will be disabled')
                # Return a passthrough element instead
                return self.make_element('identity', name)
            raise ValueError('Unable to initialize gstreamer element %s as %s. Available plugins may be missing.' % (element_type, name))
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
        Load a video file for YouTube playback mode.
        
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
            self.mode = 'youtube'
            self._create_youtube_pipeline(filepath)
            
            # Start playback
            Gst = _get_gst()
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                self.logger.error('Failed to start YouTube playback')
                raise RuntimeError('Failed to start playback')
        except Exception as e:
            self.logger.error('Error loading file %s: %s', filepath, e, exc_info=True)
            # Don't crash - just log the error
            raise
    
    def _create_youtube_pipeline(self, filepath: str):
        """Create pipeline for YouTube file playback."""
        self._ensure_gst_initialized()
        
        # On macOS, use a simpler pipeline to avoid crashes
        if sys.platform == 'darwin':
            return self._create_simple_macos_pipeline(filepath)
        
        self.logger.info('Creating YouTube playback pipeline for: %s', filepath)
        Gst = _get_gst()
        pipeline = Gst.Pipeline.new('YouTubePlayback')
        
        # File source
        filesrc = self.make_element('filesrc', 'filesrc')
        filesrc.set_property('location', filepath)
        pipeline.add(filesrc)
        
        # Decodebin for audio/video
        decodebin = self.make_element('decodebin', 'decodebin')
        pipeline.add(decodebin)
        
        # Audio pipeline
        audioconvert_input = self.make_element('audioconvert', 'audioconvert_input')
        pipeline.add(audioconvert_input)
        
        # Try to add pitch shift, but make it optional
        use_pitch_shift = False
        pitch_shift = None
        try:
            pitch_shift = self.make_element(self.config.RUBBERBAND_PLUGIN, 'pitch_shift')
            if pitch_shift and hasattr(pitch_shift, 'set_property'):
                pitch_shift.set_property('semitones', self.pitch_shift_semitones)
                pipeline.add(pitch_shift)
                use_pitch_shift = True
                self.logger.info('Pitch shift enabled')
            else:
                self.logger.warning('Pitch shift element created but not usable')
        except Exception as e:
            self.logger.warning('Could not create pitch shift element: %s. Continuing without pitch shift.', e)
        
        audioconvert_output = self.make_element('audioconvert', 'audioconvert_output')
        pipeline.add(audioconvert_output)
        
        # Audio sink - use autoaudiosink which auto-detects
        audio_sink = self.make_element(self.config.GSTREAMER_SINK, 'audio_sink')
        if self.config.audio_output:
            self.set_device(audio_sink, self.config.audio_output)
        pipeline.add(audio_sink)
        
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
        
        # Link static parts - handle optional pitch shift
        if use_pitch_shift and pitch_shift:
            audioconvert_input.link(pitch_shift)
            pitch_shift.link(audioconvert_output)
        else:
            audioconvert_input.link(audioconvert_output)
        audioconvert_output.link(audio_sink)
        
        videoconvert.link(videoscale)
        videoscale.link(video_sink)
        
        # Handle dynamic pads from decodebin
        def on_pad_added(element, pad):
            caps = pad.query_caps(None)
            caps_string = caps.to_string()
            self.logger.debug('Decodebin pad added: %s', caps_string)
            
            if caps_string.startswith('audio/'):
                pad.link(audioconvert_input.get_static_pad('sink'))
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
        pipeline = Gst.Pipeline.new('YouTubePlayback')
        
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
    
    def stop(self):
        self.logger.debug('Stopping gstreamer pipeline...')
        if self.pipeline:
            Gst = _get_gst()
            result = self.pipeline.set_state(Gst.State.NULL)
            if result == Gst.StateChangeReturn.SUCCESS:
                self.logger.debug('Pipeline state changed successfully')
            elif result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('Failed to change pipeline state')
            else:
                raise RuntimeError('Unexpected result from set_state: %s', result)
