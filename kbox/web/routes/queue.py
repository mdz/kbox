"""
Queue routes: viewing, adding, removing, editing and reordering songs.

Handlers get their collaborators through the providers in
:mod:`kbox.web.dependencies`, which read ``request.app.state`` at request
time. Nothing here needs the app object, so this router can be imported
and inspected on its own.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ...pitch_log import PitchLog
from ...playback import PlaybackController
from ...queue import MoveOutcome, QueueManager
from ...user import UserManager
from ...user_events import PHASE_QUEUED
from ..dependencies import (
    check_operator,
    get_current_user_id,
    get_history_manager,
    get_pitch_log,
    get_playback_controller,
    get_queue_manager,
    get_user_manager,
    require_user,
)
from ..models import AddSongRequest, ReorderRequest, ReplaceSongRequest, UpdateQueueItemRequest
from ..serializers import serialize_queue_item

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/queue")
def get_queue(
    queue_mgr: QueueManager = Depends(get_queue_manager),
    playback: PlaybackController = Depends(get_playback_controller),
    current_user_id: Optional[str] = Depends(get_current_user_id),
    is_operator: bool = Depends(check_operator),
):
    """
    Get current queue with current/played flags for UI rendering.

    Also carries the alerts addressed to this viewer (see kbox/alerts.py):
    every open page polls this, which is how alerts reach phones today.
    """
    queue_items = queue_mgr.get_queue()

    status = playback.get_status()
    current_song = status.current_song
    current_song_id = current_song.id if current_song else None
    cursor_id = playback.get_cursor()

    # Everything before the cursor is "played"
    cursor_idx = next((i for i, item in enumerate(queue_items) if item.id == cursor_id), None)
    upcoming = playback.get_upcoming()

    # Serialize queue items for the frontend
    queue = []
    for i, item in enumerate(queue_items):
        item_dict = serialize_queue_item(item)
        item_dict["is_current"] = item.id == current_song_id
        item_dict["is_played"] = cursor_idx is not None and i < cursor_idx
        queue.append(item_dict)

    # Next song
    next_song = None
    if upcoming:
        s = upcoming[0].item
        next_song = {
            "id": s.id,
            "user_id": s.user_id,
            "user_name": s.user_name,
            "title": s.metadata.title,
            "duration_seconds": s.metadata.duration_seconds,
        }

    # How long until the current user's next turn?
    my_next_turn = None
    is_next_me = next_song and current_user_id and next_song["user_id"] == current_user_id
    if current_user_id and not is_next_me:
        mine = next((u for u in upcoming if u.item.user_id == current_user_id), None)
        if mine:
            my_next_turn = {
                "estimated_seconds": mine.wait_seconds,
                "songs_away": mine.position - 1,
            }

    # Queue depth: count and total duration of upcoming items
    queue_depth_count = len(upcoming)
    queue_depth_seconds = sum(u.item.metadata.duration_seconds or 0 for u in upcoming)

    alerts = (
        queue_mgr.alerts.for_viewer(current_user_id, is_operator)
        if queue_mgr.alerts is not None
        else []
    )

    return {
        "queue": queue,
        "current_song_id": current_song_id,
        "next_song": next_song,
        "my_next_turn": my_next_turn,
        "queue_depth_seconds": queue_depth_seconds,
        "queue_depth_count": queue_depth_count,
        "alerts": alerts,
    }


@router.get("/api/queue/settings/{video_id:path}")
def get_song_settings(
    video_id: str,
    history_mgr=Depends(get_history_manager),
    current_user_id: str = Depends(require_user),
):
    """
    Get saved settings (pitch, etc.) for a song from playback history for current user.
    User identity is determined from session to prevent viewing others' settings.
    """
    settings = history_mgr.get_last_settings(video_id, current_user_id)
    if settings:
        return {"settings": {"pitch_semitones": settings.pitch_semitones}}
    return {"settings": None}


def queue_slot(playback: PlaybackController, item_id: int):
    """Position and estimated wait of an upcoming queue item, or None if
    it isn't upcoming (e.g. already played)."""
    for u in playback.get_upcoming():
        if u.item.id == item_id:
            return {"position": u.position, "estimated_seconds": u.wait_seconds}
    return None


