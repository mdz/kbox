"""
A thread that polls a GStreamer bus for EOS and error messages.

Needed because the signal watch on playbin's bus only fires from a GLib main
loop, which kbox does not run.

BusWatcher owns the thread and nothing else. What EOS and errors *mean* is
policy that belongs to whoever is playing, so it arrives here as two
callables -- the honest boundary for a thread is its lifecycle.
"""

import logging
import threading
from typing import Any, Callable, Optional

from .gst import _get_gst

# Bus poll timeout. Short enough that stop() returns promptly, long enough
# that the thread is idle between messages.
POLL_TIMEOUT_MS = 100


class BusWatcher:
    """Polls a bus on its own thread, dispatching EOS and errors to callbacks."""

    def __init__(
        self,
        bus_provider: Callable[[], Any],
        on_eos: Callable[[Any, Any], None],
        on_error: Callable[[Any, Any], None],
    ):
        """
        Args:
            bus_provider: called to get the bus to poll; returning None means
                there is nothing left to poll and the thread should finish.
                (That is how the watcher notices the pipeline being torn down
                underneath it.)
            on_eos: called with (bus, message) for each EOS message
            on_error: called with (bus, message) for each error message
        """
        self._bus_provider = bus_provider
        self._on_eos = on_eos
        self._on_error = on_error
        self.logger = logging.getLogger(__name__)

        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the polling thread."""
        self._running = True
        self._thread = threading.Thread(target=self._poll, daemon=True, name="GstBusPoll")
        self._thread.start()

    def _poll(self):
        Gst = _get_gst()
        bus = self._bus_provider()
        if bus is None:
            return
        while self._running and self._bus_provider() is not None:
            msg = bus.timed_pop(POLL_TIMEOUT_MS * Gst.MSECOND)
            if msg:
                if msg.type == Gst.MessageType.EOS:
                    self._on_eos(bus, msg)
                elif msg.type == Gst.MessageType.ERROR:
                    self._on_error(bus, msg)

    def stop(self):
        """Stop the bus polling thread."""
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1)
