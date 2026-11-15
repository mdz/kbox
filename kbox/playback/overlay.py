"""
The on-screen overlay text: who is singing now, who is up next, and
transient notifications that interrupt them.

OverlayCoordinator owns the base text and the deadline of the notification
currently covering it. It takes the streaming controller, so it can equally
well be constructed with a stub that only implements set_overlay_text.

It has no lock and no thread of its own. Every call comes from
PlaybackController under the playback lock, and an expired notification is
cleared by the playback monitor's tick calling expire_notification(), so a
notification lasts until the first tick after its deadline.
"""

import logging
import time
from typing import Callable, Optional


class OverlayCoordinator:
    """Keeps a persistent overlay text, with transient notifications over it.

    Not thread-safe by itself: callers must serialise access (the playback
    lock does).
    """

    def __init__(
        self,
        streaming_controller,
        clock: Callable[[], float] = time.monotonic,
    ):
        """
        Args:
            streaming_controller: StreamingController instance; only
                set_overlay_text() is used.
            clock: Monotonic clock in seconds. Injected so tests can move
                time forward without sleeping.
        """
        self.streaming_controller = streaming_controller
        self.logger = logging.getLogger(__name__)
        self._clock = clock

        self._base_overlay_text = ""  # Persistent overlay (current singer, up next, etc.)
        # When the active notification should give way to the base text, on
        # self._clock's timeline; None while no notification is showing.
        self._notification_deadline: Optional[float] = None

    @property
    def base_text(self) -> str:
        """The current base (persistent) overlay text."""
        return self._base_overlay_text

    def set_base(self, text: str):
        """
        Set the base (persistent) overlay text.

        This is the text that should be shown when no transient notification
        is active (e.g., "Now singing: Alice", "Up next: Bob").

        Args:
            text: The base overlay text, or empty string to clear
        """
        self._base_overlay_text = text
        # Only update display if no notification is active
        if self._notification_deadline is None:
            self.streaming_controller.set_overlay_text(text)

    def show_notification(self, text: str, duration_seconds: float = 5.0):
        """
        Show a transient notification over the base overlay.

        It stays up until expire_notification() is called at or after
        duration_seconds from now. A second notification replaces the first,
        and its deadline replaces the first's.

        Args:
            text: Notification text to display
            duration_seconds: How long to show the notification (default 5s)
        """
        self._notification_deadline = self._clock() + duration_seconds
        self.streaming_controller.set_overlay_text(text)
        self.logger.info("Showing notification: %s", text)

    def expire_notification(self):
        """Restore the base overlay if the active notification's time is up.

        Safe to call at any time; does nothing when no notification is
        showing or the current one has not yet reached its deadline.
        """
        if self._notification_deadline is None:
            return
        if self._clock() < self._notification_deadline:
            return
        self._notification_deadline = None
        self.streaming_controller.set_overlay_text(self._base_overlay_text)
        self.logger.debug("Restored base overlay: %s", self._base_overlay_text)
