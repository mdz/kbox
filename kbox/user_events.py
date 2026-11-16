"""Typed user interaction events stored in the user_events table.

Each event is a frozen dataclass whose fields are its ``data_json`` payload,
with its ``event_type`` string as a class constant. EventRepository turns
an event into a row; nothing else needs to know the JSON shape.
"""

from dataclasses import dataclass
from typing import ClassVar, Optional


@dataclass(frozen=True)
class UserEvent:
    """Base class for events. Subclasses set ``event_type``."""

    event_type: ClassVar[str]


@dataclass(frozen=True)
class SearchPerformed(UserEvent):
    """A guest ran a search."""

    event_type: ClassVar[str] = "search"

    query: str
    result_count: int


@dataclass(frozen=True)
class PitchStarted(UserEvent):
    """A song started playing at this pitch."""

    event_type: ClassVar[str] = "pitch_start"

    queue_item_id: int
    video_id: str
    semitones: int


PHASE_PLAYING = "playing"
PHASE_QUEUED = "queued"


@dataclass(frozen=True)
class PitchChanged(UserEvent):
    """The pitch of a queue item was changed.

    ``phase`` is PHASE_PLAYING for a change made mid-song, which also carries
    the pitch that play started at and the playback position, or
    PHASE_QUEUED for an edit before the song plays.
    """

    event_type: ClassVar[str] = "pitch_change"

    queue_item_id: int
    video_id: str
    old_semitones: int
    new_semitones: int
    phase: str
    changed_by: Optional[str]  # user id of whoever made the change, if known
    changed_by_singer: bool
    start_semitones: Optional[int] = None
    position_seconds: Optional[int] = None


@dataclass(frozen=True)
class SongStopped(UserEvent):
    """A song was stopped partway through because it wasn't working.

    Stored under the singer's user id. It is not a performance (nothing is
    written to playback_history), and a stop by the singer themselves is
    evidence that something was wrong with this track for them.
    """

    event_type: ClassVar[str] = "song_stopped"

    queue_item_id: int
    video_id: str
    position_seconds: int
    duration_seconds: Optional[int]
    stopped_by: Optional[str]  # user id of whoever stopped it, if known
    stopped_by_singer: bool
