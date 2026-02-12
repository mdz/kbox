"""
FastAPI web server for kbox.

Provides REST API and web UI for queue management and playback control.
"""

import logging
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from ..config_manager import ConfigManager
from ..playback import PlaybackController
from ..queue import DuplicateSongError, QueueManager
from ..streaming import StreamingController
from ..suggestions import SuggestionEngine, SuggestionError
from ..user import UserManager
from ..video_library import VideoLibrary

logger = logging.getLogger(__name__)


# Request models
class AddSongRequest(BaseModel):
    user_id: str  # UUID for identity
    video_id: str  # Opaque video ID like "youtube:abc123"
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


def get_video_library(request: Request) -> VideoLibrary:
    """Get VideoLibrary from app state."""
    return request.app.state.video_library


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


def get_suggestion_engine(request: Request) -> SuggestionEngine:
    """Get SuggestionEngine from app state."""
    return request.app.state.suggestion_engine


def check_operator(request: Request) -> bool:
    """
    Check if user is authenticated as operator.
    """
    return request.session.get("operator", False)


def get_current_user_id(request: Request) -> Optional[str]:
    """Get current user ID from session, or None if not authenticated."""
    return request.session.get("user_id")


def require_user(request: Request) -> str:
    """
    Require authenticated user, returning their user ID.

    Raises HTTPException 401 if user is not authenticated (no user_id in session).
    This ensures users cannot impersonate others by sending fake user_id values.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Please refresh the page.",
        )
    return user_id


def create_app(
    queue_manager: QueueManager,
    video_library: VideoLibrary,
    playback_controller: PlaybackController,
    config_manager: ConfigManager,
    user_manager: UserManager,
    history_manager,  # HistoryManager - avoid circular import
    suggestion_engine: Optional[SuggestionEngine] = None,
    streaming_controller: Optional[StreamingController] = None,
    access_token: Optional[str] = None,
    session_secret: Optional[str] = None,
) -> FastAPI:
    """
    Create and configure FastAPI application.

    Args:
        queue_manager: QueueManager instance
        video_library: VideoLibrary instance
        playback_controller: PlaybackController instance
        config_manager: ConfigManager instance
        user_manager: UserManager instance
        history_manager: HistoryManager instance
        suggestion_engine: SuggestionEngine instance (optional, for AI suggestions)
        streaming_controller: StreamingController instance (optional, for overlays)
        access_token: Access token for guest authentication (if None, auth disabled)
        session_secret: Secret key for session cookies (defaults to random if not provided)

    Returns:
        Configured FastAPI app
    """
    import secrets as secrets_module

    app = FastAPI(title="kbox", version="1.0.0")

    # Use provided session secret or generate a random one
    secret_key = session_secret or secrets_module.token_urlsafe(32)

    # Store components in app state (needed before middleware setup)
    app.state.queue_manager = queue_manager
    app.state.video_library = video_library
    app.state.playback_controller = playback_controller
    app.state.config_manager = config_manager
    app.state.user_manager = user_manager
    app.state.history_manager = history_manager
    app.state.suggestion_engine = suggestion_engine
    app.state.streaming_controller = streaming_controller
    app.state.access_token = access_token

    # Middleware is added in LIFO order (last added runs first)
    # So we add GuestAuthMiddleware BEFORE SessionMiddleware so it runs AFTER

    # Add guest authentication middleware (if access token is configured)
    if access_token:

        class GuestAuthMiddleware(BaseHTTPMiddleware):
            """Middleware to authenticate guests via access token."""

            async def dispatch(self, request: Request, call_next):
                # Allow display page without authentication (passive viewer)
                if request.url.path == "/display":
                    return await call_next(request)

                # Check if already authenticated via session
                if request.session.get("guest_authenticated"):
                    return await call_next(request)

                # Check for access token in query params
                key = request.query_params.get("key")
                if key == access_token:
                    # Valid token - set session and redirect to clean URL
                    request.session["guest_authenticated"] = True
                    # Redirect to same path without the key param
                    clean_url = str(request.url).split("?")[0]
                    return RedirectResponse(url=clean_url, status_code=302)

                # Not authenticated - return friendly error page
                return HTMLResponse(
                    content="""<!DOCTYPE html>
