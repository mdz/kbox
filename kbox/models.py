"""
Data models for kbox.

Defines typed dataclasses for all entities used throughout the application.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class User:
    """User entity with UUID-based identity.

    Identity is self-declared and unverified by design (see
    ldocs/GUEST_IDENTITY_CONTINUITY.md): `id` is the durable key everything
    else foreign-keys against, `normalized_name` is a lookup path onto it
    (not itself unique — a name shared by two real guests is an expected,
    designed-for case), and `icon`/`color` exist purely to make same-named
    guests visually distinguishable in a recognition list.
    """

    id: str
    display_name: str
    created_at: Optional[datetime] = None
    normalized_name: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    last_seen_at: Optional[datetime] = None


@dataclass
class SongMetadata:
    """Song metadata (title, duration, thumbnail, etc.)."""

    title: str  # Original video title (always preserved)
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    channel: Optional[str] = None
    # Extracted metadata (None if extraction failed/unavailable)
    artist: Optional[str] = None  # e.g., "Journey"
    song_name: Optional[str] = None  # e.g., "Don't Stop Believin'"


@dataclass
class SongSettings:
    """Song playback settings (pitch, etc.)."""

    pitch_semitones: int = 0
    # Future settings can be added here


@dataclass
class QueueItem:
    """Queue item representing a song in the queue."""

    id: int
    position: int
    user_id: str
    user_name: str
    video_id: str  # Opaque video ID like "youtube:abc123"
    metadata: SongMetadata
    settings: SongSettings
    content_status: str = "pending"
    content_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    session_id: Optional[int] = None


@dataclass
class HistoryRecord:
    """Playback history record."""

    id: int
    video_id: str  # Opaque video ID like "youtube:abc123"
    user_id: str
    user_name: str
    metadata: SongMetadata
    settings: SongSettings
    performance: Dict[str, Any]  # Performance metrics
    performed_at: Optional[datetime] = None
    theme: Optional[str] = None
    session_id: Optional[int] = None


@dataclass
class Session:
    """A party session — a bounded period of queueing/singing activity.

    Sessions are bookended by the clear-queue action: the current session
    ends (ended_at set) and a fresh session begins.
    """

    id: int
    created_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    theme: Optional[str] = None


@dataclass
class Favorite:
    """A song a user has starred to remember for later, independent of the queue."""

    user_id: str
    video_id: str  # Opaque video ID like "youtube:abc123"
    metadata: SongMetadata
    created_at: Optional[datetime] = None


@dataclass
class ConfigEntry:
    """Configuration entry."""

    key: str
    value: str
    updated_at: Optional[datetime] = None
