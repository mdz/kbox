"""
Serialization of playback status for the web API.

This is presentation, not playback: the flattening of metadata and settings
onto one dict exists because the frontend reads them that way. Keeping it
here leaves the controller holding only the state machine.
"""

from dataclasses import asdict
from typing import Any, Dict

from ..models import QueueItem


def serialize_current_song(queue_item: QueueItem) -> Dict[str, Any]:
    """
    Convert a QueueItem into the status API's current-song dict.

    Args:
        queue_item: The queue item currently playing

    Returns:
        JSON-serializable dict, with datetimes stringified and metadata and
        settings flattened onto the top level for easier frontend access.
    """
    # Convert QueueItem to dict for JSON serialization
    current_song = asdict(queue_item)
    # Convert datetime objects to ISO format strings for JSON
    if current_song.get("created_at"):
        current_song["created_at"] = current_song["created_at"].isoformat()
    # Flatten metadata and settings for easier frontend access
    current_song["title"] = queue_item.metadata.title
    current_song["duration_seconds"] = queue_item.metadata.duration_seconds
    current_song["thumbnail_url"] = queue_item.metadata.thumbnail_url
    current_song["channel"] = queue_item.metadata.channel
    current_song["artist"] = queue_item.metadata.artist
    current_song["song_name"] = queue_item.metadata.song_name
    current_song["pitch_semitones"] = queue_item.settings.pitch_semitones
    return current_song
