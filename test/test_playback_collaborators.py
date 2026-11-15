"""
Unit tests for the playback collaborators, constructed directly.

Every test here builds its subject on its own -- no PlaybackController, no
queue manager, no GStreamer. That is the point: if one of these needed a
controller to exist, the boundary would be wrong.

No timing-dependent assertions: the notification hide timer is patched so
its callback can be invoked directly.
"""

from unittest.mock import Mock, patch

from kbox.playback.cursor import QueueCursor
from kbox.playback.overlay import OverlayCoordinator


class TestOverlayCoordinator:
    """OverlayCoordinator needs nothing but something with set_overlay_text."""

    def test_set_base_updates_display(self):
        streaming = Mock()
        coordinator = OverlayCoordinator(streaming)

        coordinator.set_base("Now singing: Alice")

        assert coordinator.base_text == "Now singing: Alice"
        streaming.set_overlay_text.assert_called_once_with("Now singing: Alice")

    def test_notification_restores_base_overlay(self):
        """A notification hides the base text, and the timer brings it back.

        This is the same regression this guards at the controller level (the
        overlay going blank after a notification instead of returning to the
        current singer), checked without waiting on a real timer.
        """
        streaming = Mock()
        coordinator = OverlayCoordinator(streaming)
        coordinator.set_base("Now singing: Alice")
        streaming.reset_mock()

        with patch("kbox.playback.overlay.threading.Timer") as timer_cls:
            coordinator.show_notification("Bob added a song", duration_seconds=5.0)
            streaming.set_overlay_text.assert_called_once_with("Bob added a song")
            restore = timer_cls.call_args[0][1]

        streaming.reset_mock()
        restore()

        streaming.set_overlay_text.assert_called_once_with("Now singing: Alice")

    def test_set_base_during_notification_does_not_interrupt_it(self):
        """Changing the base text while a notification shows is deferred to it."""
        streaming = Mock()
        coordinator = OverlayCoordinator(streaming)

        with patch("kbox.playback.overlay.threading.Timer") as timer_cls:
            coordinator.show_notification("Bob added a song", duration_seconds=5.0)
            restore = timer_cls.call_args[0][1]
            streaming.reset_mock()

            coordinator.set_base("Up next: Carol")

        # The notification is still on screen
        streaming.set_overlay_text.assert_not_called()
        assert coordinator.base_text == "Up next: Carol"

        restore()
        streaming.set_overlay_text.assert_called_once_with("Up next: Carol")

    def test_second_notification_cancels_the_first_timer(self):
        streaming = Mock()
        coordinator = OverlayCoordinator(streaming)

        with patch("kbox.playback.overlay.threading.Timer") as timer_cls:
            first_timer = Mock()
            second_timer = Mock()
            timer_cls.side_effect = [first_timer, second_timer]

            coordinator.show_notification("first", duration_seconds=5.0)
            coordinator.show_notification("second", duration_seconds=5.0)

        first_timer.cancel.assert_called_once()
        second_timer.cancel.assert_not_called()
        streaming.set_overlay_text.assert_called_with("second")


class TestQueueCursor:
    """QueueCursor needs nothing but a config repository."""

    @staticmethod
    def _repository(stored_value=None):
        repo = Mock()
        repo.get.return_value = Mock(value=stored_value) if stored_value is not None else None
        return repo

    def test_loads_persisted_cursor_on_construction(self):
        repo = self._repository("42")

        cursor = QueueCursor(repo)

        assert cursor.get() == 42
        repo.get.assert_called_once_with(QueueCursor.CONFIG_KEY)

    def test_empty_storage_means_no_cursor(self):
        assert QueueCursor(self._repository()).get() is None

    def test_unparseable_stored_value_means_no_cursor(self):
        """A corrupt value must not take playback down at startup."""
        assert QueueCursor(self._repository("not-a-number")).get() is None

    def test_set_persists_the_new_cursor(self):
        repo = self._repository()
        cursor = QueueCursor(repo)

        cursor.set(7)

        assert cursor.get() == 7
        repo.set.assert_called_once_with(QueueCursor.CONFIG_KEY, "7")

    def test_clear_persists_an_empty_cursor(self):
        repo = self._repository("42")
        cursor = QueueCursor(repo)

        cursor.clear()

        assert cursor.get() is None
        repo.set.assert_called_once_with(QueueCursor.CONFIG_KEY, "")
