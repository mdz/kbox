"""
Upcoming songs and when each is expected to start.

Pure: given the queue order, the cursor, and the current song's position,
it works out what plays next and roughly when. The controller supplies that
state; this module holds none of its own.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence

from ..models import QueueItem


@dataclass(frozen=True)
class UpcomingItem:
    """A queued song that hasn't played yet, with when it is expected to start."""

    item: QueueItem
    position: int  # 1-based among upcoming songs (1 = up next)
    wait_seconds: int  # estimated time until this song starts


def upcoming_songs(
    queue: Sequence[QueueItem],
    cursor_id: Optional[int],
    current_song_id: Optional[int],
    position_seconds: Optional[float],
) -> List[UpcomingItem]:
    """
    The songs after the cursor, each with an estimated time until it starts.

    The estimate is the time left in the song now playing plus the durations
    of the upcoming songs ahead of it. With no cursor (or a cursor song no
    longer in the queue) the whole queue is upcoming.
    """
    cursor_idx = next((i for i, s in enumerate(queue) if s.id == cursor_id), None)
    upcoming_items = queue[(cursor_idx + 1) if cursor_idx is not None else 0 :]

    wait = 0.0
    current = next((s for s in queue if s.id == current_song_id), None)
    if current is not None:
        wait = max(0.0, (current.metadata.duration_seconds or 0) - (position_seconds or 0))

    upcoming = []
    for position, item in enumerate(upcoming_items, start=1):
        upcoming.append(UpcomingItem(item, position, int(wait)))
        wait += item.metadata.duration_seconds or 0
    return upcoming
