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
from kbox.playback.monitor import (
    ERROR_BACKOFF_SECONDS,
    INTERVAL_SECONDS,
    PlaybackMonitor,
)
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


class TestPlaybackMonitor:
    """PlaybackMonitor needs nothing but a callable.

    These drive _loop() directly on the test thread with time.sleep patched
    out, so the loop's behavior is checked without a second thread and
    without waiting on anything.
    """

    @staticmethod
    def _run_loop(monitor, on_tick_count, stop_after):
        """Run monitor._loop() synchronously, stopping after `stop_after` ticks."""
        slept = []

        def fake_sleep(seconds):
            slept.append(seconds)
            if len(on_tick_count) >= stop_after:
                monitor.request_stop()

        with patch("kbox.playback.monitor.time.sleep", fake_sleep):
            monitor._loop()
        return slept

    def test_loop_ticks_until_request_stop(self):
        ticks = []
        monitor = PlaybackMonitor(on_tick=lambda: ticks.append(1))
        monitor._running = True

        slept = self._run_loop(monitor, ticks, stop_after=3)

        assert len(ticks) == 3
        assert slept == [INTERVAL_SECONDS] * 3
        assert monitor.running is False

    def test_a_raising_tick_does_not_stop_the_loop(self):
        """One bad tick must not kill the monitor -- auto-start depends on it."""
        ticks = []

        def on_tick():
            ticks.append(1)
            if len(ticks) == 1:
                raise RuntimeError("boom")

        monitor = PlaybackMonitor(on_tick=on_tick)
        monitor._running = True

        slept = self._run_loop(monitor, ticks, stop_after=2)

        assert len(ticks) == 2
        # Longer pause after the failure, normal interval after the good tick
        assert slept == [ERROR_BACKOFF_SECONDS, INTERVAL_SECONDS]

    def test_start_does_not_start_a_second_thread(self):
        monitor = PlaybackMonitor(on_tick=Mock())

        with patch("kbox.playback.monitor.threading.Thread") as thread_cls:
            thread_cls.return_value.is_alive.return_value = True
            monitor.start()
            monitor.start()

        thread_cls.assert_called_once()

    def test_join_warns_when_the_thread_outlives_the_timeout(self, caplog):
        monitor = PlaybackMonitor(on_tick=Mock())
        monitor._thread = Mock()
        monitor._thread.is_alive.return_value = True

        monitor.join(timeout=0)

        monitor._thread.join.assert_called_once_with(timeout=0)
        assert "did not stop within timeout" in caplog.text
