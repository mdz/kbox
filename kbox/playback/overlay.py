"""
The on-screen overlay text: who is singing now, who is up next, and
transient notifications that interrupt them.

OverlayCoordinator owns the base text, the hide timer and the lock that
serialises the two. It takes the streaming controller, so it can equally
well be constructed with a stub that only implements set_overlay_text.

LOCK ORDERING: this object's lock is the INNER lock. PlaybackController's
playback lock is the OUTER one, and code holding it may call in here. The
reverse must NEVER happen: nothing executed while holding the lock below may
call back into a method that takes the playback lock. The timer callback is
the one piece of code here that runs on a thread of its own, and it only
touches overlay state, which is what keeps that one-directional.
"""

import logging
import threading
from typing import Optional


class OverlayCoordinator:
    """Keeps a persistent overlay text, with transient notifications over it."""

    def __init__(self, streaming_controller):
        """
        Args:
            streaming_controller: StreamingController instance; only
                set_overlay_text() is used.
        """
        self.streaming_controller = streaming_controller
        self.logger = logging.getLogger(__name__)

        self._base_overlay_text = ""  # Persistent overlay (current singer, up next, etc.)
        self._notification_timer: Optional[threading.Timer] = None
        # INNER lock - see the lock-ordering note in the module docstring.
        # May be acquired while the playback lock is held; must never be held
        # while acquiring the playback lock.
        self._notification_lock = threading.Lock()

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
        with self._notification_lock:
            self._base_overlay_text = text
            # Only update display if no notification is active
            if self._notification_timer is None:
                self.streaming_controller.set_overlay_text(text)

    def show_notification(self, text: str, duration_seconds: float = 5.0):
        """
        Show a transient notification that auto-hides and restores the base overlay.

        Args:
            text: Notification text to display
            duration_seconds: How long to show the notification (default 5s)
        """
        with self._notification_lock:
            # Cancel any pending timer
            if self._notification_timer:
                self._notification_timer.cancel()
                self._notification_timer = None

            # Show the notification
            self.streaming_controller.set_overlay_text(text)
            self.logger.info("Showing notification: %s", text)

            # Schedule restoration of base overlay
            def restore_base_overlay():
                with self._notification_lock:
                    self._notification_timer = None
                    self.streaming_controller.set_overlay_text(self._base_overlay_text)
                    self.logger.debug("Restored base overlay: %s", self._base_overlay_text)

            self._notification_timer = threading.Timer(duration_seconds, restore_base_overlay)
            self._notification_timer.daemon = True
            self._notification_timer.start()
