"""
Party status: what each open page polls to stay current.

``GET /api/party/status`` returns what playback is doing, the queue, who is
up next, this viewer's own next turn and the alerts addressed to them, all
in one response so that a phone draws everything from one moment.

Handlers get their collaborators through the providers in
:mod:`kbox.web.dependencies`, which read ``request.app.state`` at request
time, so this router can be imported and inspected on its own.
"""

from typing import Optional

from fastapi import APIRouter, Depends

from ...playback import PlaybackController
from ...queue import QueueManager
from ..dependencies import (
    check_operator,
    get_current_user_id,
    get_playback_controller,
    get_queue_manager,
)
from ..serializers import serialize_playback_status, serialize_queue_item

router = APIRouter()


@router.get("/api/party/status")
def get_party_status(
    queue_mgr: QueueManager = Depends(get_queue_manager),
    playback: PlaybackController = Depends(get_playback_controller),
    current_user_id: Optional[str] = Depends(get_current_user_id),
    is_operator: bool = Depends(check_operator),
):
    """
    The party as this viewer sees it: what playback is doing (``playback``:
    state, current song and position), the queue with
    current/played flags, who is up next, how long until this viewer's own
    next turn, how deep the queue is, and the alerts addressed to this
    viewer (see kbox/alerts.py).

    Every open page (guest and operator phones, the display) polls this,
    which is how alerts reach phones today. Playback and the queue come
    back together, built moments apart in one handler, rather than from two
    requests that each ran on their own schedule and could straddle a song
    change.
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
        "playback": serialize_playback_status(status),
        "queue": queue,
        "current_song_id": current_song_id,
        "next_song": next_song,
        "my_next_turn": my_next_turn,
        "queue_depth_seconds": queue_depth_seconds,
        "queue_depth_count": queue_depth_count,
        "alerts": alerts,
    }
