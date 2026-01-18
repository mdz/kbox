"""
Data models for kbox.

Defines typed dataclasses for all entities used throughout the application.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class User:
    """User entity with UUID-based identity."""

    id: str
    display_name: str
    created_at: Optional[datetime] = None


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
    download_status: str = "pending"
    download_path: Optional[str] = None
    error_message: Optional[str] = None
    played_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


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


@dataclass
class ConfigEntry:
    """Configuration entry."""

    key: str
    value: str
    updated_at: Optional[datetime] = None