@router.post("/api/queue")
def add_song(
    request_data: AddSongRequest,
    queue_mgr: QueueManager = Depends(get_queue_manager),
    user_mgr: UserManager = Depends(get_user_manager),
    playback: PlaybackController = Depends(get_playback_controller),
    current_user_id: str = Depends(require_user),
):
    """Add song to queue. Responds with the new item's queue slot."""
    try:
        # Get user from session, not the request body — the session is
        # the source of truth for which self-declared identity this
        # browser tab is currently acting as.
        user = user_mgr.get_user(current_user_id)
        if not user:
            raise HTTPException(status_code=400, detail="User not found. Please refresh the page.")

        # Add song to queue (metadata extraction handled by QueueManager)
        item_id = queue_mgr.add_song(
            user=user,
            video_id=request_data.video_id,
            title=request_data.title,
            duration_seconds=request_data.duration_seconds,
            thumbnail_url=request_data.thumbnail_url,
            channel=request_data.channel,
            pitch_semitones=request_data.pitch_semitones,
        )

        # Show overlay notification
        playback.show_notification(f"{user.display_name} added a song", duration_seconds=5.0)

        return {
            "id": item_id,
            "status": "added",
            "slot": queue_slot(playback, item_id),
        }
    except HTTPException:
        raise  # Let HTTP exceptions pass through
    except Exception as e:
        logger.error("Error adding song: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/queue/{item_id}")
