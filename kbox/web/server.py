"""
FastAPI web server for kbox.

Provides REST API and web UI for queue management and playback control.
"""

import logging
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import os
from pydantic import BaseModel

from ..queue import QueueManager
from ..youtube import YouTubeClient
from ..playback import PlaybackController
from ..config_manager import ConfigManager
from ..streaming import StreamingController

logger = logging.getLogger(__name__)


# Request models
class AddSongRequest(BaseModel):
    user_name: str
    youtube_video_id: str
    title: str
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    pitch_semitones: int = 0  # If 0, will check for saved setting; otherwise uses provided value


class ReorderRequest(BaseModel):
    new_position: int


class PitchRequest(BaseModel):
    semitones: int
    user_name: str


class UpdateQueueItemRequest(BaseModel):
    """Request model for updating queue item properties."""

    pitch_semitones: Optional[int] = None
    user_name: Optional[str] = None  # For permission checking


class OperatorAuthRequest(BaseModel):
    pin: str


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str


class SeekRequest(BaseModel):
    delta_seconds: int


# Dependency to get components
def get_queue_manager(request: Request) -> QueueManager:
    """Get QueueManager from app state."""
    return request.app.state.queue_manager


def get_youtube_client(request: Request) -> YouTubeClient:
    """Get YouTubeClient from app state."""
    return request.app.state.youtube_client


def get_playback_controller(request: Request) -> PlaybackController:
    """Get PlaybackController from app state."""
    return request.app.state.playback_controller


def get_config_manager(request: Request) -> ConfigManager:
    """Get ConfigManager from app state."""
    return request.app.state.config_manager


def get_streaming_controller(request: Request) -> StreamingController:
    """Get StreamingController from app state."""
    return request.app.state.streaming_controller


def check_operator(request: Request) -> bool:
    """
    Check if user is authenticated as operator.
    In test mode, always returns True.
    """
    test_mode = getattr(request.app.state, "test_mode", False)
    if test_mode:
        return True
    return request.session.get("operator", False)


