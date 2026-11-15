"""
A daemon thread that calls one function every N seconds until stopped.

PeriodicThread owns the thread's lifecycle and nothing else: start, stop, the
interval, and swallowing an exception from one tick so the next one still
happens. What a tick *does* is the owner's policy and arrives here as a single
callable -- the same boundary BusWatcher draws in kbox/streaming/bus.py.

It takes no lock. Whatever locking a tick needs is the callback's business.

The pause between ticks is a wait on a threading.Event, not a sleep, so
request_stop() wakes the thread at once: a stop takes effect within one tick,
not one interval, and join() does not have to outwait the interval.
"""

import logging
import threading
from typing import Callable, Optional

# How long join() waits for the thread to finish its current tick.
JOIN_TIMEOUT_SECONDS = 2.0


class PeriodicThread:
    """Calls `on_tick` on its own thread, every interval, until stopped."""

    def __init__(
        self,
        on_tick: Callable[[], None],
        interval_seconds: float,
        error_backoff_seconds: float,
        name: str,
    ):
        """
        Args:
            on_tick: called once per interval. An exception from it is logged
                and the loop continues, after a longer pause.
            interval_seconds: pause between ticks
            error_backoff_seconds: pause after a tick raised, so a persistent
                failure doesn't spin
            name: thread name, and what log messages are attributed to
        """
        self._on_tick = on_tick
        self._interval_seconds = interval_seconds
        self._error_backoff_seconds = error_backoff_seconds
        self.name = name
        self.logger = logging.getLogger(__name__)

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        """True once start() has been called, until request_stop()."""
        return self._thread is not None and not self._stop.is_set()

    def is_alive(self) -> bool:
        """True while the thread is still running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Start the thread, unless one is already running."""
        if self.is_alive():
            return

        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=self.name)
        self._thread.start()
        self.logger.info("%s started", self.name)

    def _loop(self):
        """Tick, then wait out the interval (or the backoff), until stopped."""
        while not self._stop.is_set():
            try:
                self._on_tick()
                pause = self._interval_seconds
            except Exception as e:
                self.logger.error("Error in %s: %s", self.name, e, exc_info=True)
                pause = self._error_backoff_seconds
            self._stop.wait(pause)

    def request_stop(self):
        """
        Ask the thread to stop, without waiting for it to exit.

        Separate from join() so a caller can take the thread out of play
        first, tear other things down, and only then wait -- which is what
        PlaybackController.shutdown() does, cancelling the transition timer
        in between so it cannot fire during the wait.
        """
        self._stop.set()

    def join(self, timeout: float = JOIN_TIMEOUT_SECONDS):
        """
        Wait for the thread to exit, warning if it doesn't.

        Args:
            timeout: seconds to wait. The pause between ticks ends as soon as
                a stop is requested, so this only has to cover a tick that is
                already running.
        """
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                self.logger.warning("%s thread did not stop within timeout", self.name)

    def stop(self, timeout: float = JOIN_TIMEOUT_SECONDS):
        """request_stop() and join(), for callers with nothing to do in between."""
        self.request_stop()
        self.join(timeout)
