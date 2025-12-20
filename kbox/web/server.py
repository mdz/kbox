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


class MySongPitchRequest(BaseModel):
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
    test_mode: bool = False,
) -> FastAPI:
    """
    Create and configure FastAPI application.

    Args:
        queue_manager: QueueManager instance
        youtube_client: YouTubeClient instance
        playback_controller: PlaybackController instance
        config_manager: ConfigManager instance

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
        """Get current queue, including currently playing song if any."""
        queue = queue_mgr.get_queue()

        # Include currently playing song even if it's been marked as played
        status = playback.get_status()
        if status.get("current_song"):
            current_song_id = status["current_song"]["id"]
            # Check if current song is already in the queue
            if not any(item["id"] == current_song_id for item in queue):
                # Get the current song from database and add it to queue
                current_song = queue_mgr.get_item(current_song_id)
                if current_song:
                    queue.append(current_song)
                    # Sort by position to maintain order
                    queue.sort(key=lambda x: x.get("position", 0))

        return {"queue": queue}

    @app.post("/api/queue")
    async def add_song(
        request_data: AddSongRequest,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        youtube: YouTubeClient = Depends(get_youtube_client),
    ):
        """Add song to queue."""
        try:
            # If pitch_semitones is 0, let add_song check for saved setting
            # Otherwise, use the explicitly provided value
            pitch_to_use = None if request_data.pitch_semitones == 0 else request_data.pitch_semitones
            
            item_id = queue_mgr.add_song(
                user_name=request_data.user_name,
                youtube_video_id=request_data.youtube_video_id,
                title=request_data.title,
                duration_seconds=request_data.duration_seconds,
                thumbnail_url=request_data.thumbnail_url,
                pitch_semitones=pitch_to_use,
            )

            # Trigger download (PlaybackController will handle this)
            # The download monitor will pick it up

            return {"id": item_id, "status": "added"}
        except Exception as e:
            logger.error("Error adding song: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/queue/{item_id}")
    async def remove_song(
        item_id: int,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
    ):
        """Remove song from queue (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
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
            raise HTTPException(status_code=400, detail="Failed to skip")

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
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Set pitch adjustment for current song (operator only)."""
        if not is_operator:
            raise HTTPException(
                status_code=403, detail="Operator authentication required"
            )

        if playback.set_pitch(request_data.semitones):
            return {"status": "updated", "pitch": request_data.semitones}
        else:
            raise HTTPException(
                status_code=400, detail="No current song or failed to update"
            )

    @app.post("/api/playback/pitch/my-song")
    async def set_my_song_pitch(
        request_data: MySongPitchRequest,
        playback: PlaybackController = Depends(get_playback_controller),
    ):
        """
        Set pitch adjustment for current song if it belongs to the requesting user.
        No operator authentication required - users can adjust their own song's pitch.
        """
        status = playback.get_status()
        current_song = status.get("current_song")
        
        if not current_song:
            raise HTTPException(
                status_code=400, detail="No song is currently playing"
            )
        
        # Verify the song belongs to the requesting user
        if current_song.get("user_name") != request_data.user_name:
            raise HTTPException(
                status_code=403, detail="You can only adjust pitch for your own song"
            )
        
        if playback.set_pitch(request_data.semitones):
            return {"status": "updated", "pitch": request_data.semitones}
        else:
            raise HTTPException(
                status_code=400, detail="Failed to update pitch"
            )

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
        """Get all configuration."""
        return config.get_all()

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
