"""
Pydantic request models for the kbox HTTP API.

Request bodies only -- responses are hand-built dicts shaped in
:mod:`kbox.web.serializers` and in the route handlers themselves.
"""

from typing import Optional

from pydantic import BaseModel


class AddSongRequest(BaseModel):
    user_id: str  # UUID for identity
    video_id: str  # Opaque video ID like "youtube:abc123"
    title: str
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    channel: Optional[str] = None
    pitch_semitones: int = 0


class ReplaceSongRequest(BaseModel):
    """Request model for replacing a queue item's video in place."""

    video_id: str  # Opaque video ID like "youtube:abc123"
    title: str
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    channel: Optional[str] = None


class ReorderRequest(BaseModel):
    new_position: int


class PitchRequest(BaseModel):
    semitones: int
    user_id: str  # UUID for identity


class UpdateQueueItemRequest(BaseModel):
    """Request model for updating queue item properties."""

    pitch_semitones: Optional[int] = None
    user_id: Optional[str] = None  # UUID for permission checking


class UserRequest(BaseModel):
    """Request model for user registration/update."""

    user_id: str  # UUID
    display_name: str


class OperatorAuthRequest(BaseModel):
    pin: str


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str


class SeekRequest(BaseModel):
    delta_seconds: int


class AddFavoriteRequest(BaseModel):
    """Request model for starring a song."""

    video_id: str  # Opaque video ID like "youtube:abc123"
    title: str
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    channel: Optional[str] = None