def remove_song(
    item_id: int,
    queue_mgr: QueueManager = Depends(get_queue_manager),
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """
    Remove song from queue.

    Operators can remove any song. Users can remove their own songs. If it is
    the song playing now, it stops and playback moves on to the next singer.
    User identity is determined from session (not query params) — identity is
    self-declared and unverified, so this just fixes which UUID a write is
    attributed to, not who it "really" belongs to.
    """
    # Get the item to check ownership
    item = queue_mgr.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    # Check permissions: operator can remove any, users can only remove their own
    if not is_operator:
        if not current_user_id or current_user_id != item.user_id:
            raise HTTPException(status_code=403, detail="You can only remove songs you added")

    # Through playback, not the queue directly: it may be the song playing now.
    if not playback.remove_song(item_id):
        raise HTTPException(status_code=404, detail="Queue item not found")
    return {"status": "removed"}


@router.post("/api/queue/{item_id}/replace")
def replace_song(
    item_id: int,
    request_data: ReplaceSongRequest,
    queue_mgr: QueueManager = Depends(get_queue_manager),
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """
    Replace a queue item's song in place (e.g. wrong version was added).

    Keeps the item's queue position and attribution unchanged. Operators
    can replace any song; users can only replace their own.
    User identity is determined from session — self-declared and unverified
    by design, so this gates which UUID the current session is acting as,
    not proof of who that UUID belongs to.
    """
    item = queue_mgr.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    if not is_operator:
        if not current_user_id or current_user_id != item.user_id:
            raise HTTPException(status_code=403, detail="You can only replace songs you added")

    if not playback.replace_song(
        item_id,
        video_id=request_data.video_id,
        title=request_data.title,
        duration_seconds=request_data.duration_seconds,
        thumbnail_url=request_data.thumbnail_url,
        channel=request_data.channel,
    ):
        raise HTTPException(status_code=404, detail="Queue item not found")

    return {"status": "replaced", "slot": queue_slot(playback, item_id)}


@router.post("/api/queue/{item_id}/retry")
def retry_download(
    item_id: int,
    queue_mgr: QueueManager = Depends(get_queue_manager),
    is_operator: bool = Depends(check_operator),
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """
    Try a song's failed download again (e.g. after a passing YouTube hiccup).

    Operators can retry any song; users can only retry their own.
    """
    item = queue_mgr.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    if not is_operator:
        if not current_user_id or current_user_id != item.user_id:
            raise HTTPException(status_code=403, detail="You can only retry songs you added")

    if not queue_mgr.retry_download(item_id):
        raise HTTPException(status_code=409, detail="This song's download hasn't failed")
    return {"status": "retrying"}


@router.patch("/api/queue/{item_id}/position")
def reorder_song(
    item_id: int,
    request_data: ReorderRequest,
    queue_mgr: QueueManager = Depends(get_queue_manager),
    is_operator: bool = Depends(check_operator),
):
    """Reorder song in queue (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    if not queue_mgr.reorder_song(item_id, request_data.new_position):
        raise HTTPException(status_code=404, detail="Queue item not found or invalid position")
    return {"status": "reordered"}


@router.patch("/api/queue/{item_id}")
def update_queue_item(
    item_id: int,
    request_data: UpdateQueueItemRequest,
    queue_mgr: QueueManager = Depends(get_queue_manager),
    is_operator: bool = Depends(check_operator),
    current_user_id: Optional[str] = Depends(get_current_user_id),
    pitch_log: PitchLog = Depends(get_pitch_log),
):
    """
    Update properties of a queue item.
    Operators can update any song, users can only update their own.
    User identity is determined from session — self-declared and unverified
    by design, so this gates which UUID the current session is acting as,
    not proof of who that UUID belongs to.

    Currently supported fields:
    - pitch_semitones: Pitch adjustment in semitones
    """
    # Get the queue item to check ownership
    item = queue_mgr.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    # Check permissions: operator can edit any, users can only edit their own
    if not is_operator:
        if not current_user_id or current_user_id != item.user_id:
            raise HTTPException(status_code=403, detail="You can only edit songs you added")

    # Update pitch if provided
    if request_data.pitch_semitones is not None:
        if not queue_mgr.update_pitch(item_id, request_data.pitch_semitones):
            raise HTTPException(status_code=404, detail="Queue item not found")
        pitch_log.pitch_changed(
            item,
            request_data.pitch_semitones,
            phase=PHASE_QUEUED,
            changed_by=current_user_id,
        )

    # Return updated item
    updated_item = queue_mgr.get_item(item_id)
    return {"status": "updated", "item": updated_item}


@router.post("/api/queue/{item_id}/play-next")
def play_next(
    item_id: int,
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Move song to play next after currently playing song (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    if not playback.move_to_next(item_id):
        raise HTTPException(status_code=404, detail="Queue item not found")

    return {"status": "moved_to_next"}


@router.post("/api/queue/{item_id}/move-to-end")
def move_to_end(
    item_id: int,
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Move song to end of queue (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    if not playback.move_to_end(item_id):
        raise HTTPException(status_code=404, detail="Queue item not found")

    return {"status": "moved_to_end"}


@router.post("/api/queue/{item_id}/move-down")
def move_down(
    item_id: int,
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Move song down 1 position. Operator only."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    result = playback.move_down(item_id)

    if result.outcome is MoveOutcome.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Queue item not found")
    elif result.outcome is MoveOutcome.FAILED:
        raise HTTPException(status_code=500, detail="Failed to move down song")
    elif result.outcome is MoveOutcome.ALREADY_AT_BOUNDARY:
        return {"status": "already_at_end", "position": result.old_position}

    return {
        "status": "moved_down",
        "old_position": result.old_position,
        "new_position": result.new_position,
    }


@router.post("/api/queue/{item_id}/move-up")
def move_up(
    item_id: int,
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Move song up 1 position. Operator only."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    result = playback.move_up(item_id)

    if result.outcome is MoveOutcome.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Queue item not found")
    elif result.outcome is MoveOutcome.FAILED:
        raise HTTPException(status_code=500, detail="Failed to move up song")
    elif result.outcome is MoveOutcome.ALREADY_AT_BOUNDARY:
        return {"status": "already_at_start", "position": result.old_position}

    return {
        "status": "moved_up",
        "old_position": result.old_position,
        "new_position": result.new_position,
    }


@router.post("/api/queue/clear")
def clear_queue(
    queue_mgr: QueueManager = Depends(get_queue_manager),
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Clear entire queue (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    count = queue_mgr.clear_queue()
    playback.clear_cursor()
    return {"status": "cleared", "items_removed": count}
