"""
Turning domain objects into the shapes the frontend reads.

This is the one place a QueueItem becomes a JSON dict. The flattening of
metadata and settings onto the top level is a frontend convenience, not
something the queue or playback layers should know about -- those hand back
QueueItem and PlaybackStatus, and the shaping happens here, next to the
routes that serve it.
"""

from dataclasses import asdict
from typing import Any, Dict

from ..models import QueueItem


def serialize_queue_item(item: QueueItem) -> Dict[str, Any]:
    """
    Convert a QueueItem to a JSON-serializable dict for the frontend.

    Args:
        item: The queue item to serialize

    Returns:
        Dict with datetimes stringified, and metadata and settings flattened
        onto the top level for easier frontend access.
    """
    # Convert QueueItem to dict for JSON serialization
    item_dict = asdict(item)
    # Convert datetime objects to ISO format strings for JSON
    if item_dict.get("created_at"):
        item_dict["created_at"] = item_dict["created_at"].isoformat()
    # Flatten metadata and settings for easier frontend access
    item_dict["title"] = item.metadata.title
    item_dict["duration_seconds"] = item.metadata.duration_seconds
    item_dict["thumbnail_url"] = item.metadata.thumbnail_url
    item_dict["channel"] = item.metadata.channel
    item_dict["artist"] = item.metadata.artist
    item_dict["song_name"] = item.metadata.song_name
    item_dict["pitch_semitones"] = item.settings.pitch_semitones
    return item_dict