<html>
<head>
    <title>kbox - Access Required</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #fff;
        }
        .container {
            text-align: center;
            padding: 2rem;
        }
        h1 { font-size: 2.5rem; margin-bottom: 0.5rem; }
        p { font-size: 1.2rem; opacity: 0.8; }
        .qr-hint {
            margin-top: 2rem;
            padding: 1.5rem;
            background: rgba(255,255,255,0.1);
            border-radius: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎤 kbox</h1>
        <p>Karaoke queue system</p>
        <div class="qr-hint">
            <p>📱 Scan the QR code on the TV screen to join</p>
        </div>
    </div>
</body>
</html>""",
                    status_code=401,
                )

        app.add_middleware(GuestAuthMiddleware)

    # SessionMiddleware must be added AFTER GuestAuthMiddleware (runs first due to LIFO)
    app.add_middleware(SessionMiddleware, secret_key=secret_key)

    # Templates
    templates = Jinja2Templates(directory="kbox/web/templates")

    # Static files (CSS, JS)
    from fastapi.staticfiles import StaticFiles

    app.mount("/static", StaticFiles(directory="kbox/web/static"), name="static")

    # Queue endpoints
    @app.get("/api/queue")
    async def get_queue(
        queue_mgr: QueueManager = Depends(get_queue_manager),
        playback: PlaybackController = Depends(get_playback_controller),
        current_user_id: Optional[str] = Depends(get_current_user_id),
    ):
        """Get current queue with current/played flags for UI rendering."""
        from dataclasses import asdict

        # Get all queue items (including played ones for navigation)
        queue_items = queue_mgr.get_queue()

        # Get current song from playback status
        status = playback.get_status()
        current_song = status.get("current_song")
        current_song_id = current_song.get("id") if current_song else None
        position_seconds = status.get("position_seconds", 0)

        # Convert QueueItem objects to dicts and add flags for UI rendering
        queue = []
        for item in queue_items:
            item_dict = asdict(item)
            # Flatten metadata and settings for easier frontend access
            item_dict["title"] = item.metadata.title
            item_dict["duration_seconds"] = item.metadata.duration_seconds
            item_dict["thumbnail_url"] = item.metadata.thumbnail_url
            item_dict["channel"] = item.metadata.channel
            item_dict["artist"] = item.metadata.artist
            item_dict["song_name"] = item.metadata.song_name
            item_dict["pitch_semitones"] = item.settings.pitch_semitones
            # Add UI flags
            item_dict["is_current"] = item.id == current_song_id
            item_dict["is_played"] = item.played_at is not None
            queue.append(item_dict)

        # Get the next song that will play (unplayed, ready, after current)
        # This is the single source of truth - frontend should just display this
        next_song_item = queue_mgr.get_ready_song_at_offset(current_song_id, 1)
        next_song = None
        if next_song_item:
            next_song = {
                "id": next_song_item.id,
                "user_id": next_song_item.user_id,
                "user_name": next_song_item.user_name,
                "title": next_song_item.metadata.title,
                "duration_seconds": next_song_item.metadata.duration_seconds,
            }

        # Calculate when the current user's next song is coming up
        # (only if they're not already the immediate next singer)
        my_next_turn = None
        is_next_me = next_song and current_user_id and next_song["user_id"] == current_user_id
        if current_user_id and not is_next_me:
            # Get unplayed songs in queue order (excluding the currently playing song)
            upcoming = [
                item
                for item in queue_items
                if item.played_at is None and item.id != current_song_id
            ]

            # Time remaining in the current song
            current_duration = current_song.get("duration_seconds", 0) if current_song else 0
            time_until = max(0, current_duration - position_seconds) if current_song else 0
            songs_away = 0

            for item in upcoming:
                if item.user_id == current_user_id:
                    my_next_turn = {
                        "estimated_seconds": int(time_until),
                        "songs_away": songs_away,
                    }
                    break
                songs_away += 1
                time_until += item.metadata.duration_seconds or 0

        # Calculate queue depth (estimated wait time for next addition)
        pending_items = [item for item in queue if not item["is_played"]]
        queue_depth_seconds = sum(item.get("duration_seconds") or 0 for item in pending_items)
        queue_depth_count = len(pending_items)

        return {
            "queue": queue,
            "current_song_id": current_song_id,
            "next_song": next_song,
            "my_next_turn": my_next_turn,
            "queue_depth_seconds": queue_depth_seconds,
            "queue_depth_count": queue_depth_count,
        }

    @app.get("/api/queue/settings/{video_id:path}")
    async def get_song_settings(
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

    @app.post("/api/queue")
    async def add_song(
        request_data: AddSongRequest,
        request: Request,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        user_mgr: UserManager = Depends(get_user_manager),
        current_user_id: str = Depends(require_user),
    ):
        """Add song to queue."""
        try:
            # Get user from session (ignore request_data.user_id to prevent impersonation)
            user = user_mgr.get_user(current_user_id)
            if not user:
                raise HTTPException(
                    status_code=400, detail="User not found. Please refresh the page."
                )

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
            playback = request.app.state.playback_controller
            if playback:
                playback.show_notification(
                    f"{user.display_name} added a song", duration_seconds=5.0
                )

            return {"id": item_id, "status": "added"}
        except HTTPException:
            raise  # Let HTTP exceptions pass through
        except DuplicateSongError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            logger.error("Error adding song: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/queue/{item_id}")
    async def remove_song(
        item_id: int,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
        current_user_id: Optional[str] = Depends(get_current_user_id),
    ):
        """
        Remove song from queue.

        Operators can remove any song. Users can remove their own songs.
        User identity is determined from session (not query params) to prevent impersonation.
        """
        # Get the item to check ownership
        item = queue_mgr.get_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        # Check permissions: operator can remove any, users can only remove their own
        if not is_operator:
            if not current_user_id or current_user_id != item.user_id:
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
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
        current_user_id: Optional[str] = Depends(get_current_user_id),
    ):
        """
        Update properties of a queue item.
        Operators can update any song, users can only update their own.
        User identity is determined from session to prevent impersonation.

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

    @app.post("/api/queue/{item_id}/move-down")
    async def move_down(
        item_id: int,
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Move song down 1 position. Operator only."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        result = playback.move_down(item_id)

        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail="Queue item not found")
        elif result.get("status") == "error":
            raise HTTPException(status_code=500, detail="Failed to move down song")

        return result

    @app.post("/api/queue/{item_id}/move-up")
    async def move_up(
        item_id: int,
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Move song up 1 position. Operator only."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        result = playback.move_up(item_id)

        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail="Queue item not found")
        elif result.get("status") == "error":
            raise HTTPException(status_code=500, detail="Failed to move up song")

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

    # Video search endpoints
    @app.get("/api/search")
    async def search_videos(
        q: str,
        max_results: int = 10,
        video_lib: VideoLibrary = Depends(get_video_library),
    ):
        """Search for videos across all configured sources."""
        results = video_lib.search(q, max_results)
        return {"results": results}

    @app.get("/api/suggestions")
    async def get_suggestions(
        max_results: int = 8,
        suggestion_engine: SuggestionEngine = Depends(get_suggestion_engine),
        current_user_id: str = Depends(require_user),
    ):
        """
        Get AI-powered song suggestions for current user.

        Suggestions are based on user's history, current queue, and operator theme.
        User identity is determined from session to prevent impersonation.
        """
        if not suggestion_engine:
            raise HTTPException(
                status_code=503,
                detail="Suggestions not available. Configure AI settings first.",
            )

        try:
            results = suggestion_engine.get_suggestions(current_user_id, max_results)
            return {"results": results, "source": "ai"}
        except SuggestionError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            logger.error("Unexpected error generating suggestions: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500, detail="An error occurred while generating suggestions."
            )

    @app.get("/api/video/{video_id:path}")
    async def get_video_info(
        video_id: str,
        video_lib: VideoLibrary = Depends(get_video_library),
    ):
        """Get video information by opaque video ID."""
        info = video_lib.get_info(video_id)
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
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
        current_user_id: Optional[str] = Depends(get_current_user_id),
    ):
        """
        Set pitch adjustment for current song.
        Allowed if: user owns the song OR user is an operator.
        User identity is determined from session to prevent impersonation.
        """
        status = playback.get_status()
        current_song = status.get("current_song")

        if not current_song:
            raise HTTPException(status_code=400, detail="No song is currently playing")

        # Check authorization: user's own song OR operator
        is_own_song = current_song.get("user_id") == current_user_id

        if not is_own_song and not is_operator:
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
        config_data = config.get_full_config()

        # Ensure current audio device is in the options list
        # (DeviceMonitor may not return busy devices, but we still want to show the current one)
        current_device = config_data["values"].get("audio_output_device")
        if current_device:
            audio_schema = config_data["schema"].get("audio_output_device")
            if audio_schema and "options" in audio_schema:
                device_values = [opt["value"] for opt in audio_schema["options"]]
                if current_device not in device_values:
                    audio_schema["options"].append(
                        {
                            "value": current_device,
                            "label": f"{current_device} (current)",
                        }
                    )

        return config_data

    @app.patch("/api/config")
    async def update_config(
        request: Request,
        request_data: ConfigUpdateRequest,
        config: ConfigManager = Depends(get_config_manager),
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Update configuration (operator only)."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        # Save the configuration
        config.set(request_data.key, request_data.value)

        # Check if this config change requires streaming subsystem restart
        STREAMING_CONFIG_KEYS = {
            "audio_output_device",
            "audio_output_channels",
            "overlay_qr_position",
            "overlay_qr_size_percent",
        }

        needs_restart = request_data.key in STREAMING_CONFIG_KEYS

        if needs_restart and request.app.state.streaming_controller:
            try:
                logger.info("Applying streaming config change: %s", request_data.key)

                # Stop playback
                playback.stop_playback()

                # Reinitialize the pipeline with new config
                streaming = request.app.state.streaming_controller
                streaming.reinitialize_pipeline()

                # Show idle screen so display stays active
                playback.show_idle_screen()

                logger.info("Streaming configuration applied successfully")

                return {
                    "status": "updated",
                    "key": request_data.key,
                    "value": request_data.value,
                    "restarted": True,
                    "message": "Configuration updated and streaming subsystem restarted",
                }
            except Exception as e:
                logger.error("Error restarting streaming subsystem: %s", e, exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"Config saved but failed to restart streaming: {str(e)}",
                )

        return {
            "status": "updated",
            "key": request_data.key,
            "value": request_data.value,
        }

    # User endpoints
    @app.post("/api/users")
    async def register_user(
        request: Request,
        request_data: UserRequest,
        user_mgr: UserManager = Depends(get_user_manager),
    ):
        """
        Register or update a user.

        On first call, binds the provided user_id to the session.
        On subsequent calls, uses the session's user_id (ignores provided one).
        This prevents user impersonation.
        """
        # Check if session already has a user_id
        session_user_id = request.session.get("user_id")

        if session_user_id:
            # Session already bound - use the session's user_id, ignore request
            user_id = session_user_id
        else:
            # First registration - bind this user_id to session
            user_id = request_data.user_id
            request.session["user_id"] = user_id

        user = user_mgr.get_or_create_user(user_id=user_id, display_name=request_data.display_name)
        return user

    # History endpoints
    @app.get("/api/history/{user_id}")
    async def get_user_history(
        user_id: str,
        history_mgr=Depends(get_history_manager),
        is_operator: bool = Depends(check_operator),
        current_user_id: Optional[str] = Depends(get_current_user_id),
    ):
        """
        Get playback history for a specific user.

        Users can only view their own history. Operators can view anyone's history.
        User identity is determined from session to prevent impersonation.
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
    @app.get("/display", response_class=HTMLResponse)
    async def display(request: Request):
        """Fullscreen YouTube embed display for TV/monitor."""
        # Authenticate the session so API calls from this page work
        request.session["guest_authenticated"] = True
        return templates.TemplateResponse(request, "display.html")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, config: ConfigManager = Depends(get_config_manager)):
        """Serve web UI."""
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "long_song_warning_minutes": config.get_int("long_song_warning_minutes", 5),
            },
        )

    return app
