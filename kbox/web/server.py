"""
FastAPI web server for kbox.

Provides REST API and web UI for queue management and playback control.
"""

import logging
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from ..config_manager import ConfigManager
from ..playback import PlaybackController
from ..queue import QueueManager
from ..streaming import StreamingController
from ..user import UserManager
from ..youtube import YouTubeClient

logger = logging.getLogger(__name__)


# Request models
class AddSongRequest(BaseModel):
    user_id: str  # UUID for identity
    youtube_video_id: str
    title: str
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    channel: Optional[str] = None
    pitch_semitones: int = 0


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


def get_user_manager(request: Request) -> UserManager:
    """Get UserManager from app state."""
    return request.app.state.user_manager


def get_history_manager(request: Request):
    """Get HistoryManager from app state."""
    return request.app.state.history_manager


def check_operator(request: Request) -> bool:
    """
    Check if user is authenticated as operator.
    """
    return request.session.get("operator", False)


def create_app(
    queue_manager: QueueManager,
    youtube_client: YouTubeClient,
    playback_controller: PlaybackController,
    config_manager: ConfigManager,
    user_manager: UserManager,
    history_manager,  # HistoryManager - avoid circular import
    streaming_controller: Optional[StreamingController] = None,
) -> FastAPI:
    """
    Create and configure FastAPI application.

    Args:
        queue_manager: QueueManager instance
        youtube_client: YouTubeClient instance
        playback_controller: PlaybackController instance
        config_manager: ConfigManager instance
        user_manager: UserManager instance
        streaming_controller: StreamingController instance (optional, for overlays)

    Returns:
        Configured FastAPI app
    """
    app = FastAPI(title="kbox", version="1.0.0")

    # Add session middleware for operator authentication
    app.add_middleware(SessionMiddleware, secret_key="kbox-secret-key-change-in-production")

    # Store components in app state
    app.state.queue_manager = queue_manager
    app.state.youtube_client = youtube_client
    app.state.playback_controller = playback_controller
    app.state.config_manager = config_manager
    app.state.user_manager = user_manager
    app.state.history_manager = history_manager
    app.state.streaming_controller = streaming_controller

    # Templates
    templates = Jinja2Templates(directory="kbox/web/templates")

    # Queue endpoints
    @app.get("/api/queue")
    async def get_queue(
        queue_mgr: QueueManager = Depends(get_queue_manager),
        playback: PlaybackController = Depends(get_playback_controller),
    ):
        """Get current queue with current/played flags for UI rendering."""
        from dataclasses import asdict

        # Get all queue items (including played ones for navigation)
        queue_items = queue_mgr.get_queue(include_played=True)

        # Get current song from playback status
        status = playback.get_status()
        current_song = status.get("current_song")
        current_song_id = current_song.get("id") if current_song else None

        # Convert QueueItem objects to dicts and add flags for UI rendering
        queue = []
        for item in queue_items:
            item_dict = asdict(item)
            # Flatten metadata and settings for easier frontend access
            item_dict["title"] = item.metadata.title
            item_dict["duration_seconds"] = item.metadata.duration_seconds
            item_dict["thumbnail_url"] = item.metadata.thumbnail_url
            item_dict["channel"] = item.metadata.channel
            item_dict["pitch_semitones"] = item.settings.pitch_semitones
            # Add UI flags
            item_dict["is_current"] = item.id == current_song_id
            item_dict["is_played"] = item.played_at is not None
            queue.append(item_dict)

        return {"queue": queue, "current_song_id": current_song_id}

    @app.get("/api/queue/settings/{youtube_video_id}")
    async def get_song_settings(
        youtube_video_id: str,
        user_id: str,
        history_mgr=Depends(get_history_manager),
    ):
        """Get saved settings (pitch, etc.) for a song from playback history for a specific user."""
        # Get settings from history (assumes YouTube source)
        settings = history_mgr.get_last_settings("youtube", youtube_video_id, user_id)
        if settings:
            return {"settings": {"pitch_semitones": settings.pitch_semitones}}
        return {"settings": None}

    @app.post("/api/queue")
    async def add_song(
        request_data: AddSongRequest,
        request: Request,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        youtube: YouTubeClient = Depends(get_youtube_client),
        user_mgr: UserManager = Depends(get_user_manager),
    ):
        """Add song to queue."""
        try:
            # Get user (they should already exist from registration)
            user = user_mgr.get_user(request_data.user_id)
            if not user:
                raise HTTPException(
                    status_code=400, detail="User not found. Please refresh the page."
                )

            # Add song with source-agnostic schema
            item_id = queue_mgr.add_song(
                user=user,
                source="youtube",
                source_id=request_data.youtube_video_id,
                title=request_data.title,
                duration_seconds=request_data.duration_seconds,
                thumbnail_url=request_data.thumbnail_url,
                channel=request_data.channel,
                pitch_semitones=request_data.pitch_semitones,
            )

            # Trigger download (PlaybackController will handle this)
            # The download monitor will pick it up
            # When download completes, PlaybackController auto-starts if idle

            # Show overlay notification
            streaming = request.app.state.streaming_controller
            if streaming:
                streaming.show_notification(
                    f"{user.display_name} added a song", duration_seconds=5.0
                )

            return {"id": item_id, "status": "added"}
        except HTTPException:
            raise  # Let HTTP exceptions pass through
        except Exception as e:
            logger.error("Error adding song: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/queue/{item_id}")
    async def remove_song(
        item_id: int,
        user_id: Optional[str] = None,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
    ):
        """
        Remove song from queue.

        Operators can remove any song. Users can remove their own songs
        by providing their user_id as a query parameter.
        """
        # Get the item to check ownership
        item = queue_mgr.get_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        # Check permissions: operator can remove any, users can only remove their own
        if not is_operator:
            if not user_id or user_id != item.user_id:
                raise HTTPException(status_code=403, detail="You can only remove songs you added")

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
            raise HTTPException(status_code=403, detail="Operator authentication required")

        if not queue_mgr.reorder_song(item_id, request_data.new_position):
            raise HTTPException(status_code=404, detail="Queue item not found or invalid position")
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
            if not request_data.user_id or request_data.user_id != item.user_id:
                raise HTTPException(status_code=403, detail="You can only edit songs you added")

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
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Move song to play next after currently playing song (operator only)."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        if not playback.move_to_next(item_id):
            raise HTTPException(status_code=404, detail="Queue item not found")

        return {"status": "moved_to_next"}

    @app.post("/api/queue/{item_id}/move-to-end")
    async def move_to_end(
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

    @app.post("/api/queue/{item_id}/bump-down")
    async def bump_down(
        item_id: int,
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Move song down 1 position (for no-shows). Operator only.

        If the song is currently playing, this also skips to the next song.
        """
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        result = playback.bump_down(item_id)

        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail="Queue item not found")
        elif result.get("status") == "error":
            raise HTTPException(status_code=500, detail="Failed to bump down song")

        return result

    @app.post("/api/queue/clear")
    async def clear_queue(
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
    ):
        """Clear entire queue (operator only)."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

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
    async def get_video_info(video_id: str, youtube: YouTubeClient = Depends(get_youtube_client)):
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
            raise HTTPException(status_code=403, detail="Operator authentication required")

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
            raise HTTPException(status_code=403, detail="Operator authentication required")

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
            raise HTTPException(status_code=403, detail="Operator authentication required")

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
            raise HTTPException(status_code=403, detail="Operator authentication required")

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
            raise HTTPException(status_code=403, detail="Operator authentication required")

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
        """Jump to and play a song immediately at its current queue position (operator only)."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        if playback.jump_to_song(item_id):
            return {"status": "playing_now", "item_id": item_id}
        else:
            raise HTTPException(status_code=400, detail="Failed to play song now")

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
            raise HTTPException(status_code=400, detail="No song is currently playing")

        # Check authorization: user's own song OR operator
        is_own_song = current_song.get("user_id") == request_data.user_id
        is_op = check_operator(request)

        if not is_own_song and not is_op:
            raise HTTPException(
                status_code=403, detail="You can only adjust pitch for your own song"
            )

        if playback.set_pitch(request_data.semitones):
            return {"status": "updated", "pitch": request_data.semitones}
        else:
            raise HTTPException(status_code=400, detail="Failed to update pitch")

    @app.post("/api/playback/restart")
    async def restart(
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Restart current song from the beginning (operator only)."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

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
            raise HTTPException(status_code=403, detail="Operator authentication required")

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
        """
        Get all configuration with rich schema metadata.

        Returns:
            - values: Current configuration values
            - schema: Metadata for each editable key (control type, options, description)
            - groups: Group definitions for organizing the config UI
        """
        return config.get_full_config()

    @app.patch("/api/config")
    async def update_config(
        request_data: ConfigUpdateRequest,
        config: ConfigManager = Depends(get_config_manager),
        is_operator: bool = Depends(check_operator),
    ):
        """Update configuration (operator only)."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        config.set(request_data.key, request_data.value)
        return {
            "status": "updated",
            "key": request_data.key,
            "value": request_data.value,
        }

    # User endpoints
    @app.post("/api/users")
    async def register_user(
        request_data: UserRequest,
        user_mgr: UserManager = Depends(get_user_manager),
    ):
        """Register or update a user."""
        user = user_mgr.get_or_create_user(
            user_id=request_data.user_id, display_name=request_data.display_name
        )
        return user

    # History endpoints
    @app.get("/api/history/{user_id}")
    async def get_user_history(
        user_id: str,
        history_mgr=Depends(get_history_manager),
    ):
        """Get playback history for a specific user."""
        history = history_mgr.get_user_history(user_id, limit=50)
        # Convert HistoryRecord objects to dicts for JSON serialization
        history_dicts = []
        for record in history:
            history_dicts.append(
                {
                    "id": record.id,
                    "source": record.source,
                    "source_id": record.source_id,
                    "performed_at": record.performed_at.isoformat()
                    if record.performed_at
                    else None,
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

    # Web UI
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Serve web UI."""
        return templates.TemplateResponse(request, "index.html")

    return app
