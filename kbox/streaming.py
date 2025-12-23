"""
GStreamer-based streaming controller for audio/video playback.

Uses a persistent playbin pipeline with custom sink bins for pitch shifting.
The pipeline is created at initialization and stays alive, switching between
READY (idle) and PLAYING (song) states.
"""

import logging
import sys
import threading
from typing import Any, Optional

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

        gi.require_version("GLib", "2.0")
        gi.require_version("GObject", "2.0")
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst as _Gst_module

        _Gst = _Gst_module
        return _Gst
    except Exception as e:
        logging.getLogger(__name__).error("Failed to import GStreamer: %s", e)
        raise


class StreamingController:
    """Controls GStreamer pipeline for audio/video playback."""

    def __init__(self, config_manager, server, use_fakesinks: bool = False):
        """
        Initialize StreamingController with persistent pipeline.

        Args:
            config_manager: Configuration manager instance
            server: Server instance
            use_fakesinks: If True, use fakesinks for headless testing (internal use only)
        """
        self.config_manager = config_manager
        self.server = server
        self.use_fakesinks = use_fakesinks
        self.logger = logging.getLogger(__name__)

        # State tracking
        self.state = "idle"  # 'idle', 'playing', 'paused'
        self.current_file: Optional[str] = None
        self.pitch_shift_semitones = 0
        self.eos_callback = None

        # Pipeline components (set by _create_persistent_pipeline)
        self.playbin: Any = None
        self.audio_bin: Any = None
        self.video_bin: Any = None
        self.pitch_shift_element: Any = None

        # Overlay elements (set by _create_video_sink_bin)
        self.qr_overlay = None
        self.text_overlay = None
        self._notification_timer = None
        self._notification_lock = None

        # Interstitial state
        self._is_interstitial = False  # True when displaying interstitial (not a song)

        # GStreamer initialization state
        self._gst_initialized = False

        # Create the persistent pipeline
        self.logger.info(
            "StreamingController initializing with %s",
            "fakesinks" if use_fakesinks else "hardware sinks",
        )
        self._create_persistent_pipeline()
        self.logger.info("StreamingController initialized, pipeline ready in idle state")

    # =========================================================================
    # GStreamer Initialization
    # =========================================================================

    def _ensure_gst_initialized(self):
        """Initialize GStreamer if not already done."""
        Gst = _get_gst()

        if self._gst_initialized:
            return

        if not Gst.is_initialized():
            self.logger.info("Initializing GStreamer...")
            try:
                argv = [
                    "kbox",
                    "--gst-disable-segtrap",
                    "--gst-disable-registry-fork",
                    "--gst-disable-registry-update",
                ]
                if sys.platform == "darwin":
                    import os

                    os.environ.setdefault("GST_PLUGIN_SCANNER", "")
                    os.environ.setdefault("GST_REGISTRY_FORK", "no")
                    if "LADSPA_PATH" not in os.environ:
                        ladspa_path = os.path.expanduser("~/.ladspa")
                        if os.path.exists(ladspa_path):
                            os.environ["LADSPA_PATH"] = ladspa_path

                Gst.init(argv)
                self.logger.info("GStreamer initialized successfully")
            except Exception as e:
                self.logger.error("Failed to initialize GStreamer: %s", e, exc_info=True)
                if sys.platform == "darwin":
                    self.logger.warning("GStreamer init had issues, but continuing anyway")
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
        self.playbin = Gst.ElementFactory.make("playbin", "playbin")
        if self.playbin is None:
            raise RuntimeError("Failed to create playbin element")

        # Create and attach custom audio sink bin (with pitch shift)
        self.audio_bin = self._create_audio_sink_bin()
        self.playbin.set_property("audio-sink", self.audio_bin)

        # Create and attach custom video sink bin
        self.video_bin = self._create_video_sink_bin()
        self.playbin.set_property("video-sink", self.video_bin)

        # Connect bus handlers for EOS, errors, and warnings
        bus = self.playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)

        # Start bus polling thread for EOS/error handling
        # (signal watch requires GLib main loop which may not be running)
        self._start_bus_polling()

        # Start in READY state (idle, no output)
        ret = self.playbin.set_state(Gst.State.READY)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to set pipeline to READY state")

        self.logger.info("Persistent pipeline created successfully")

    def _create_audio_sink_bin(self):
        """Create audio sink bin with pitch shift element."""
        Gst = _get_gst()
        audio_bin = Gst.Bin.new("audio_sink_bin")

        # Create elements: audioconvert -> pitch_shift -> audioconvert -> sink
        ac1 = Gst.ElementFactory.make("audioconvert", "ac1")
        if ac1 is None:
            raise RuntimeError("Failed to create audioconvert element")

        # Create pitch shift element or identity passthrough
        self.pitch_shift_element = self._create_pitch_shift_or_identity()

        ac2 = Gst.ElementFactory.make("audioconvert", "ac2")
        if ac2 is None:
            raise RuntimeError("Failed to create audioconvert element")

        # Create platform-appropriate audio sink
        from .platform import create_audio_sink

        audio_output_device = self.config_manager.get("audio_output_device")
        sink = create_audio_sink(use_fakesinks=self.use_fakesinks, device=audio_output_device)

        # Add all elements to bin
        for elem in [ac1, self.pitch_shift_element, ac2, sink]:
            audio_bin.add(elem)

        # Link elements
        if not ac1.link(self.pitch_shift_element):
            raise RuntimeError("Failed to link audioconvert to pitch_shift")
        if not self.pitch_shift_element.link(ac2):
            raise RuntimeError("Failed to link pitch_shift to audioconvert")
        if not ac2.link(sink):
            raise RuntimeError("Failed to link audioconvert to sink")

        # Create ghost pad pointing to first element's sink pad
        sink_pad = ac1.get_static_pad("sink")
        ghost_pad = Gst.GhostPad.new("sink", sink_pad)
        audio_bin.add_pad(ghost_pad)

        self.logger.info("Audio sink bin created with pitch shift")
        return audio_bin

    def _create_video_sink_bin(self):
        """Create video sink bin with overlays, scaling and format conversion."""
        Gst = _get_gst()
        video_bin = Gst.Bin.new("video_sink_bin")

        # Build element chain: videoconvert -> qr_overlay -> text_overlay -> videoscale -> sink
        elements = []

        # 1. videoconvert (required)
        vc = Gst.ElementFactory.make("videoconvert", "videoconvert")
        if vc is None:
            raise RuntimeError("Failed to create videoconvert element")
        elements.append(vc)

        # 2. QR code overlay (optional - graceful fallback if unavailable)
        self.qr_overlay = self._create_qr_overlay_element()
        if self.qr_overlay:
            elements.append(self.qr_overlay)

        # 3. Text overlay for notifications (optional - graceful fallback)
        self.text_overlay = self._create_text_overlay_element()
        if self.text_overlay:
            elements.append(self.text_overlay)

        # Initialize notification lock
        self._notification_lock = threading.Lock()

        # 4. videoscale (required)
        vs = Gst.ElementFactory.make("videoscale", "videoscale")
        if vs is None:
            raise RuntimeError("Failed to create videoscale element")
        elements.append(vs)

        # 5. Platform-appropriate video sink
        from .platform import create_video_sink

        sink = create_video_sink(use_fakesinks=self.use_fakesinks)
        elements.append(sink)

        # Add all elements to bin
        for elem in elements:
            video_bin.add(elem)

        # Link elements in order
        for i in range(len(elements) - 1):
            if not elements[i].link(elements[i + 1]):
                raise RuntimeError(
                    f"Failed to link {elements[i].get_name()} to {elements[i + 1].get_name()}"
                )

        # Create ghost pad pointing to first element's sink pad
        sink_pad = elements[0].get_static_pad("sink")
        ghost_pad = Gst.GhostPad.new("sink", sink_pad)
        video_bin.add_pad(ghost_pad)

        # Add pad probe on videoconvert's src pad to detect video dimensions
        if self.qr_overlay:
            vc_src_pad = vc.get_static_pad("src")
            vc_src_pad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, self._on_video_caps_event)
            self.logger.debug("QR overlay pad probe added to videoconvert src pad")

        self.logger.info(
            "Video sink bin created with overlays (qr=%s, text=%s)",
            self.qr_overlay is not None,
            self.text_overlay is not None,
        )
        return video_bin

    def _create_qr_overlay_element(self):
        """Create gdkpixbufoverlay element for QR code, or None if unavailable."""
        Gst = _get_gst()

        try:
            qr = Gst.ElementFactory.make("gdkpixbufoverlay", "qr_overlay")
            if qr is None:
                self.logger.warning("gdkpixbufoverlay not available, QR overlay disabled")
                return None

            # Store config for later use when positioning
            self._qr_position = self.config_manager.get("overlay_qr_position") or "top-left"
            self._qr_size_percent = self.config_manager.get_int("overlay_qr_size_percent", 10)
            self._qr_current_size = 72  # Default size, will be updated by caps probe
            self._qr_current_padding = 15

            # Set initial size and alpha - will be updated when we know video dimensions
            qr.set_property("overlay-width", self._qr_current_size)
            qr.set_property("overlay-height", self._qr_current_size)
            qr.set_property("offset-x", self._qr_current_padding)
            qr.set_property("offset-y", self._qr_current_padding)
            qr.set_property("alpha", 0.7)  # Semi-transparent

            self.logger.info(
                "QR overlay element created (size_percent=%d%%, position=%s)",
                self._qr_size_percent,
                self._qr_position,
            )
            return qr

        except Exception as e:
            self.logger.warning("Failed to create QR overlay: %s", e)
            return None

    def _create_text_overlay_element(self):
        """Create textoverlay element for notifications, or None if unavailable."""
        Gst = _get_gst()

        try:
            text = Gst.ElementFactory.make("textoverlay", "text_overlay")
            if text is None:
                self.logger.warning("textoverlay not available, text notifications disabled")
                return None

            # Configure text overlay - subtle, top-right corner
            text.set_property("text", "")  # Start with no text
            text.set_property("valignment", "top")
            text.set_property("halignment", "right")
            text.set_property("xpad", 20)
            text.set_property("ypad", 20)
            text.set_property("font-desc", "Sans 9")  # Half of original 18pt
            text.set_property("shaded-background", True)
            text.set_property("silent", True)  # No text initially

            self.logger.info("Text overlay element created")
            return text

        except Exception as e:
            self.logger.warning("Failed to create text overlay: %s", e)
            return None

    def _on_video_caps_event(self, pad, info):
        """Handle video caps events to detect resolution changes."""
        Gst = _get_gst()

        try:
            event = info.get_event()
            if event is None:
                return Gst.PadProbeReturn.OK

            if event.type == Gst.EventType.CAPS:
                self.logger.debug("Received CAPS event on videoconvert src pad")
                caps = event.parse_caps()
                if caps:
                    struct = caps.get_structure(0)
                    if struct:
                        # Extract width and height - try multiple API styles
                        width = None
                        height = None

                        # Try dictionary-style access first (most compatible)
                        if hasattr(struct, "__getitem__"):
                            try:
                                width = struct["width"]
                                height = struct["height"]
                            except (KeyError, TypeError):
                                pass

                        # Fallback: try get_value (some versions)
                        if width is None and hasattr(struct, "get_value"):
                            try:
                                width = struct.get_value("width")
                                height = struct.get_value("height")
                            except Exception:
                                pass

                        # Fallback: try to parse from caps string
                        if width is None:
                            caps_str = caps.to_string()
                            self.logger.debug("Parsing caps from string: %s", caps_str)
                            import re

                            width_match = re.search(r"width=\(int\)(\d+)", caps_str)
                            height_match = re.search(r"height=\(int\)(\d+)", caps_str)
                            if width_match and height_match:
                                width = int(width_match.group(1))
                                height = int(height_match.group(1))

                        if width is not None and height is not None:
                            self.logger.info("Detected video resolution: %dx%d", width, height)
                            self._update_qr_size_for_resolution(width, height)
                        else:
                            self.logger.debug(
                                "Could not extract resolution from caps: %s",
                                caps.to_string()[:200],
                            )
                    else:
                        self.logger.debug("CAPS event had no structure")
                else:
                    self.logger.debug("CAPS event had no caps")
        except Exception as e:
            self.logger.warning("Error processing video caps event: %s", e, exc_info=True)

        return Gst.PadProbeReturn.OK

    def _update_qr_size_for_resolution(self, width, height):
        """Update QR overlay size and position based on video resolution."""
        if not self.qr_overlay:
            return

        try:
            # Size QR as configured percentage of video height
            percent = self._qr_size_percent / 100.0
            qr_size = max(48, int(height * percent))  # Minimum 48px for scannability
            padding = max(10, int(height * 0.02))  # ~2% padding

            # Calculate position based on configured corner
            position = self._qr_position
            if position == "top-left":
                x, y = padding, padding
            elif position == "top-right":
                x, y = width - qr_size - padding, padding
            elif position == "bottom-left":
                x, y = padding, height - qr_size - padding
            else:  # bottom-right
                x, y = width - qr_size - padding, height - qr_size - padding

            # Update overlay properties
            self.qr_overlay.set_property("overlay-width", qr_size)
            self.qr_overlay.set_property("overlay-height", qr_size)
            self.qr_overlay.set_property("offset-x", x)
            self.qr_overlay.set_property("offset-y", y)

            # Store current values
            self._qr_current_size = qr_size
            self._qr_current_padding = padding

            self.logger.info(
                "QR overlay sized for %dx%d: size=%dpx (%d%%), position=%s at (%d,%d)",
                width,
                height,
                qr_size,
                self._qr_size_percent,
                position,
                x,
                y,
            )

        except Exception as e:
            self.logger.warning("Failed to update QR size for resolution: %s", e)

    def _create_pitch_shift_or_identity(self):
        """Create pitch shift element or identity passthrough if unavailable."""
        Gst = _get_gst()

        rubberband_plugin = self.config_manager.get("rubberband_plugin")
        if not rubberband_plugin:
            self.logger.warning("No rubberband plugin configured, using identity")
            return Gst.ElementFactory.make("identity", "pitch_shift")

        try:
            elem = Gst.ElementFactory.make(rubberband_plugin, "pitch_shift")
            if elem is None:
                import os

                self.logger.warning(
                    'Rubberband plugin "%s" not found (LADSPA_PATH=%s), using identity',
                    rubberband_plugin,
                    os.environ.get("LADSPA_PATH", "not set"),
                )
                return Gst.ElementFactory.make("identity", "pitch_shift")

            # Check if element supports semitones property
            element_type = type(elem).__name__
            if element_type == "GstIdentity":
                self.logger.warning("Got identity element, pitch shift not available")
                return elem

            if hasattr(elem, "set_property"):
                try:
                    elem.set_property("semitones", self.pitch_shift_semitones)
                    self.logger.info("Pitch shift element created successfully")
                    return elem
                except Exception as e:
                    self.logger.warning("Pitch shift element lacks semitones property: %s", e)
                    return Gst.ElementFactory.make("identity", "pitch_shift")
            else:
                self.logger.warning("Pitch shift element lacks set_property")
                return Gst.ElementFactory.make("identity", "pitch_shift")

        except Exception as e:
            self.logger.warning("Error creating pitch shift: %s, using identity", e)
            return Gst.ElementFactory.make("identity", "pitch_shift")

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
        self.logger.info("Loading file: %s (start_position=%s)", filepath, start_position_seconds)

        Gst = _get_gst()

        self.logger.debug("[DEBUG] load_file: entry, current_state=%s", self.state)

        # Clear interstitial flag - we're loading a real song
        self._is_interstitial = False

        # Set to NULL to reset pipeline
        self.playbin.set_state(Gst.State.NULL)
        self.logger.debug("[DEBUG] load_file: after NULL")

        # Unmute audio (may have been muted for interstitial)
        self.playbin.set_property("mute", False)

        # Set new URI
        self.playbin.set_property("uri", f"file://{filepath}")

        # If we need to start at a non-zero position, go to PAUSED first,
        # seek, then go to PLAYING. This prevents audio from position 0
        # playing briefly before the seek completes.
        if start_position_seconds > 0:
            self.logger.debug("[DEBUG] load_file: going to PAUSED for pre-seek")
            ret = self.playbin.set_state(Gst.State.PAUSED)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Failed to pause for seek")

            # Wait for PAUSED state
            ret, state, pending = self.playbin.get_state(5 * Gst.SECOND)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Pipeline failed to reach PAUSED state")

            # Seek while paused
            position_ns = start_position_seconds * Gst.SECOND
            self.logger.debug(
                "[DEBUG] load_file: seeking to %s while paused", start_position_seconds
            )
            self.playbin.seek_simple(
                Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, position_ns
            )

        # Start playing
        ret = self.playbin.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to start playback")

        self.logger.debug("[DEBUG] load_file: after PLAYING request, ret=%s", ret)

        # Wait for state change to complete or error
        ret, state, pending = self.playbin.get_state(5 * Gst.SECOND)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Pipeline failed to reach PLAYING state")

        self.logger.debug("[DEBUG] load_file: state reached %s", state)

        self.state = "playing"
        self.current_file = filepath
        self.logger.info("Playback started successfully")

    def stop_playback(self):
        """Stop current playback and return to idle state."""
        self.logger.info("Stopping playback")

        Gst = _get_gst()
        self.logger.debug("[DEBUG] stop_playback: before READY, state=%s", self.state)
        self.playbin.set_state(Gst.State.READY)
        self.logger.debug("[DEBUG] stop_playback: after READY")

        self.state = "idle"
        self.current_file = None
        self.logger.info("Returned to idle state")

    def pause(self):
        """Pause playback."""
        if self.state != "playing":
            self.logger.warning("Cannot pause: not currently playing")
            raise RuntimeError("Cannot pause: not currently playing")

        Gst = _get_gst()
        ret = self.playbin.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to pause playback")

        # Wait for state change to complete (up to 5 seconds)
        ret, state, pending = self.playbin.get_state(5 * Gst.SECOND)
        if state != Gst.State.PAUSED:
            self.logger.warning(
                "Pause state change: ret=%s, state=%s, pending=%s", ret, state, pending
            )

        self.state = "paused"
        self.logger.info("Playback paused")

    def resume(self):
        """Resume playback."""
        if self.state != "paused":
            self.logger.warning("Cannot resume: not currently paused")
            raise RuntimeError("Cannot resume: not currently paused")

        Gst = _get_gst()
        ret = self.playbin.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to resume playback")

        # Wait for state change to complete
        self.playbin.get_state(Gst.SECOND)

        self.state = "playing"
        self.logger.info("Playback resumed")

    def stop(self):
        """Stop the streaming controller and cleanup resources."""
        self.logger.info("Stopping streaming controller...")

        # Cancel notification timer
        if self._notification_timer:
            self._notification_timer.cancel()
            self._notification_timer = None

        # Stop bus polling first
        self._stop_bus_polling()

        if self.playbin:
            try:
                Gst = _get_gst()
                self.playbin.set_state(Gst.State.NULL)
                self.playbin = None
            except Exception as e:
                self.logger.error("Error stopping pipeline: %s", e, exc_info=True)

        self.logger.info("Streaming controller stopped")

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
            self.logger.debug("Pitch shift already set to %s semitones", semitones)
            return

        self.logger.info("Setting pitch shift to %s semitones", semitones)
        self.pitch_shift_semitones = semitones

        if self.pitch_shift_element:
            try:
                element_type = type(self.pitch_shift_element).__name__
                if element_type != "GstIdentity":
                    self.pitch_shift_element.set_property("semitones", semitones)
                    self.logger.info("Pitch shift updated in element")
                else:
                    self.logger.warning("Pitch shift element is identity, no effect")
            except Exception as e:
                self.logger.warning("Could not update pitch shift: %s", e)

    # =========================================================================
    # Position and Seeking
    # =========================================================================

    def get_position(self) -> Optional[int]:
        """Get current playback position in seconds."""
        if self.state not in ("playing", "paused"):
            return None

        try:
            Gst = _get_gst()
            success, position = self.playbin.query_position(Gst.Format.TIME)
            if success:
                return position // Gst.SECOND
            return None
        except Exception as e:
            self.logger.warning("Could not get playback position: %s", e)
            return None

    def seek(self, position_seconds: int) -> bool:
        """
        Seek to a specific position in seconds.

        Args:
            position_seconds: Position to seek to

        Returns:
            True if successful, False otherwise
        """
        if self.state not in ("playing", "paused"):
            self.logger.warning("Cannot seek: no active playback")
            return False

        try:
            Gst = _get_gst()
            position_ns = position_seconds * Gst.SECOND
            success = self.playbin.seek_simple(
                Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, position_ns
            )
            if success:
                self.logger.info("Seeked to position: %s seconds", position_seconds)
                # Track last seek position and time to handle stale position queries
                import time

                self._last_seek_position = position_seconds
                self._last_seek_time = time.time()
            else:
                self.logger.warning("Seek failed")
            return success
        except Exception as e:
            self.logger.error("Error seeking: %s", e, exc_info=True)
            return False

    # =========================================================================
    # Overlay Control
    # =========================================================================

    def show_notification(self, text: str, duration_seconds: float = 5.0):
        """
        Show transient text notification that auto-hides.

        Args:
            text: Notification text to display
            duration_seconds: How long to show the notification (default 5s)
        """
        if not self.text_overlay:
            self.logger.debug("Text overlay not available, skipping notification")
            return

        if not self._notification_lock:
            return

        with self._notification_lock:
            # Cancel any pending hide timer
            if self._notification_timer:
                self._notification_timer.cancel()
                self._notification_timer = None

            try:
                # Show the text
                self.text_overlay.set_property("text", text)
                self.text_overlay.set_property("silent", False)
                self.logger.debug("Showing notification: %s", text)

                # Schedule hide after duration
                def hide_notification():
                    self._hide_notification()

                self._notification_timer = threading.Timer(duration_seconds, hide_notification)
                self._notification_timer.daemon = True
                self._notification_timer.start()

            except Exception as e:
                self.logger.warning("Failed to show notification: %s", e)

    def _hide_notification(self):
        """Hide the current notification."""
        if not self.text_overlay:
            return

        if not self._notification_lock:
            return

        with self._notification_lock:
            try:
                self.text_overlay.set_property("text", "")
                self.text_overlay.set_property("silent", True)
                self._notification_timer = None
                self.logger.debug("Notification hidden")
            except Exception as e:
                self.logger.warning("Failed to hide notification: %s", e)

    def set_overlay_text(self, text: str):
        """
        Set persistent overlay text (does not auto-hide).
        Use empty string to clear the overlay.

        Args:
            text: Text to display, or empty string to hide
        """
        if not self.text_overlay:
            self.logger.debug("Text overlay not available, skipping")
            return

        if not self._notification_lock:
            return

        with self._notification_lock:
            # Cancel any pending auto-hide timer
            if self._notification_timer:
                self._notification_timer.cancel()
                self._notification_timer = None

            try:
                if text:
                    self.text_overlay.set_property("text", text)
                    self.text_overlay.set_property("silent", False)
                    self.logger.debug("Set persistent overlay text: %s", text)
                else:
                    self.text_overlay.set_property("text", "")
                    self.text_overlay.set_property("silent", True)
                    self.logger.debug("Cleared overlay text")
            except Exception as e:
                self.logger.warning("Failed to set overlay text: %s", e)

    def update_qr_overlay(self, image_path: str):
        """
        Update QR code overlay image.

        Args:
            image_path: Path to the QR code PNG image
        """
        if not self.qr_overlay:
            self.logger.debug("QR overlay not available")
            return

        try:
            import os

            if not os.path.exists(image_path):
                self.logger.warning("QR image not found: %s", image_path)
                return

            # Verify file size
            file_size = os.path.getsize(image_path)
            self.logger.debug("QR image file size: %d bytes", file_size)

            self.qr_overlay.set_property("location", image_path)

            # Log current overlay properties for debugging
            try:
                loc = self.qr_overlay.get_property("location")
                ox = self.qr_overlay.get_property("offset-x")
                oy = self.qr_overlay.get_property("offset-y")
                ow = self.qr_overlay.get_property("overlay-width")
                oh = self.qr_overlay.get_property("overlay-height")
                alpha = self.qr_overlay.get_property("alpha")
                self.logger.info(
                    "QR overlay configured: location=%s, offset=(%d,%d), size=%dx%d, alpha=%.2f",
                    loc,
                    ox,
                    oy,
                    ow,
                    oh,
                    alpha,
                )
            except Exception as prop_err:
                self.logger.debug("Could not read overlay properties: %s", prop_err)

        except Exception as e:
            self.logger.warning("Failed to update QR overlay: %s", e)

    def set_qr_visible(self, visible: bool):
        """
        Toggle QR code visibility.

        Args:
            visible: True to show, False to hide
        """
        if not self.qr_overlay:
            self.logger.debug("QR overlay not available")
            return

        try:
            if visible:
                self.qr_overlay.set_property("alpha", 0.9)
            else:
                self.qr_overlay.set_property("alpha", 0.0)
            self.logger.debug("QR overlay visibility set to: %s", visible)

        except Exception as e:
            self.logger.warning("Failed to set QR visibility: %s", e)

    # =========================================================================
    # Static Image Display
    # =========================================================================

    def display_image(self, image_path: str):
        """
        Display a static image (interstitial screen).

        The image will be displayed indefinitely until another file is loaded.
        Audio is muted for image display.

        Args:
            image_path: Path to the image file to display
        """
        self.logger.debug("Displaying image: %s", image_path)

        Gst = _get_gst()

        # Mark that we're showing an interstitial
        self._is_interstitial = True

        # Stop any current playback
        self.playbin.set_state(Gst.State.NULL)

        # Set the image URI - GStreamer will use imagefreeze for static images
        self.playbin.set_property("uri", f"file://{image_path}")

        # Mute audio for interstitials (they're silent)
        self.playbin.set_property("mute", True)

        # Start playing
        ret = self.playbin.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error("Failed to start interstitial playback")
            self._is_interstitial = False
            return

        # Wait for state change
        ret, state, pending = self.playbin.get_state(5 * Gst.SECOND)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error("Interstitial failed to reach PLAYING state")
            self._is_interstitial = False
            return

        self.state = "playing"
        self.current_file = image_path
        self.logger.info("Image displayed successfully")

    def is_showing_interstitial(self) -> bool:
        """Check if currently showing an interstitial."""
        return self._is_interstitial

    # =========================================================================
    # Callbacks
    # =========================================================================

    def set_eos_callback(self, callback):
        """Set callback for end-of-stream events."""
        self.eos_callback = callback

    def _on_eos(self, bus, message):
        """Handle end-of-stream message."""
        self.logger.info("End of stream reached (interstitial=%s)", self._is_interstitial)

        # Don't trigger callback for interstitials - they should hold/loop
        if self._is_interstitial:
            # For interstitials, we just stay at the end frame
            # The PlaybackController will load the next content when ready
            self.logger.debug("Interstitial ended, holding last frame")
            return

        if self.eos_callback:
            self.eos_callback()

    def _on_error(self, bus, message):
        """Handle error message."""
        err, debug = message.parse_error()
        self.logger.error("GStreamer error: %s", err)
        self.logger.error("Debug info: %s", debug)

    def _on_warning(self, bus, message):
        """Handle warning message."""
        warn, debug = message.parse_warning()
        self.logger.warning("GStreamer warning: %s", warn)
        self.logger.warning("Debug info: %s", debug)

        # Check for critical audio device warnings
        warn_str = str(warn).lower()
        if "unknown pcm" in warn_str or "could not open audio device" in warn_str:
            self.logger.error("CRITICAL: Audio device error detected - %s", warn)

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

        self._bus_poll_thread = threading.Thread(target=poll_bus, daemon=True, name="GstBusPoll")
        self._bus_poll_thread.start()

    def _stop_bus_polling(self):
        """Stop the bus polling thread."""
        self._bus_poll_running = False
        if hasattr(self, "_bus_poll_thread") and self._bus_poll_thread.is_alive():
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
            return "null"

        try:
            Gst = _get_gst()
            # Use 1 second timeout instead of waiting forever
            _, state, _ = self.playbin.get_state(Gst.SECOND)
            return state.value_nick
        except Exception as e:
            self.logger.warning("Error getting pipeline state: %s", e)
            return "unknown"
