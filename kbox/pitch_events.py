"""Recording of pitch choices in user_events, as evidence of singers' comfort.

playback_history keeps only the pitch a song ended on, and most songs end on
0 simply because nobody touched the control. The real evidence of what a
singer finds comfortable is a deliberate change -- above all a correction
made mid-song -- so every change is recorded here, along with the pitch each
play started at, for future pitch suggestions (issue #215).

Both event types are stored under the *singer's* user id (the owner of the
queue item), since they describe that singer's comfort; who actually made a
change is kept in the event data.

``pitch_start`` -- a song started playing::

    {"queue_item_id", "video_id", "semitones"}

``pitch_change`` -- the pitch of a queue item was changed::

    {"queue_item_id", "video_id", "old_semitones", "new_semitones",
     "phase",              # "playing" (mid-song) or "queued" (before playback)
     "start_semitones",    # pitch this play started at; None when queued
     "position_seconds",   # playback position; None when queued or unknown
     "changed_by",         # user id of whoever made the change, or None
     "changed_by_singer"}  # whether that was the singer themself

Recording is best-effort: a failure is logged and never interrupts playback
or the request that made the change.
"""

import logging
from typing import Any, Dict, Optional

from .models import QueueItem

PITCH_START_EVENT = "pitch_start"
PITCH_CHANGE_EVENT = "pitch_change"

PHASE_PLAYING = "playing"
PHASE_QUEUED = "queued"

logger = logging.getLogger(__name__)


def record_pitch_start(event_repo, song: QueueItem) -> None:
    """Record the pitch a song started playing at."""
    _record(
        event_repo,
        song.user_id,
        PITCH_START_EVENT,
        {
            "queue_item_id": song.id,
            "video_id": song.video_id,
            "semitones": song.settings.pitch_semitones,
        },
    )


def record_pitch_change(
    event_repo,
    song: QueueItem,
    new_semitones: int,
    *,
    phase: str,
    changed_by: Optional[str],
    start_semitones: Optional[int] = None,
    position_seconds: Optional[int] = None,
) -> None:
    """Record a change to a queue item's pitch.

    Args:
        event_repo: EventRepository (or None, to record nothing)
        song: The queue item as it was *before* the change
        new_semitones: The pitch it was changed to
        phase: PHASE_PLAYING or PHASE_QUEUED
        changed_by: User id of whoever made the change, if known
        start_semitones: Pitch the current play started at (PHASE_PLAYING)
        position_seconds: Playback position at the change (PHASE_PLAYING)
    """
    old_semitones = song.settings.pitch_semitones
    if old_semitones == new_semitones:
        return
    _record(
        event_repo,
        song.user_id,
        PITCH_CHANGE_EVENT,
        {
            "queue_item_id": song.id,
            "video_id": song.video_id,
            "old_semitones": old_semitones,
            "new_semitones": new_semitones,
            "phase": phase,
            "start_semitones": start_semitones,
            "position_seconds": position_seconds,
            "changed_by": changed_by,
            "changed_by_singer": changed_by is not None and changed_by == song.user_id,
        },
    )


def _record(event_repo, user_id: str, event_type: str, data: Dict[str, Any]) -> None:
    if event_repo is None:
        return
    try:
        event_repo.record(user_id, event_type, data)
    except Exception:
        logger.warning("Failed to record %s event", event_type, exc_info=True)
