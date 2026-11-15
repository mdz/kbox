"""
Page routes: the guest web UI, the TV display page and its song-ended
callback, and the root-level icons browsers probe for.

Templates come from :func:`kbox.web.dependencies.get_templates`, which
create_app populates (including the ``static_version`` global that the
pages' asset URLs depend on).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from ...config_manager import ConfigManager
from ...playback import PlaybackController
from ...suggestions import SuggestionEngine
from ..dependencies import (
    get_config_manager,
    get_playback_controller,
    get_suggestion_engine,
    get_templates,
)

router = APIRouter()


# Browsers (notably iOS Safari) probe these paths at the site root
# regardless of <link> tags, so serve them directly to avoid log noise.
@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("kbox/web/static/favicon.ico")


@router.get("/apple-touch-icon.png", include_in_schema=False)
def apple_touch_icon():
    return FileResponse("kbox/web/static/apple-touch-icon.png")


@router.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
def apple_touch_icon_precomposed():
    return FileResponse("kbox/web/static/apple-touch-icon-precomposed.png")


@router.get("/display", response_class=HTMLResponse)
def display(request: Request, templates: Jinja2Templates = Depends(get_templates)):
    """Fullscreen YouTube embed display for TV/monitor."""
    # Authenticate the session so API calls from this page work
    request.session["guest_authenticated"] = True
    return templates.TemplateResponse(request, "display.html")


@router.post("/api/display/song-ended")
def display_song_ended(
    playback: PlaybackController = Depends(get_playback_controller),
):
    """Signal that a song finished playing (called by /display when embed ends)."""
    playback.on_song_end()
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    config: ConfigManager = Depends(get_config_manager),
    templates: Jinja2Templates = Depends(get_templates),
    suggestion_engine: SuggestionEngine = Depends(get_suggestion_engine),
):
    """Serve web UI."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "long_song_warning_minutes": config.get_int("long_song_warning_minutes", 5),
            "duplicate_singer_nudge_enabled": config.get_bool(
                "duplicate_singer_nudge_enabled", True
            ),
            "search_suggestions_enabled": config.get_bool("search_suggestions_enabled", True),
            "suggestion_theme": config.get("suggestion_theme"),
            # Hide "Suggest for me" when there is no LLM to answer it.
            "ai_suggestions_enabled": bool(suggestion_engine and suggestion_engine.is_configured()),
        },
    )
