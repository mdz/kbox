"""
FastAPI web server for kbox.

Provides REST API and web UI for queue management and playback control.
"""

import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel

from ..queue import QueueManager, QueueManager as QM
from ..youtube import YouTubeClient
from ..playback import PlaybackController
from ..config_manager import ConfigManager

app = FastAPI(title="kbox", version="0.1.0")
templates = Jinja2Templates(directory="kbox/web/templates")

# Global state (will be set by dependency injection)
_queue_manager: Optional[QueueManager] = None
_youtube_client: Optional[YouTubeClient] = None
_playback_controller: Optional[PlaybackController] = None
_config_manager: Optional[ConfigManager] = None
_operator_pin: str = "1234"
_operator_authenticated: bool = False

logger = logging.getLogger(__name__)


# Request/Response Models
class AddSongRequest(BaseModel):
    user_name: str
    youtube_video_id: str
    title: str
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    pitch_semitones: int = 0


class ReorderRequest(BaseModel):
    new_position: int


class PitchRequest(BaseModel):
    semitones: int


class OperatorAuthRequest(BaseModel):
    pin: str


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str


def get_queue_manager() -> QueueManager:
    """Dependency to get queue manager."""
    if _queue_manager is None:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")
    return _queue_manager


def get_youtube_client() -> YouTubeClient:
    """Dependency to get YouTube client."""
    if _youtube_client is None:
        raise HTTPException(status_code=500, detail="YouTube client not initialized")
    return _youtube_client


def get_playback_controller() -> PlaybackController:
    """Dependency to get playback controller."""
    if _playback_controller is None:
        raise HTTPException(status_code=500, detail="Playback controller not initialized")
    return _playback_controller


def get_config_manager() -> ConfigManager:
    """Dependency to get config manager."""
    if _config_manager is None:
        raise HTTPException(status_code=500, detail="Config manager not initialized")
    return _config_manager


def require_operator():
    """Dependency to require operator authentication."""
    if not _operator_authenticated:
        raise HTTPException(status_code=403, detail="Operator authentication required")
    return True


# Queue Endpoints
@app.get("/api/queue")
async def get_queue(queue_manager: QueueManager = Depends(get_queue_manager)):
    """Get the current queue."""
    return {"queue": queue_manager.get_queue()}


@app.post("/api/queue")
async def add_song(
    request: AddSongRequest,
    queue_manager: QueueManager = Depends(get_queue_manager),
    youtube_client: YouTubeClient = Depends(get_youtube_client)
):
    """Add a song to the queue."""
    item_id = queue_manager.add_song(
        user_name=request.user_name,
        youtube_video_id=request.youtube_video_id,
        title=request.title,
        duration_seconds=request.duration_seconds,
        thumbnail_url=request.thumbnail_url,
        pitch_semitones=request.pitch_semitones
    )
    
    # Trigger download
    queue_item = queue_manager.get_item(item_id)
    if queue_item:
        youtube_client.download_video(
            request.youtube_video_id,
            item_id,
            status_callback=lambda status, path, error: queue_manager.update_download_status(
                item_id, status, path, error
            )
        )
        queue_manager.update_download_status(item_id, QM.STATUS_DOWNLOADING)
    
    return {"id": item_id, "message": "Song added to queue"}


@app.delete("/api/queue/{item_id}")
async def remove_song(
    item_id: int,
    queue_manager: QueueManager = Depends(get_queue_manager),
    _: bool = Depends(require_operator)
):
    """Remove a song from the queue (operator only)."""
    if queue_manager.remove_song(item_id):
        return {"message": "Song removed"}
    raise HTTPException(status_code=404, detail="Song not found")


@app.patch("/api/queue/{item_id}/position")
async def reorder_song(
    item_id: int,
    request: ReorderRequest,
    queue_manager: QueueManager = Depends(get_queue_manager),
    _: bool = Depends(require_operator)
):
    """Reorder a song in the queue (operator only)."""
    if queue_manager.reorder_song(item_id, request.new_position):
        return {"message": "Song reordered"}
    raise HTTPException(status_code=404, detail="Song not found or invalid position")


@app.post("/api/queue/clear")
async def clear_queue(
    queue_manager: QueueManager = Depends(get_queue_manager),
    _: bool = Depends(require_operator)
):
    """Clear the entire queue (operator only)."""
    count = queue_manager.clear_queue()
    return {"message": f"Queue cleared ({count} items removed)"}


