"""
Playback history routes. Users can view their own history; operators can
view anyone's.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import check_operator, get_current_user_id, get_history_manager

router = APIRouter()


@router.get("/api/history/{user_id}")
def get_user_history(
    user_id: str,
    history_mgr=Depends(get_history_manager),
    is_operator: bool = Depends(check_operator),
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """
    Get playback history for a specific user.

    Users can only view their own history. Operators can view anyone's history.
    User identity is determined from session — self-declared and unverified
    by design, so this gates which UUID the current session is acting as,
    not proof of who that UUID belongs to.
    """
    # Check permissions: operators can view any, users can only view their own
    if not is_operator:
        if not current_user_id or current_user_id != user_id:
            raise HTTPException(status_code=403, detail="You can only view your own history")

    history = history_mgr.get_user_history(user_id, limit=50)
    # Convert HistoryRecord objects to dicts for JSON serialization
    history_dicts = []
    for record in history:
        history_dicts.append(
            {
                "id": record.id,
                "video_id": record.video_id,
                "performed_at": record.performed_at.isoformat() if record.performed_at else None,
                "title": record.metadata.title,
                "duration_seconds": record.metadata.duration_seconds,
                "thumbnail_url": record.metadata.thumbnail_url,
                "pitch_semitones": record.settings.pitch_semitones,
                "played_duration_seconds": record.performance.get("played_duration_seconds"),
                "playback_end_position_seconds": record.performance.get(
                    "playback_end_position_seconds"
                ),
                "completion_percentage": record.performance.get("completion_percentage"),
            }
        )
    return {"history": history_dicts}
