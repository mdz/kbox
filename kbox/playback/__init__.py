"""
Playback controller for kbox.

Orchestrates playback, manages state transitions, and handles error recovery.

The package splits that into pieces that each own their own state (or none at
all), following the same shape as kbox/streaming/:

- history_policy: pure threshold/percentage decisions about performance history
- song_tuning: per-song trim-point and loudness-gain lookups
- status: serialization of a queue item for the status API
- upcoming: which songs play next and an estimated start time for each
- controller: PlaybackController, the state machine that wires them together
"""

from .controller import PlaybackController, PlaybackState
from .upcoming import UpcomingItem, upcoming_songs

__all__ = ["PlaybackController", "PlaybackState", "UpcomingItem", "upcoming_songs"]
