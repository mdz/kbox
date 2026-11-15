"""
Unit tests for PeriodicThread.

No sleeps and no clock: the loop is either driven synchronously on the test
thread with the stop Event's wait() recorded, or run on its real thread with
an hour-long interval and synchronised through Events. In the latter, the
join() timeout is only a guard so a broken stop fails instead of hanging --
with the hour-long interval, a stop that waited out the pause could never
finish within it.
"""

import threading
from unittest.mock import Mock, patch

from kbox.periodic import PeriodicThread

INTERVAL = 7.0
BACKOFF = 11.0
# Long enough that the test would hang if a stop had to outwait it.
NEVER = 3600.0
# Guard on joins below; a prompt stop finishes far inside it.
JOIN_GUARD = 10.0


def _run_loop_synchronously(periodic, stop_after_waits):
    """Run periodic._loop() on this thread, recording each pause it takes.

    Requests a stop once `stop_after_waits` pauses have been taken, so the
    loop ends without a second thread and without waiting on anything.
    """
    waits = []

    def fake_wait(timeout=None):
        waits.append(timeout)
        if len(waits) >= stop_after_waits:
            periodic.request_stop()
        return periodic._stop.is_set()

    with patch.object(periodic._stop, "wait", fake_wait):
        periodic._loop()
    return waits


class TestLoop:
    def test_ticks_once_per_interval_until_stopped(self):
        on_tick = Mock()
        periodic = PeriodicThread(on_tick, INTERVAL, BACKOFF, name="Test")

        waits = _run_loop_synchronously(periodic, stop_after_waits=3)

        assert on_tick.call_count == 3
        assert waits == [INTERVAL] * 3

    def test_a_raising_tick_does_not_stop_the_loop(self):
        """One bad tick must not kill the thread -- auto-start depends on it."""
        on_tick = Mock(side_effect=[RuntimeError("boom"), None])
        periodic = PeriodicThread(on_tick, INTERVAL, BACKOFF, name="Test")

        waits = _run_loop_synchronously(periodic, stop_after_waits=2)

        assert on_tick.call_count == 2
        # Longer pause after the failure, normal interval after the good tick
        assert waits == [BACKOFF, INTERVAL]

    def test_raising_tick_is_logged_against_the_thread_name(self, caplog):
        on_tick = Mock(side_effect=RuntimeError("boom"))
        periodic = PeriodicThread(on_tick, INTERVAL, BACKOFF, name="ContentMonitor")

        _run_loop_synchronously(periodic, stop_after_waits=1)

        assert "Error in ContentMonitor: boom" in caplog.text

    def test_does_not_tick_once_stop_was_requested(self):
        on_tick = Mock()
        periodic = PeriodicThread(on_tick, INTERVAL, BACKOFF, name="Test")
        periodic.request_stop()

        periodic._loop()

        on_tick.assert_not_called()


class TestStop:
    @staticmethod
    def _started(on_tick, interval=NEVER, backoff=NEVER):
        periodic = PeriodicThread(on_tick, interval, backoff, name="Test")
        periodic.start()
        return periodic

    def test_request_stop_interrupts_the_interval(self):
        ticked = threading.Event()
        periodic = self._started(ticked.set)
        assert ticked.wait(JOIN_GUARD)

        periodic.request_stop()
        periodic.join(timeout=JOIN_GUARD)

        assert not periodic.is_alive()

    def test_request_stop_interrupts_the_error_backoff(self):
        raised = threading.Event()

        def on_tick():
            raised.set()
            raise RuntimeError("boom")

        periodic = self._started(on_tick)
        assert raised.wait(JOIN_GUARD)

        periodic.request_stop()
        periodic.join(timeout=JOIN_GUARD)

        assert not periodic.is_alive()

    def test_stop_waits_for_the_thread(self):
        ticked = threading.Event()
        periodic = self._started(ticked.set)
        assert ticked.wait(JOIN_GUARD)

        periodic.stop(timeout=JOIN_GUARD)

        assert not periodic.is_alive()

    def test_running_tracks_start_and_request_stop(self):
        periodic = PeriodicThread(Mock(), NEVER, NEVER, name="Test")
        assert periodic.running is False

        periodic.start()
        assert periodic.running is True

        periodic.stop(timeout=JOIN_GUARD)
        assert periodic.running is False

    def test_can_be_restarted_after_stopping(self):
        ticks = []
        second_tick = threading.Event()

        def on_tick():
            ticks.append(1)
            if len(ticks) == 2:
                second_tick.set()

        periodic = self._started(on_tick)
        periodic.stop(timeout=JOIN_GUARD)

        periodic.start()
        assert second_tick.wait(JOIN_GUARD)
        periodic.stop(timeout=JOIN_GUARD)

    def test_start_does_not_start_a_second_thread(self):
        periodic = PeriodicThread(Mock(), INTERVAL, BACKOFF, name="Test")

        with patch("kbox.periodic.threading.Thread") as thread_cls:
            thread_cls.return_value.is_alive.return_value = True
            periodic.start()
            periodic.start()

        thread_cls.assert_called_once()

    def test_join_warns_when_the_thread_outlives_the_timeout(self, caplog):
        periodic = PeriodicThread(Mock(), INTERVAL, BACKOFF, name="Test")
        periodic._thread = Mock()
        periodic._thread.is_alive.return_value = True

        periodic.join(timeout=0)

        periodic._thread.join.assert_called_once_with(timeout=0)
        assert "Test thread did not stop within timeout" in caplog.text

    def test_join_before_start_is_a_no_op(self):
        PeriodicThread(Mock(), INTERVAL, BACKOFF, name="Test").join()
