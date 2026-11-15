"""
Favorites routes: star and unstar songs, and list a user's own favorites.
Favorites are private to the user who starred them.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ...models import SongMetadata
from ..dependencies import get_current_user_id, get_favorites_manager, require_user
from ..models import AddFavoriteRequest

router = APIRouter()


@router.post("/api/favorites")
def add_favorite(
    request_data: AddFavoriteRequest,
    favorites_mgr=Depends(get_favorites_manager),
    current_user_id: str = Depends(require_user),
):
    """Star a song for the current user."""
    metadata = SongMetadata(
        title=request_data.title,
        duration_seconds=request_data.duration_seconds,
        thumbnail_url=request_data.thumbnail_url,
        channel=request_data.channel,
    )
    favorites_mgr.add_favorite(current_user_id, request_data.video_id, metadata)
    return {"status": "favorited"}


@router.delete("/api/favorites/{video_id}")
def remove_favorite(
    video_id: str,
    favorites_mgr=Depends(get_favorites_manager),
    current_user_id: str = Depends(require_user),
):
    """Unstar a song for the current user."""
    favorites_mgr.remove_favorite(current_user_id, video_id)
    return {"status": "unfavorited"}


@router.get("/api/favorites/{user_id}")
def get_user_favorites(
    user_id: str,
    favorites_mgr=Depends(get_favorites_manager),
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """
    Get favorited songs for a specific user.

    Favorites are private - users can only view their own.
    User identity is determined from session — self-declared and unverified
    by design, so this gates which UUID the current session is acting as,
    not proof of who that UUID belongs to.
    """
    if not current_user_id or current_user_id != user_id:
        raise HTTPException(status_code=403, detail="You can only view your own favorites")

    favorites = favorites_mgr.get_user_favorites(user_id)
    favorite_dicts = []
    for fav in favorites:
        favorite_dicts.append(
            {
                "video_id": fav.video_id,
                "title": fav.metadata.title,
                "duration_seconds": fav.metadata.duration_seconds,
                "thumbnail_url": fav.metadata.thumbnail_url,
                "channel": fav.metadata.channel,
                "created_at": fav.created_at.isoformat() if fav.created_at else None,
            }
        )
    return {"favorites": favorite_dicts}