def create_app(
    queue_manager: QueueManager,
    youtube_client: YouTubeClient,
    playback_controller: PlaybackController,
    config_manager: ConfigManager,
    streaming_controller: Optional[StreamingController] = None,
    test_mode: bool = False,
) -> FastAPI:
    """
    Create and configure FastAPI application.

    Args:
        queue_manager: QueueManager instance
        youtube_client: YouTubeClient instance
        playback_controller: PlaybackController instance
        config_manager: ConfigManager instance
        streaming_controller: StreamingController instance (optional, for overlays)

    Returns:
        Configured FastAPI app
    """
    app = FastAPI(title="kbox", version="1.0.0")

    # Add session middleware for operator authentication
    app.add_middleware(
        SessionMiddleware, secret_key="kbox-secret-key-change-in-production"
    )

    # Store components and test mode in app state
    app.state.queue_manager = queue_manager
    app.state.youtube_client = youtube_client
    app.state.playback_controller = playback_controller
    app.state.config_manager = config_manager
    app.state.streaming_controller = streaming_controller
    app.state.test_mode = test_mode
    logger.info("Test mode enabled: %s", test_mode)

    # Templates
    templates = Jinja2Templates(directory="kbox/web/templates")

    # Queue endpoints
    @app.get("/api/queue")
    async def get_queue(
        queue_mgr: QueueManager = Depends(get_queue_manager),
        playback: PlaybackController = Depends(get_playback_controller),
    ):
        """Get current queue with current/played flags for UI rendering."""
        # Get all queue items (including played ones for navigation)
        queue = queue_mgr.get_queue(include_played=True)

        # Get current song from playback status
        status = playback.get_status()
        current_song = status.get("current_song")
        current_song_id = current_song["id"] if current_song else None

        # Add flags for UI rendering
        for item in queue:
            item["is_current"] = (item["id"] == current_song_id)
            item["is_played"] = (item.get("played_at") is not None)

        return {
            "queue": queue,
            "current_song_id": current_song_id
        }

    @app.get("/api/queue/settings/{youtube_video_id}")
    async def get_song_settings(
        youtube_video_id: str,
        user_name: str,
        queue_mgr: QueueManager = Depends(get_queue_manager),
    ):
        """Get saved settings (pitch, etc.) for a song from playback history for a specific user."""
        settings = queue_mgr.get_last_song_settings(youtube_video_id, user_name)
        return {"settings": settings}

    @app.post("/api/queue")
    async def add_song(
        request_data: AddSongRequest,
        request: Request,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        youtube: YouTubeClient = Depends(get_youtube_client),
    ):
        """Add song to queue."""
        try:
            # Use the pitch value provided by the frontend (which will have checked history if needed)
            item_id = queue_mgr.add_song(
                user_name=request_data.user_name,
                youtube_video_id=request_data.youtube_video_id,
                title=request_data.title,
                duration_seconds=request_data.duration_seconds,
                thumbnail_url=request_data.thumbnail_url,
                pitch_semitones=request_data.pitch_semitones,
            )

            # Trigger download (PlaybackController will handle this)
            # The download monitor will pick it up

            # Show overlay notification
            streaming = request.app.state.streaming_controller
            if streaming:
                streaming.show_notification(
                    f"{request_data.user_name} added a song",
                    duration_seconds=5.0
                )

            return {"id": item_id, "status": "added"}
        except Exception as e:
            logger.error("Error adding song: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/queue/{item_id}")
    async def remove_song(
        item_id: int,
        user_name: Optional[str] = None,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
    ):
        """
        Remove song from queue.
        
        Operators can remove any song. Users can remove their own songs
        by providing their user_name as a query parameter.
        """
        # Get the item to check ownership
        item = queue_mgr.get_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        # Check permissions: operator can remove any, users can only remove their own
        if not is_operator:
            if not user_name or user_name != item["user_name"]:
                raise HTTPException(
                    status_code=403, detail="You can only remove songs you added"
                )

        if not queue_mgr.remove_song(item_id):
            raise HTTPException(status_code=404, detail="Queue item not found")
        return {"status": "removed"}

    @app.patch("/api/queue/{item_id}/position")
    async def reorder_song(
        item_id: int,
        request_data: ReorderRequest,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
    ):
        """Reorder song in queue (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        if not queue_mgr.reorder_song(item_id, request_data.new_position):
            raise HTTPException(
                status_code=404, detail="Queue item not found or invalid position"
            )
        return {"status": "reordered"}

    @app.patch("/api/queue/{item_id}")
    async def update_queue_item(
        item_id: int,
        request_data: UpdateQueueItemRequest,
        request: Request,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
    ):
        """
        Update properties of a queue item.
        Operators can update any song, users can only update their own.

        Currently supported fields:
        - pitch_semitones: Pitch adjustment in semitones
        """
        # Get the queue item to check ownership
        item = queue_mgr.get_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        # Check permissions: operator can edit any, users can only edit their own
        if not is_operator:
            if (
                not request_data.user_name
                or request_data.user_name != item["user_name"]
            ):
                raise HTTPException(
                    status_code=403, detail="You can only edit songs you added"
                )

        # Update pitch if provided
        if request_data.pitch_semitones is not None:
            if not queue_mgr.update_pitch(item_id, request_data.pitch_semitones):
                raise HTTPException(status_code=404, detail="Queue item not found")

        # Return updated item
        updated_item = queue_mgr.get_item(item_id)
        return {"status": "updated", "item": updated_item}

    @app.post("/api/queue/{item_id}/play-next")
    async def play_next(
        item_id: int,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
    ):
        """Move song to position 1 (play next) (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        if not queue_mgr.reorder_song(item_id, 1):
            raise HTTPException(status_code=404, detail="Queue item not found")
        return {"status": "moved_to_next"}

    @app.post("/api/queue/{item_id}/move-to-end")
    async def move_to_end(
        item_id: int,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
    ):
        """Move song to end of queue (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        # Get max position
        queue = queue_mgr.get_queue()
        max_position = max((item.get("position", 0) for item in queue), default=0)

        if not queue_mgr.reorder_song(item_id, max_position):
            raise HTTPException(status_code=404, detail="Queue item not found")
        return {"status": "moved_to_end"}

    @app.post("/api/queue/{item_id}/bump-down")
    async def bump_down(
        item_id: int,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
        playback: PlaybackController = Depends(get_playback_controller),
    ):
        """Move song down 1 position (for no-shows). Operator only.
        
        If the song is currently playing, this also skips to the next song.
        """
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        # Get current item and queue
        item = queue_mgr.get_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        current_position = item.get("position", 0)
        queue = queue_mgr.get_queue()
        max_position = max((q.get("position", 0) for q in queue), default=0)

        # Move down 1 position, but not past the end
        new_position = min(current_position + 1, max_position)

        if new_position == current_position:
            # Already at the end
            return {"status": "already_at_end", "position": current_position}

        # Check if this is the currently playing song
        is_currently_playing = (
            playback and 
            playback.current_song and 
            playback.current_song.get('id') == item_id
        )

        if not queue_mgr.reorder_song(item_id, new_position):
            raise HTTPException(status_code=404, detail="Failed to move song")
        
        # If this was the currently playing song, go to previous (which is now the song that moved up)
        if is_currently_playing:
            playback.previous()
            return {"status": "bumped_down_and_skipped", "old_position": current_position, "new_position": new_position}
        
        return {"status": "bumped_down", "old_position": current_position, "new_position": new_position}

    @app.post("/api/queue/clear")
    async def clear_queue(
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
    ):
        """Clear entire queue (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        count = queue_mgr.clear_queue()
        return {"status": "cleared", "items_removed": count}

    # YouTube endpoints
    @app.get("/api/youtube/search")
    async def search_youtube(
        q: str,
        max_results: int = 10,
        youtube: YouTubeClient = Depends(get_youtube_client),
    ):
        """Search YouTube for karaoke videos."""
        results = youtube.search(q, max_results)
        return {"results": results}

    @app.get("/api/youtube/video/{video_id}")
    async def get_video_info(
        video_id: str, youtube: YouTubeClient = Depends(get_youtube_client)
    ):
        """Get video information."""
        info = youtube.get_video_info(video_id)
        if not info:
            raise HTTPException(status_code=404, detail="Video not found")
        return info

    # Playback endpoints
    @app.get("/api/playback/status")
    async def get_playback_status(
        playback: PlaybackController = Depends(get_playback_controller),
    ):
        """Get current playback status."""
        return playback.get_status()

    @app.post("/api/playback/play")
    async def play(
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Start/resume playback (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        if playback.play():
            return {"status": "playing"}
        else:
            raise HTTPException(status_code=400, detail="Failed to start playback")

    @app.post("/api/playback/pause")
    async def pause(
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Pause playback (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        if playback.pause():
            return {"status": "paused"}
        else:
            raise HTTPException(status_code=400, detail="Failed to pause")

    @app.post("/api/playback/stop")
    async def stop(
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Stop playback and return to idle (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        if playback.stop_playback():
            return {"status": "stopped"}
        else:
            raise HTTPException(status_code=400, detail="Failed to stop")

    @app.post("/api/playback/skip")
    async def skip(
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Skip to next song (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        if playback.skip():
            return {"status": "skipped"}
        else:
            # Return 200 with warning instead of error - no next song available
            return {"status": "no_next_song", "message": "No next song in queue to skip to"}

    @app.post("/api/playback/previous")
    async def previous(
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Go to previous song (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        if playback.previous():
            return {"status": "previous"}
        else:
            # Return 200 with warning instead of error - no previous song available
            return {"status": "no_previous_song", "message": "Previous song not available"}

    @app.post("/api/playback/jump/{item_id}")
    async def jump_to_song(
        item_id: int,
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Jump to a specific song in the queue (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        if playback.jump_to_song(item_id):
            return {"status": "jumped", "item_id": item_id}
        else:
            raise HTTPException(status_code=400, detail="Failed to jump to song")

    @app.post("/api/playback/pitch")
    async def set_pitch(
        request_data: PitchRequest,
        request: Request,
        playback: PlaybackController = Depends(get_playback_controller),
    ):
        """
        Set pitch adjustment for current song.
        Allowed if: user owns the song OR user is an operator.
        """
        status = playback.get_status()
        current_song = status.get("current_song")
        
        if not current_song:
            raise HTTPException(
                status_code=400, detail="No song is currently playing"
            )
        
        # Check authorization: user's own song OR operator
        is_own_song = current_song.get("user_name") == request_data.user_name
        is_op = check_operator(request)
        
        if not is_own_song and not is_op:
            raise HTTPException(
                status_code=403, detail="You can only adjust pitch for your own song"
            )
        
        if playback.set_pitch(request_data.semitones):
            return {"status": "updated", "pitch": request_data.semitones}
        else:
            raise HTTPException(
                status_code=400, detail="Failed to update pitch"
            )

    @app.post("/api/playback/restart")
    async def restart(
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Restart current song from the beginning (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        if playback.restart():
            return {"status": "restarted"}
        else:
            raise HTTPException(status_code=400, detail="Failed to restart song")

    @app.post("/api/playback/seek")
    async def seek(
        request_data: SeekRequest,
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Seek forward or backward in current song (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        if playback.seek_relative(request_data.delta_seconds):
            return {"status": "seeked", "delta_seconds": request_data.delta_seconds}
        else:
            raise HTTPException(status_code=400, detail="Failed to seek")

    # Authentication endpoints
    @app.get("/api/auth/operator")
    async def check_operator_status(request: Request):
        """Check if user is currently authenticated as operator."""
        is_operator = check_operator(request)
        return {"operator": is_operator}

    @app.post("/api/auth/operator")
    async def authenticate_operator(
        request: Request,
        auth_data: OperatorAuthRequest,
        config: ConfigManager = Depends(get_config_manager),
    ):
        """Authenticate as operator with PIN."""
        correct_pin = config.get("operator_pin", "1234")
        if auth_data.pin == correct_pin:
            request.session["operator"] = True
            return {"status": "authenticated", "operator": True}
        else:
            raise HTTPException(status_code=401, detail="Invalid PIN")

    @app.post("/api/auth/logout")
    async def logout_operator(request: Request):
        """Exit operator mode."""
        request.session["operator"] = False
        return {"status": "logged_out", "operator": False}

    # Configuration endpoints
    @app.get("/api/config")
    async def get_config(config: ConfigManager = Depends(get_config_manager)):
        """Get all configuration with editable keys metadata."""
        return {
            "values": config.get_all(),
            "editable_keys": config.get_editable_keys()
        }

    @app.patch("/api/config")
    async def update_config(
        request_data: ConfigUpdateRequest,
        config: ConfigManager = Depends(get_config_manager),
        is_operator: bool = Depends(check_operator),
    ):
        """Update configuration (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        config.set(request_data.key, request_data.value)
        return {
            "status": "updated",
            "key": request_data.key,
            "value": request_data.value,
        }

    # Web UI
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Serve web UI."""
        test_mode = getattr(request.app.state, "test_mode", False)
        logger.debug("Rendering index page with test_mode=%s", test_mode)
        return templates.TemplateResponse(
            "index.html", {"request": request, "test_mode": test_mode}
        )

    return app
