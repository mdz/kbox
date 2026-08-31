"""
EOS/error/warning bus message handlers, and the bus polling thread.
"""

import logging
import threading
from typing import TYPE_CHECKING, Any

from . import gst


class BusMixin:
    """StreamingController methods for handling playbin bus messages."""

    if TYPE_CHECKING:
        # Provided by StreamingController.__init__ and the other mixins it
        # combines with; declared here only so mypy can typecheck this
        # mixin's methods on their own.
        logger: logging.Logger
        playbin: Any
        _is_interstitial: bool

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

    def _start_bus_polling(self):
        """Start a thread to poll the bus for messages."""
        self._bus_poll_running = True

        def poll_bus():
            Gst = gst._get_gst()
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
