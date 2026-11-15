"""
Playback controller for kbox.

Orchestrates playback, manages state transitions, and handles error recovery.

The package splits that into pieces that each own their own state (or none at
all), following the same shape as kbox/streaming/:

- history_policy: pure threshold/percentage decisions about performance history
- song_tuning: per-song trim-point and loudness-gain lookups
- upcoming: which songs play next and an estimated start time for each
- cursor: QueueCursor, where we are in the queue, persisted
- overlay: OverlayCoordinator, the base overlay text and notifications over it
- screens: ScreenPresenter, the interstitial screens
- monitor: PlaybackMonitor, the background tick thread's lifecycle
- controller: PlaybackController, the state machine that wires them together
"""

from .controller import (
    PlaybackController,
    PlaybackState,
    PlaybackStatus,
    SeekOutcome,
    SeekResult,
)
from .upcoming import UpcomingItem, upcoming_songs

__all__ = [
    "PlaybackController",
    "PlaybackState",
    "PlaybackStatus",
    "SeekOutcome",
    "SeekResult",
    "UpcomingItem",
    "upcoming_songs",
]
