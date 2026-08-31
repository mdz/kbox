"""
StreamingController facade: transport controls and pipeline lifecycle.

Uses a persistent playbin pipeline with custom sink bins for pitch shifting.
The pipeline is created at initialization and stays alive, switching between
READY (idle) and PLAYING (song) states.
"""

import logging
import sys
from typing import Any, Optional

from . import gst
from .bus import BusMixin
from .overlays import OverlayMixin
from .pipelines import PipelineMixin
from .still_image import StillImageMixin


class StreamingController(PipelineMixin, OverlayMixin, StillImageMixin, BusMixin):
    """Controls GStreamer pipeline for audio/video playback."""

    # intervideosink/intervideosrc channel connecting playbin to the display
    # pipeline. Any string works as long as both ends agree.
    DISPLAY_CHANNEL = "kbox-display"

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

        # Always-on display pipeline (set by _create_display_pipeline). Owns
        # the screen for the whole process lifetime -- see that method.
        self.display_pipeline: Any = None
        self._display_size: Optional[tuple] = None  # (width, height) if known
        self._render_size: Optional[tuple] = None  # fixed frame size fed to the sink

        # Feeds still images into the display bridge while an interstitial is
        # showing. Mutually exclusive with playbin: only one may feed at once.
        self.interstitial_pipeline: Any = None

        # Overlay elements (set by _create_display_pipeline)
        self.qr_overlay = None
        self.text_overlay = None
        self._notification_timer = None
        self._notification_lock = None
        self._qr_image_path: Optional[str] = None  # Track QR image path for reinit

        # Interstitial state
        self._is_interstitial = False  # True when displaying interstitial (not a song)

        # GStreamer initialization state
        self._gst_initialized = False
        self._gst_missing = gst._get_gst() is None

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
        Gst = gst._get_gst()

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

        Gst = gst._get_gst()

        self.logger.debug("load_file: entry, current_state=%s", self.state)

        # Clear interstitial flag - we're loading a real song
        self._is_interstitial = False

        # Hand the display bridge over to playbin: the interstitial pipeline
        # must stop feeding it before playbin starts, or both would.
        self._stop_interstitial_pipeline()

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

        Gst = gst._get_gst()
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

        Gst = gst._get_gst()
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

        Gst = gst._get_gst()
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

        # Cancel notification timer
        if self._notification_timer:
            self._notification_timer.cancel()
            self._notification_timer = None

        # Stop bus polling first
        self._stop_bus_polling()

        self._stop_interstitial_pipeline()

        if self.playbin:
            try:
                Gst = gst._get_gst()
                self.playbin.set_state(Gst.State.NULL)
                self.playbin = None
            except Exception as e:
                self.logger.error("Error stopping pipeline: %s", e, exc_info=True)

        # Release the screen last, so it stays claimed until we are really
        # shutting down rather than blinking during teardown.
        if self.display_pipeline:
            try:
                Gst = gst._get_gst()
                self.display_pipeline.set_state(Gst.State.NULL)
                self.display_pipeline = None
            except Exception as e:
                self.logger.error("Error stopping display pipeline: %s", e, exc_info=True)

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

        self._stop_interstitial_pipeline()

        # Set pipeline to NULL and release resources
        if self.playbin:
            Gst = gst._get_gst()
            self.playbin.set_state(Gst.State.NULL)
            # Pipeline will be garbage collected
            self.playbin = None
            self.audio_bin = None
            self.video_bin = None
            self.pitch_shift_element = None
            self.volume_element = None
            self.qr_overlay = None
            self.text_overlay = None

        # Tear the display pipeline down too -- it holds the DRM device, and
        # the rebuilt one cannot claim the screen until this one lets go.
        # This does briefly show the console, but only on an explicit config
        # change, not on the per-song path that #94 is about.
        if self.display_pipeline:
            Gst = gst._get_gst()
            self.display_pipeline.set_state(Gst.State.NULL)
            self.display_pipeline.get_state(5 * Gst.SECOND)
            self.display_pipeline = None
            self._display_size = None

        # Reset state
        self.state = "idle"
        self.current_file = None
        self._is_interstitial = False

        # Recreate the pipeline with fresh config
        self._create_persistent_pipeline()

        # Re-apply QR overlay image if we had one
        if self._qr_image_path and self.qr_overlay:
            self.logger.debug("Re-applying QR overlay after pipeline reinit")
            self.update_qr_overlay(self._qr_image_path)

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
            Gst = gst._get_gst()
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
            Gst = gst._get_gst()
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
            Gst = gst._get_gst()
            # Use 1 second timeout instead of waiting forever
            _, state, _ = self.playbin.get_state(Gst.SECOND)
            return state.value_nick
        except Exception as e:
            self.logger.warning("Error getting pipeline state: %s", e)
            return "unknown"
