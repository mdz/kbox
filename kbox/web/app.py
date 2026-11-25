"""
FastAPI application assembly for kbox.

:func:`create_app` wires the pieces together: collaborators onto
``app.state`` (read back by :mod:`kbox.web.dependencies`), the middleware
from :mod:`kbox.web.middleware` in the order guest auth depends on, the
versioned static mount, and the routers under :mod:`kbox.web.routes`. The
route handlers themselves live in those routers.
"""

import logging
import secrets
from typing import Optional

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from ..config_manager import ConfigManager
from ..pitch_log import PitchLog
from ..playback import PlaybackController
from ..queue import QueueManager
from ..search_suggest import SearchSuggester
from ..streaming import StreamingController
from ..suggestions import SuggestionEngine
from ..user import UserManager
from ..video_library import VideoLibrary
from .middleware import (
    ClockTolerantSessionMiddleware,
    GuestAuthMiddleware,
    ImmutableStaticFiles,
    RequestLogMiddleware,
)
from .routes import auth as auth_routes
from .routes import config as config_routes
from .routes import favorites as favorites_routes
from .routes import history as history_routes
from .routes import pages as pages_routes
from .routes import party as party_routes
from .routes import playback as playback_routes
from .routes import queue as queue_routes
from .routes import search as search_routes
from .routes import users as users_routes

# Root logger stays at INFO (DEBUG globally is too spammy from third-party
# libs like httpx/litellm); opt the web layer's loggers in on their own. Set
# on the package logger so every module under kbox.web -- the routers and
# middleware, not just this one -- inherits it.
logging.getLogger("kbox.web").setLevel(logging.DEBUG)


def create_app(
    queue_manager: QueueManager,
    video_library: VideoLibrary,
    playback_controller: PlaybackController,
    config_manager: ConfigManager,
    user_manager: UserManager,
    history_manager,  # HistoryManager - avoid circular import
    favorites_manager,  # FavoritesManager - avoid circular import
    suggestion_engine: Optional[SuggestionEngine] = None,
    streaming_controller: Optional[StreamingController] = None,
    pitch_log: Optional[PitchLog] = None,
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
        favorites_manager: FavoritesManager instance
        suggestion_engine: SuggestionEngine instance (optional, for AI suggestions)
        streaming_controller: StreamingController instance (optional, for overlays)
        pitch_log: PitchLog instance (optional; built from the history
            database if not given)
        access_token: Access token for guest authentication (if None, auth disabled)
        session_secret: Secret key for session cookies (defaults to random if not provided)

    Returns:
        Configured FastAPI app
    """
    app = FastAPI(title="kbox", version="1.0.0")

    # Use provided session secret or generate a random one
    secret_key = session_secret or secrets.token_urlsafe(32)

    # Store components in app state (needed before middleware setup)
    app.state.queue_manager = queue_manager
    app.state.video_library = video_library
    app.state.playback_controller = playback_controller
    app.state.config_manager = config_manager
    app.state.user_manager = user_manager
    app.state.history_manager = history_manager
    app.state.favorites_manager = favorites_manager
    app.state.suggestion_engine = suggestion_engine
    app.state.streaming_controller = streaming_controller
    app.state.pitch_log = pitch_log or PitchLog(history_manager.database)
    app.state.access_token = access_token
    # Autocomplete is self-contained (config + an HTTP call), so it's built
    # here rather than threaded through main.py's wiring.
    app.state.search_suggester = SearchSuggester(config_manager)

    # Templates. Built before the middleware because GuestAuthMiddleware
    # renders its access-required page from them.
    templates = Jinja2Templates(directory="kbox/web/templates")
    app.state.templates = templates

    # Middleware is added in LIFO order (last added runs first)
    # So we add GuestAuthMiddleware BEFORE SessionMiddleware so it runs AFTER
    app.add_middleware(RequestLogMiddleware, user_manager=user_manager)

    # Add guest authentication middleware (if access token is configured)
    if access_token:
        app.add_middleware(
            GuestAuthMiddleware,
            config_manager=config_manager,
            access_token=access_token,
            templates=templates,
        )

    # SessionMiddleware must be added AFTER GuestAuthMiddleware (runs first due to LIFO)
    app.add_middleware(ClockTolerantSessionMiddleware, secret_key=secret_key)

    # Static files (CSS, JS), mounted under a per-process version so browsers
    # can cache them forever instead of guessing a freshness lifetime. With
    # no versioning, mobile Chrome in particular could hold onto a stale
    # app.js/queue.js across deploys — even surviving a plain page reload —
    # silently calling window.* functions that no longer matched the current
    # server-rendered HTML/other JS files. A fresh version segment on every
    # restart (i.e. every real deploy) gives stale-vs-current assets distinct
    # URLs, so guests never pay a revalidation round trip for assets that
    # haven't changed, and never see stale ones after a restart either.
    static_version = secrets.token_hex(4)
    templates.env.globals["static_version"] = static_version

    app.mount(
        f"/static/{static_version}",
        ImmutableStaticFiles(directory="kbox/web/static"),
        name="static",
    )

    # Routes, one router per area of the API (kbox/web/routes/). Included in
    # the order the handlers used to be registered in create_app.
    app.include_router(pages_routes.router)
    app.include_router(party_routes.router)
    app.include_router(queue_routes.router)
    app.include_router(search_routes.router)
    app.include_router(playback_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(config_routes.router)
    app.include_router(users_routes.router)
    app.include_router(history_routes.router)
    app.include_router(favorites_routes.router)

    return app
