"""
Threads that deliver GStreamer bus messages to their handlers.

Polling is the only delivery path: bus.add_signal_watch() dispatches from a
GLib main loop, and kbox does not run one (there is no GLib.MainLoop anywhere
in the tree; the app's event loop is uvicorn's), so a connected signal handler
would never fire. See docs/development/gstreamer-pipeline.md.

BusWatcher owns the thread and nothing else. What a message *means* is policy
belonging to whoever owns the bus, so it arrives here as a dispatch callable
-- the honest boundary for a thread is its lifecycle.
"""

import logging
import threading
from typing import Any, Callable, Optional

from .gst import _get_gst

# Bus poll timeout. Short enough that stop() returns promptly, long enough
# that the thread is idle between messages.
POLL_TIMEOUT_MS = 100


class BusWatcher:
    """Polls one bus on its own thread, handing each message to `dispatch`."""

    def __init__(
        self,
        name: str,
        bus_provider: Callable[[], Any],
        dispatch: Callable[[Any, Any], None],
    ):
        """
        Args:
            name: thread name, and what a failed dispatch is logged against
            bus_provider: called to get the bus to poll; returning None means
                there is nothing left to poll and the thread should finish.
                That is how the watcher notices its pipeline being torn down
                underneath it -- teardown clears the attribute, and the bus of
                a NULL-state pipeline is of no further use.
            dispatch: called with (bus, message) for each message
        """
        self.name = name
        self._bus_provider = bus_provider
        self._dispatch = dispatch
        self.logger = logging.getLogger(__name__)

        self._running = False
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        """False once stop() has been called."""
        return self._running

    def is_alive(self) -> bool:
        """True while the polling thread is still running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Start the polling thread."""
        self._running = True
        self._thread = threading.Thread(target=self._poll, daemon=True, name=self.name)
        self._thread.start()

    def _poll(self):
        Gst = _get_gst()
        while self._running:
            # Re-read the bus each pass, so teardown ends the loop rather
            # than racing it.
            bus = self._bus_provider()
            if bus is None:
                return
            msg = bus.timed_pop(POLL_TIMEOUT_MS * Gst.MSECOND)  # 100ms timeout
            if msg:
                try:
                    self._dispatch(bus, msg)
                except Exception:
                    self.logger.exception("Error handling bus message on %s", self.name)

    def stop(self):
        """Stop the polling thread and wait briefly for it to exit."""
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1)
