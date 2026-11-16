"""Log of singers' pitch choices, as evidence of what they find comfortable.

playback_history keeps only the pitch a song ended on, and most songs end on
0 simply because nobody touched the control. The real evidence of comfort is
a deliberate change -- above all a correction made mid-song -- so every
change is logged, along with the pitch each play started at, for future
pitch suggestions (issue #215).

Events are stored under the *singer's* user id (the owner of the queue
item), since they describe that singer's comfort; who actually made a change
is kept in the event (PitchChanged.changed_by).
"""

import logging
from typing import Optional

from .database import Database, EventRepository
from .models import QueueItem
from .user_events import PitchChanged, PitchStarted, UserEvent


class PitchLog:
    """Records pitch choices to user_events.

    Logging is best-effort: a failure to write is logged and swallowed, so it
    never interrupts playback or the request that changed the pitch.
    """

    def __init__(self, database: Database):
        self.repository = EventRepository(database)
        self.logger = logging.getLogger(__name__)

    def song_started(self, song: QueueItem) -> None:
        """Log the pitch a song started playing at."""
        self._record(
            song.user_id,
            PitchStarted(
                queue_item_id=song.id,
                video_id=song.video_id,
                semitones=song.settings.pitch_semitones,
            ),
        )

    def pitch_changed(
        self,
        song: QueueItem,
        new_semitones: int,
        *,
        phase: str,
        changed_by: Optional[str],
        start_semitones: Optional[int] = None,
        position_seconds: Optional[int] = None,
    ) -> None:
        """Log a change to a queue item's pitch. Does nothing if unchanged.

        Args:
            song: The queue item as it was *before* the change
            new_semitones: The pitch it was changed to
            phase: PHASE_PLAYING (mid-song) or PHASE_QUEUED (before playback)
            changed_by: User id of whoever made the change, if known
            start_semitones: Pitch the current play started at (mid-song only)
            position_seconds: Playback position at the change (mid-song only)
        """
        old_semitones = song.settings.pitch_semitones
        if old_semitones == new_semitones:
            return
        self._record(
            song.user_id,
            PitchChanged(
                queue_item_id=song.id,
                video_id=song.video_id,
                old_semitones=old_semitones,
                new_semitones=new_semitones,
                phase=phase,
                changed_by=changed_by,
                changed_by_singer=changed_by is not None and changed_by == song.user_id,
                start_semitones=start_semitones,
                position_seconds=position_seconds,
            ),
        )

    def _record(self, user_id: str, event: UserEvent) -> None:
        try:
            self.repository.record(user_id, event)
        except Exception:
            self.logger.warning("Failed to record %s event", event.event_type, exc_info=True)
