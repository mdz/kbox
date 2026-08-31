"""Shared JSON codecs used by kbox database repositories."""

import json

from ..models import SongMetadata, SongSettings

# ============================================================================
# Shared JSON Codecs
# ============================================================================


def _encode_metadata(metadata: SongMetadata) -> str:
    """Encode SongMetadata to JSON for storage."""
    return json.dumps(
        {
            "title": metadata.title,
            "duration_seconds": metadata.duration_seconds,
            "thumbnail_url": metadata.thumbnail_url,
            "channel": metadata.channel,
            "artist": metadata.artist,
            "song_name": metadata.song_name,
        }
    )


def _decode_metadata(metadata_json: str) -> SongMetadata:
    """Decode SongMetadata from JSON."""
    if not metadata_json:
        return SongMetadata(title="Unknown")
    try:
        data = json.loads(metadata_json)
        return SongMetadata(
            title=data.get("title", "Unknown"),
            duration_seconds=data.get("duration_seconds"),
            thumbnail_url=data.get("thumbnail_url"),
            channel=data.get("channel"),
            artist=data.get("artist"),
            song_name=data.get("song_name"),
        )
    except (json.JSONDecodeError, TypeError):
        return SongMetadata(title="Unknown")


def _encode_settings(settings: SongSettings) -> str:
    """Encode SongSettings to JSON for storage."""
    return json.dumps({"pitch_semitones": settings.pitch_semitones})


def _decode_settings(settings_json: str) -> SongSettings:
    """Decode SongSettings from JSON."""
    if not settings_json:
        return SongSettings()
    try:
        data = json.loads(settings_json)
        return SongSettings(pitch_semitones=data.get("pitch_semitones") or 0)
    except (json.JSONDecodeError, TypeError):
        return SongSettings()
