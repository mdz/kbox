"""
The thread that periodically pokes the playback controller.

PlaybackMonitor owns the thread's lifecycle and nothing else: start, stop,
the interval, and swallowing an exception from one tick so the next one still
happens. What a tick *does* is policy that reads and writes the controller's
state machine (self.state, self.current_song_id, the "already shown"
notification flags) and can call play(), so it stays on the controller and
arrives here as a single callable. That is the honest boundary for a thread --
the same one BusWatcher draws in kbox/streaming/bus.py.

The monitor takes no lock. Whatever locking a tick needs is the callback's
business, and the controller's _check_* methods do their own check-then-lock-
then-recheck under the playback lock.
"""

import logging
import threading
import time
from typing import Callable, Optional

# Check every 2 seconds.
INTERVAL_SECONDS = 2
# Wait longer after a tick raised, so a persistent failure doesn't spin.
ERROR_BACKOFF_SECONDS = 5
# How long shutdown() waits for the thread to notice it should stop.
JOIN_TIMEOUT_SECONDS = 2.0


class PlaybackMonitor:
    """Calls `on_tick` on its own thread, every interval, until stopped."""

    def __init__(
        self,
        on_tick: Callable[[], None],
        interval_seconds: float = INTERVAL_SECONDS,
        error_backoff_seconds: float = ERROR_BACKOFF_SECONDS,
        name: str = "PlaybackMonitor",
    ):
        """
        Args:
            on_tick: called once per interval. An exception from it is logged
                and the loop continues, after a longer pause.
            interval_seconds: pause between ticks
            error_backoff_seconds: pause after a tick raised
            name: thread name
        """
        self._on_tick = on_tick
        self._interval_seconds = interval_seconds
        self._error_backoff_seconds = error_backoff_seconds
        self._name = name
        self.logger = logging.getLogger(__name__)

        self._running = False
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        """False before start() and once stop() has been called."""
        return self._running

    def is_alive(self) -> bool:
        """True while the monitor thread is still running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Start the monitor thread, unless one is already running."""
        if self.is_alive():
            return

        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name=self._name)
        self._thread.start()
        self.logger.info("Playback monitor started")

    def _loop(self):
        """Periodically check playback state and handle notifications, auto-start, etc."""
        while self._running:
            try:
                self._on_tick()
                time.sleep(self._interval_seconds)
            except Exception as e:
                self.logger.error("Error in monitor: %s", e, exc_info=True)
                time.sleep(self._error_backoff_seconds)

    def request_stop(self):
        """
        Ask the monitor to stop, without waiting for the thread to exit.

        Separate from join() so a caller can take the monitor out of play
        first, tear other things down, and only then wait -- which is what
        PlaybackController.shutdown() does, cancelling the transition timer
        in between so it cannot fire during the wait.
        """
        self._running = False

    def join(self, timeout: float = JOIN_TIMEOUT_SECONDS):
        """
        Wait for the monitor thread to exit, warning if it doesn't.

        Args:
            timeout: seconds to wait. The thread spends almost all its time
                in the interval sleep, so this needs to exceed the interval
                to normally succeed.
        """
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                self.logger.warning("Monitor thread did not stop within timeout")
