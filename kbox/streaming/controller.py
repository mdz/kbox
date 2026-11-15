"""
GStreamer-based streaming controller for audio/video playback.

Uses a persistent playbin pipeline with custom sink bins for pitch shifting.
The pipeline is created at initialization and stays alive, switching between
READY (idle) and PLAYING (song) states.

StreamingController is the facade the rest of kbox talks to. It owns playbin,
the transport state and the pitch/volume values, and it constructs and wires
the collaborators that own everything else: DisplayPipeline (the screen),
OverlayController (QR and text), StillImageFeeder (interstitials) and
BusWatcher (the poll thread).
"""

import logging
import sys
from typing import Any, Optional

from . import elements
from .bus import BusWatcher
from .gst import _get_gst, caps_dimensions
from .overlays import OverlayController
from .pipelines import DISPLAY_CHANNEL, DisplayPipeline
from .still_image import StillImageFeeder


class StreamingController:
    """Controls GStreamer pipeline for audio/video playback."""

    # intervideosink/intervideosrc channel connecting playbin to the display
    # pipeline. Any string works as long as both ends agree.
    DISPLAY_CHANNEL = DISPLAY_CHANNEL

    # Rate the interstitial pipeline refreshes the bridge at. intervideosrc
    # serves a received buffer only a couple of times before falling back to
    # generating black, so a still image has to keep being pushed at roughly
    # the rate the display pipeline pulls or black frames interleave with it.
    INTERSTITIAL_FRAMERATE = 30

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
        self.volume_gain_linear = 1.0
        self.eos_callback = None

        # Pipeline components (set by _create_persistent_pipeline)
        self.playbin: Any = None
        self.audio_bin: Any = None
        self.video_bin: Any = None
        self.pitch_shift_element: Any = None
        self.volume_element: Any = None

        # Always-on display pipeline (set by _create_persistent_pipeline).
        # Owns the screen for the whole process lifetime -- see
        # DisplayPipeline.
        self.display: Optional[DisplayPipeline] = None

        # Drives the overlay elements the display pipeline linked in. Created
        # up front with no elements so the remembered QR image path has
        # somewhere to live even when GStreamer is missing entirely.
        self.overlays = OverlayController(None, None, config_manager)

        # Feeds still images into the display bridge while an interstitial is
        # showing. Mutually exclusive with playbin: only one may feed at once.
        self.still_image = StillImageFeeder(self.DISPLAY_CHANNEL, self.INTERSTITIAL_FRAMERATE)

        # Poll threads delivering bus messages, one per bus (set by
        # _create_persistent_pipeline)
        self.bus_watchers: list[BusWatcher] = []

        # Interstitial state
        self._is_interstitial = False  # True when displaying interstitial (not a song)

        # GStreamer initialization state
        self._gst_initialized = False
        self._gst_missing = _get_gst() is None

        if self._gst_missing:
            self.logger.warning(
                "GStreamer not available -- playback features disabled. "
                "Browser-based display (/display) still works."
            )
        else:
            self.logger.info(
                "StreamingController initializing with %s",
                "fakesinks" if use_fakesinks else "hardware sinks",
            )
            self._create_persistent_pipeline()
            self.logger.info("StreamingController initialized, pipeline ready in idle state")

    def _requires_gst(self) -> bool:
        """Return True if GStreamer is available, False otherwise."""
        if self._gst_missing:
            return False
        return True

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

        # Bring up the display pipeline FIRST. It claims the screen once and
        # holds it for the entire session, independently of playbin.
        self._create_display_pipeline()

        self.playbin = Gst.ElementFactory.make("playbin", "playbin")
        if self.playbin is None:
            raise RuntimeError("Failed to create playbin element")

        # Create and attach custom audio sink bin (with pitch shift)
        audio = elements.create_audio_sink_bin(
            self.config_manager,
            self.use_fakesinks,
            self.pitch_shift_semitones,
            self.volume_gain_linear,
        )
        self.audio_bin = audio.bin
        self.pitch_shift_element = audio.pitch_shift_element
        self.volume_element = audio.volume_element
        self.playbin.set_property("audio-sink", self.audio_bin)

        # Create and attach custom video sink bin
        self.video_bin = elements.create_video_sink_bin(
            self.DISPLAY_CHANNEL, self._on_source_caps_event
        )
        self.playbin.set_property("video-sink", self.video_bin)

        # Bus messages are delivered by polling threads, not by
        # bus.add_signal_watch() + bus.connect(): a signal watch only fires
        # from a GLib main loop, and kbox never runs one. Both this bus and
        # the display pipeline's are polled -- see _start_bus_polling().
        self._start_bus_polling()

        # Start in READY state (idle, no output)
        ret = self.playbin.set_state(Gst.State.READY)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to set pipeline to READY state")

        self.logger.info("Persistent pipeline created successfully")

    def _create_display_pipeline(self):
        """
        Bring up the display pipeline and the overlay controller driving it.

        DisplayPipeline builds the elements and takes itself to READY, which
        is where the render size becomes known. The overlays are sized to that
        frame before start() goes to PLAYING, and the QR image from any
        previous incarnation is re-applied by reinitialize_pipeline.
        """
        self.display = DisplayPipeline(self.config_manager, self.use_fakesinks)

        self.overlays = OverlayController(
            self.display.qr_overlay,
            self.display.text_overlay,
            self.config_manager,
            qr_image_path=self.overlays.qr_image_path,
        )

        if self.display.render_size:
            width, height = self.display.render_size
            self.overlays.update_qr_size_for_resolution(width, height)
            self.overlays.update_text_layout_for_resolution(width)

        self.display.start()

    def _on_source_caps_event(self, pad, info):
        """
        Log the dimensions of the video stream playbin is actually decoding.

        Purely diagnostic. The display pipeline rescales everything to the
        screen, so without this the source resolution is invisible in the logs
        and has to be recovered by probing the file on disk.
        """
        Gst = _get_gst()

        try:
            event = info.get_event()
            if event is None or event.type != Gst.EventType.CAPS:
                return Gst.PadProbeReturn.OK

            caps = event.parse_caps()
            width, height = caps_dimensions(caps)
            if width is not None and height is not None:
                display = self.display.display_size if self.display else None
                if display:
                    self.logger.info(
                        "Source video stream: %dx%d (scaled to %dx%d for display)",
                        width,
                        height,
                        display[0],
                        display[1],
                    )
                else:
                    self.logger.info("Source video stream: %dx%d", width, height)
            else:
                self.logger.debug(
                    "Could not extract source dimensions from caps: %s",
                    caps.to_string()[:200] if caps else None,
                )
        except Exception as e:
            self.logger.warning("Error logging source video caps: %s", e)

        return Gst.PadProbeReturn.OK

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

        Note on "GStreamer-Audio-CRITICAL ... gst_audio_ring_buffer_set_channel_positions:
        should not be reached": this fires on essentially every song load and is a known
        upstream quirk, not a bug in this pipeline. In gstaudioringbuffer.c, that function
        calls gst_audio_get_channel_reorder_map(device_positions, stream_positions), which
        returns FALSE (triggering the critical) if EITHER side's position array contains
        GST_AUDIO_CHANNEL_POSITION_NONE. The caller only special-cases "positionless" for
        the device side; it never checks the stream's own negotiated positions. Plain
        2-channel audio negotiated without an explicit channel-mask (the common case for
        our source files) is exactly this "positionless" case, so it collides with ALSA
        sinks that report explicit FRONT_LEFT/FRONT_RIGHT device positions - on every
        renegotiation, regardless of pipeline state-change timing. Confirmed via GStreamer
        1.26.2 source; do not treat this log line as evidence of an app-level bug without
        new data.
        """
        self.logger.info("Loading file: %s (start_position=%s)", filepath, start_position_seconds)

        if not self._requires_gst():
            return

        Gst = _get_gst()

        self.logger.debug("load_file: entry, current_state=%s", self.state)

        # Clear interstitial flag - we're loading a real song
        self._is_interstitial = False

        # Hand the display bridge over to playbin: the interstitial pipeline
        # must stop feeding it before playbin starts, or both would.
        self.still_image.stop()

        # Drop to READY to reset pipeline before loading the next file. See
        # the "NULL versus READY also makes no difference to format
        # renegotiation" trap in docs/development/gstreamer-pipeline.md.
        self.playbin.set_state(Gst.State.READY)
        self.logger.debug("load_file: after READY")

        # Unmute audio (may have been muted for interstitial)
        self.playbin.set_property("mute", False)

        # Set new URI
        self.playbin.set_property("uri", f"file://{filepath}")

        # If we need to start at a non-zero position, go to PAUSED first,
        # seek, then go to PLAYING. This prevents audio from position 0
        # playing briefly before the seek completes.
        if start_position_seconds > 0:
            self.logger.debug("load_file: going to PAUSED for pre-seek")
            ret = self.playbin.set_state(Gst.State.PAUSED)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Failed to pause for seek")

            # Wait for PAUSED state
            ret, state, pending = self.playbin.get_state(5 * Gst.SECOND)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Pipeline failed to reach PAUSED state")

            # Seek while paused
            position_ns = start_position_seconds * Gst.SECOND
            self.logger.debug("load_file: seeking to %s while paused", start_position_seconds)
            self.playbin.seek_simple(
                Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, position_ns
            )

        # Start playing
        ret = self.playbin.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to start playback")

        self.logger.debug("load_file: after PLAYING request, ret=%s", ret)

        # Wait for state change to complete or error
        ret, state, pending = self.playbin.get_state(5 * Gst.SECOND)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Pipeline failed to reach PLAYING state")

        self.logger.debug("load_file: state reached %s", state)

        self.state = "playing"
        self.current_file = filepath
        self.logger.info("Playback started successfully")

    def stop_playback(self):
        """Stop current playback and return to idle state."""
        if not self._requires_gst():
            return

        self.logger.info("Stopping playback")

        Gst = _get_gst()
        self.logger.debug("stop_playback: before READY, state=%s", self.state)
        self.playbin.set_state(Gst.State.READY)
        self.logger.debug("stop_playback: after READY")

        self.state = "idle"
        self.current_file = None
        self.logger.info("Returned to idle state")

    def pause(self):
        """Pause playback."""
        if not self._requires_gst():
            return
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
        if not self._requires_gst():
            return
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
        if not self._requires_gst():
            return

        self.logger.info("Stopping streaming controller...")

        # Stop bus polling first
        self._stop_bus_polling()

        self.still_image.stop()

        if self.playbin:
            try:
                Gst = _get_gst()
                self.playbin.set_state(Gst.State.NULL)
                self.playbin = None
            except Exception as e:
                self.logger.error("Error stopping pipeline: %s", e, exc_info=True)

        # Release the screen last, so it stays claimed until we are really
        # shutting down rather than blinking during teardown.
        if self.display:
            self.display.stop()
            self.display = None

        self.logger.info("Streaming controller stopped")

    def reinitialize_pipeline(self):
        """
        Reinitialize the pipeline with fresh configuration.

        Used when audio/video config changes to apply new settings without
        restarting the entire application. Rebuilds the GStreamer pipeline
        while preserving display state.
        """
        if not self._requires_gst():
            return

        self.logger.info("Reinitializing pipeline with updated configuration...")

        # Save current display state
        was_showing_interstitial = self._is_interstitial
        current_file_backup = self.current_file

        # Stop bus polling
        self._stop_bus_polling()

        self.still_image.stop()

        # Set pipeline to NULL and release resources
        if self.playbin:
            Gst = _get_gst()
            self.playbin.set_state(Gst.State.NULL)
            # Pipeline will be garbage collected
            self.playbin = None
            self.audio_bin = None
            self.video_bin = None
            self.pitch_shift_element = None
            self.volume_element = None

        # Tear the display pipeline down too -- it holds the DRM device, and
        # the rebuilt one cannot claim the screen until this one lets go.
        if self.display:
            self.display.release_for_rebuild()
            self.display = None

        # Reset state
        self.state = "idle"
        self.current_file = None
        self._is_interstitial = False

        # Recreate the pipeline with fresh config
        self._create_persistent_pipeline()

        # Re-apply QR overlay image if we had one
        if self.overlays.qr_image_path and self.overlays.qr_overlay:
            self.logger.debug("Re-applying QR overlay after pipeline reinit")
            self.update_qr_overlay(self.overlays.qr_image_path)

        # Restore display if there was an interstitial showing
        if was_showing_interstitial and current_file_backup:
            try:
                self.display_image(current_file_backup)
            except Exception as e:
                self.logger.warning("Could not restore interstitial display: %s", e)

        self.logger.info("Pipeline reinitialized successfully")

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
            return

        self.logger.info("Setting pitch shift to %s semitones", semitones)
        self.pitch_shift_semitones = semitones

        if not self._requires_gst():
            return

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
    # Volume Control
    # =========================================================================

    def set_volume_gain_db(self, gain_db: float):
        """
        Set the loudness-normalization gain, in dB, applied on top of the
        main output level.

        Updates the volume element if available. The setting persists across
        song changes since the element is in a persistent bin, so callers
        should reset it to 0.0 when there's no measurement for a song.

        Args:
            gain_db: Gain adjustment in decibels (positive boosts, negative cuts)
        """
        from ..loudness import db_to_linear

        linear = db_to_linear(gain_db)
        if linear == self.volume_gain_linear:
            return

        self.logger.info("Setting volume gain to %.1f dB (%.3fx)", gain_db, linear)
        self.volume_gain_linear = linear

        if not self._requires_gst():
            return

        if self.volume_element:
            try:
                self.volume_element.set_property("volume", linear)
            except Exception as e:
                self.logger.warning("Could not update volume gain: %s", e)

    # =========================================================================
    # Position and Seeking
    # =========================================================================

    def get_position(self) -> Optional[int]:
        """Get current playback position in seconds."""
        if not self._requires_gst():
            return None
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
        if not self._requires_gst():
            return False
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
    #
    # Thin pass-throughs: the overlay elements and the QR geometry belong to
    # self.overlays. Transient notifications are the playback layer's job
    # (kbox.playback.overlay.OverlayCoordinator), not this one's.
    # =========================================================================

    def set_overlay_text(self, text: str):
        """
        Set the overlay text. Use empty string to clear the overlay.

        Args:
            text: Text to display, or empty string to hide
        """
        if not self._requires_gst():
            return
        self.overlays.set_overlay_text(text)

    def update_qr_overlay(self, image_path: str):
        """
        Update QR code overlay image.

        Args:
            image_path: Path to the QR code PNG image
        """
        # No _requires_gst() guard, unlike its siblings above. With GStreamer
        # missing self.overlays holds no elements, so its own element guard
        # already covers that case -- and going through it is what records the
        # path for a pipeline that may yet be built.
        self.overlays.update_qr_overlay(image_path)

    def set_qr_visible(self, visible: bool):
        """
        Toggle QR code visibility.

        Args:
            visible: True to show, False to hide
        """
        if not self._requires_gst():
            return
        self.overlays.set_qr_visible(visible)

    # =========================================================================
    # Static Image Display
    # =========================================================================

    def display_image(self, image_path: str):
        """
        Display a static image (interstitial screen).

        The image is displayed indefinitely until another file is loaded.
        Interstitials are silent, so no audio is involved at all now that they
        no longer go through playbin.

        Args:
            image_path: Path to the image file to display
        """
        self.logger.debug("Displaying image: %s", image_path)

        if not self._requires_gst():
            return

        Gst = _get_gst()

        # Mark that we're showing an interstitial
        self._is_interstitial = True

        # Only one thing may feed the display bridge at a time.
        self.playbin.set_state(Gst.State.NULL)

        if not self.still_image.start(image_path):
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

        # Guard against a stale flag only. Interstitials no longer run through
        # playbin at all -- they have their own imagefreeze pipeline, which
        # never reaches EOS -- so this bus should only ever see songs ending.
        if self._is_interstitial:
            self.logger.debug("Ignoring EOS while an interstitial is showing")
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

    def _dispatch_playbin_message(self, bus, msg):
        """Route one message from playbin's bus to its handler."""
        Gst = _get_gst()
        if msg.type == Gst.MessageType.EOS:
            self._on_eos(bus, msg)
        elif msg.type == Gst.MessageType.ERROR:
            self._on_error(bus, msg)
        elif msg.type == Gst.MessageType.WARNING:
            self._on_warning(bus, msg)

    def _start_bus_polling(self):
        """
        Start the threads that deliver bus messages.

        One thread per bus (playbin's and the display pipeline's), so neither
        bus's latency depends on the other. Polling is the only delivery path:
        bus.add_signal_watch() needs a GLib main loop and kbox does not run
        one, so a connected signal handler would never fire.

        Each watcher's bus provider returns None once its pipeline is gone,
        which is how teardown ends the loop rather than racing it.
        """
        self.bus_watchers = [
            BusWatcher(
                "GstBusPoll",
                lambda: self.playbin.get_bus() if self.playbin else None,
                self._dispatch_playbin_message,
            ),
            BusWatcher(
                "GstDisplayBusPoll",
                lambda: self.display.bus() if self.display else None,
                lambda bus, msg: self.display.dispatch_bus_message(bus, msg)
                if self.display
                else None,
            ),
        ]
        for watcher in self.bus_watchers:
            watcher.start()

    def _stop_bus_polling(self):
        """Stop the bus polling threads."""
        for watcher in self.bus_watchers:
            watcher.stop()
        self.bus_watchers = []

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
        if not self._requires_gst():
            return "null"
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
