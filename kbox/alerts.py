"""
Alerts: telling particular people that something needs their attention.

An alert is addressed to people -- guests by user ID, and whoever is
operating -- never to a device or a transport. Whatever notices a problem
publishes an alert here and resolves it once the problem is gone; how the
alert reaches anyone is up to the delivery channels, so a new channel works
for every kind of alert without the code that raised them changing.

Delivery channels today:

- Phones. Each open kbox page polls the queue endpoint, which includes the
  alerts addressed to that viewer (see for_viewer()). The page buzzes and
  shows each alert the first time it sees its id. That only works while the
  page is open and the phone unlocked, and the buzz only where the browser
  supports vibration (Android, not iOS).
- The TV, for an alert with a room_message: a short line on the display, so
  someone in the room can tell a person who isn't looking at their phone.

A channel that pushes rather than waits to be polled (Web Push, say)
subscribes with subscribe() and is called on every publish().
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Optional

# Kinds of alert. The phone page picks what tapping an alert does by kind.
KIND_DOWNLOAD_FAILED = "download_failed"


@dataclass(frozen=True)
class Alert:
    """
    One problem, addressed to the people who should hear about it.

    key identifies the problem itself ("download_failed:262"), so resolving
    it takes only the key, and publishing the same key again replaces the
    earlier alert. id is new on every publish: a client buzzes once per id,
    so a problem that comes back after being resolved buzzes again.
    """

    key: str
    kind: str
    # For the guests in user_ids.
    message: str
    user_ids: FrozenSet[str] = frozenset()
    # For operators; None means operators are not told.
    operator_message: Optional[str] = None
    # For everyone in the room, on the TV; None means the TV stays quiet.
    room_message: Optional[str] = None
    queue_item_id: Optional[int] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


AlertListener = Callable[[Alert], None]


class AlertCenter:
    """The active alerts, and the channels that want to hear of new ones."""

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._active: Dict[str, Alert] = {}
        self._listeners: List[AlertListener] = []

    def subscribe(self, listener: AlertListener) -> None:
        """
        Call listener with every alert published from now on.

        The listener runs on the publishing thread, after the alert is
        active. An exception from it is logged and does not reach the
        publisher or the other listeners.
        """
        with self._lock:
            self._listeners.append(listener)

    def publish(self, alert: Alert) -> None:
        """Make an alert active (replacing any with its key) and tell the listeners."""
        with self._lock:
            self._active[alert.key] = alert
            listeners = list(self._listeners)
        self.logger.info("Alert raised: %s", alert.key)
        for listener in listeners:
            try:
                listener(alert)
            except Exception:
                self.logger.exception("Alert listener failed for %s", alert.key)

    def resolve(self, key: str) -> None:
        """Drop the alert with this key, if there is one."""
        with self._lock:
            resolved = self._active.pop(key, None)
        if resolved is not None:
            self.logger.info("Alert resolved: %s", key)

    def clear(self) -> None:
        """Drop every alert (e.g. when the queue is cleared)."""
        with self._lock:
            self._active.clear()

    def active(self) -> List[Alert]:
        """Every active alert, oldest first."""
        with self._lock:
            return list(self._active.values())

    def for_viewer(self, user_id: Optional[str], is_operator: bool) -> List[Dict[str, object]]:
        """
        The active alerts addressed to one viewer, as the phone page reads them.

        A viewer who is both the guest an alert names and an operator gets
        it once, with the guest's message: it is their own problem to fix.
        """
        result: List[Dict[str, object]] = []
        for alert in self.active():
            if user_id is not None and user_id in alert.user_ids:
                message = alert.message
            elif is_operator and alert.operator_message is not None:
                message = alert.operator_message
            else:
                continue
            result.append(
                {
                    "id": alert.id,
                    "kind": alert.kind,
                    "message": message,
                    "queue_item_id": alert.queue_item_id,
                }
            )
        return result