# YouTube Endpoints
@app.get("/api/youtube/search")
async def search_youtube(
    q: str,
    max_results: int = 10,
    youtube_client: YouTubeClient = Depends(get_youtube_client)
):
    """Search YouTube for videos."""
    results = youtube_client.search(q, max_results)
    return {"results": results}


@app.get("/api/youtube/video/{video_id}")
async def get_video_info(
    video_id: str,
    youtube_client: YouTubeClient = Depends(get_youtube_client)
):
    """Get information about a specific YouTube video."""
    info = youtube_client.get_video_info(video_id)
    if info:
        return info
    raise HTTPException(status_code=404, detail="Video not found")


# Playback Endpoints
@app.get("/api/playback/status")
async def get_playback_status(
    playback_controller: PlaybackController = Depends(get_playback_controller)
):
    """Get current playback status."""
    return playback_controller.get_status()


@app.post("/api/playback/play")
async def play(
    playback_controller: PlaybackController = Depends(get_playback_controller),
    _: bool = Depends(require_operator)
):
    """Start or resume playback (operator only)."""
    if playback_controller.play():
        return {"message": "Playback started"}
    return {"message": "No ready songs in queue"}


@app.post("/api/playback/pause")
async def pause(
    playback_controller: PlaybackController = Depends(get_playback_controller),
    _: bool = Depends(require_operator)
):
    """Pause playback (operator only)."""
    if playback_controller.pause():
        return {"message": "Playback paused"}
    return {"message": "Not playing"}


@app.post("/api/playback/skip")
async def skip(
    playback_controller: PlaybackController = Depends(get_playback_controller),
    _: bool = Depends(require_operator)
):
    """Skip to next song (operator only)."""
    if playback_controller.skip():
        return {"message": "Skipped to next song"}
    return {"message": "No next song available"}


@app.post("/api/playback/previous")
async def previous(
    playback_controller: PlaybackController = Depends(get_playback_controller),
    _: bool = Depends(require_operator)
):
    """Go to previous song (operator only)."""
    if playback_controller.previous():
        return {"message": "Went to previous song"}
    return {"message": "Previous song not available"}


@app.post("/api/playback/pitch")
async def set_pitch(
    request: PitchRequest,
    playback_controller: PlaybackController = Depends(get_playback_controller),
    _: bool = Depends(require_operator)
):
    """Set pitch adjustment for current song (operator only)."""
    if playback_controller.set_pitch(request.semitones):
        return {"message": f"Pitch set to {request.semitones} semitones"}
    return {"message": "No current song"}


# Authentication Endpoints
@app.post("/api/auth/operator")
async def authenticate_operator(request: OperatorAuthRequest):
    """Authenticate as operator with PIN."""
    global _operator_authenticated
    if request.pin == _operator_pin:
        _operator_authenticated = True
        return {"message": "Operator authenticated", "authenticated": True}
    raise HTTPException(status_code=401, detail="Invalid PIN")


@app.post("/api/auth/logout")
async def logout_operator():
    """Exit operator mode."""
    global _operator_authenticated
    _operator_authenticated = False
    return {"message": "Logged out", "authenticated": False}


# Configuration Endpoints
@app.get("/api/config")
async def get_config(config_manager: ConfigManager = Depends(get_config_manager)):
    """Get all configuration."""
    return config_manager.get_all()


@app.patch("/api/config")
async def update_config(
    request: ConfigUpdateRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    _: bool = Depends(require_operator)
):
    """Update configuration (operator only)."""
    config_manager.set(request.key, request.value)
    return {"message": f"Config {request.key} updated"}


# Web UI
@app.get("/", response_class=HTMLResponse)
async def web_ui(request: Request):
    """Serve the web UI."""
    return templates.TemplateResponse("index.html", {"request": request})


def initialize(
    queue_manager: QueueManager,
    youtube_client: YouTubeClient,
    playback_controller: PlaybackController,
    config_manager: ConfigManager
):
    """Initialize the web server with dependencies."""
    global _queue_manager, _youtube_client, _playback_controller, _config_manager, _operator_pin
    
    _queue_manager = queue_manager
    _youtube_client = youtube_client
    _playback_controller = playback_controller
    _config_manager = config_manager
    _operator_pin = config_manager.get("operator_pin", "1234")
    
    logger.info("Web server initialized")

