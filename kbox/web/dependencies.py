"""
FastAPI dependency providers for the kbox HTTP API.

Every collaborator a route handler needs is reachable from
``request.app.state``, which :func:`kbox.web.app.create_app` populates.
Routers depend on these providers rather than on the app object, so a router
module can be imported and inspected without building an application.
"""

from typing import Optional

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from ..config_manager import ConfigManager
from ..database import EventRepository
from ..playback import PlaybackController
from ..queue import QueueManager
from ..search_suggest import SearchSuggester
from ..streaming import StreamingController
from ..suggestions import SuggestionEngine
from ..user import UserManager
from ..video_library import VideoLibrary


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


def get_favorites_manager(request: Request):
    """Get FavoritesManager from app state."""
    return request.app.state.favorites_manager


def get_search_suggester(request: Request) -> SearchSuggester:
    """Get SearchSuggester from app state."""
    return request.app.state.search_suggester


def get_templates(request: Request) -> Jinja2Templates:
    """Get the page templates from app state."""
    return request.app.state.templates


def get_event_repo(request: Request) -> EventRepository:
    """Get EventRepository, lazily created from the database."""
    if not hasattr(request.app.state, "_event_repo"):
        db = request.app.state.history_manager.database
        request.app.state._event_repo = EventRepository(db)
    return request.app.state._event_repo


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
    Guest identity here is self-declared and unverified, by design (see
    docs/development/guest-identity.md) — this just requires *some* identity
    to be bound to the session before allowing a write; it's not proof of
    who that identity belongs to.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Please refresh the page.",
        )
    return user_id
