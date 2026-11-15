"""
FastAPI web server for kbox.

Provides REST API and web UI for queue management and playback control.
"""

import logging
import secrets
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config_manager import ConfigManager
from ..database import EventRepository
from ..identity import normalize_name
from ..playback import PlaybackController, PlaybackState, SeekOutcome
from ..queue import MoveOutcome, QueueManager
from ..search_suggest import SearchSuggester
from ..streaming import StreamingController
from ..suggestions import SuggestionEngine, SuggestionError
from ..user import UserManager
from ..video_library import VideoLibrary
from .dependencies import (
    check_operator,
    get_config_manager,
    get_current_user_id,
    get_event_repo,
    get_favorites_manager,
    get_history_manager,
    get_playback_controller,
    get_queue_manager,
    get_search_suggester,
    get_suggestion_engine,
    get_user_manager,
    get_video_library,
    require_user,
)
from .middleware import (
    ClockTolerantSessionMiddleware,
    GuestAuthMiddleware,
    ImmutableStaticFiles,
    RequestLogMiddleware,
)
from .models import (
    AddFavoriteRequest,
    AddSongRequest,
    ConfigUpdateRequest,
    OperatorAuthRequest,
    PitchRequest,
    ReorderRequest,
    ReplaceSongRequest,
    SeekRequest,
    UpdateQueueItemRequest,
    UserRequest,
)
from .serializers import serialize_queue_item

logger = logging.getLogger(__name__)
# Root logger stays at INFO (DEBUG globally is too spammy from third-party
# libs like httpx/litellm); opt this logger in on its own.
logger.setLevel(logging.DEBUG)

# Measured against the live endpoint, asking at every prefix length whether
# the song the guest was actually after was already on offer. Replayed over
# 185 real searches from user_events:
#
#     1 char   2%     2 chars   2%     3 chars  15%     4 chars  29%
#     5 chars 44%     6 chars  52%     8 chars  62%    11 chars  83%
#
# There is no knee to land on - it is a ramp - so this number is a judgement
# about where a dropdown stops being noise, not a measurement. 4 is roughly
# where the hit rate doubles off the floor, while still serving the real
# four-character queries in the corpus ("Mary", "cure", "River"), which a
# higher threshold would shut out entirely.
#
# An earlier version of this comment claimed a sharp knee at 3 characters and
# a 75% hit rate. That came from replaying 30 karaoke standards - the most
# searched songs on earth, which is precisely what a popularity-ranked
# suggestion endpoint is best at. Real queries are artist-plus-title, partial,
# misspelled and often obscure. Do not re-tune this against a corpus of
# well-known songs; see docs/development/search-autocomplete.md.
MIN_SUGGEST_QUERY_LENGTH = 4


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
    app.state.access_token = access_token
    # Autocomplete is self-contained (config + an HTTP call), so it's built
    # here rather than threaded through main.py's wiring.
    app.state.search_suggester = SearchSuggester(config_manager)

    # Templates. Built before the middleware because GuestAuthMiddleware
    # renders its access-required page from them.
    templates = Jinja2Templates(directory="kbox/web/templates")

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

    # Browsers (notably iOS Safari) probe these paths at the site root
    # regardless of <link> tags, so serve them directly to avoid log noise.
    from fastapi.responses import FileResponse

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse("kbox/web/static/favicon.ico")

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def apple_touch_icon():
        return FileResponse("kbox/web/static/apple-touch-icon.png")

    @app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
    def apple_touch_icon_precomposed():
        return FileResponse("kbox/web/static/apple-touch-icon-precomposed.png")

    # Queue endpoints
    @app.get("/api/queue")
    def get_queue(
        queue_mgr: QueueManager = Depends(get_queue_manager),
        playback: PlaybackController = Depends(get_playback_controller),
        current_user_id: Optional[str] = Depends(get_current_user_id),
    ):
        """Get current queue with current/played flags for UI rendering."""
        queue_items = queue_mgr.get_queue()

        status = playback.get_status()
        current_song = status.current_song
        current_song_id = current_song.id if current_song else None
        cursor_id = playback.get_cursor()

        # Everything before the cursor is "played"
        cursor_idx = next((i for i, item in enumerate(queue_items) if item.id == cursor_id), None)
        upcoming = playback.get_upcoming()

        # Serialize queue items for the frontend
        queue = []
        for i, item in enumerate(queue_items):
            item_dict = serialize_queue_item(item)
            item_dict["is_current"] = item.id == current_song_id
            item_dict["is_played"] = cursor_idx is not None and i < cursor_idx
            queue.append(item_dict)

        # Next song
        next_song = None
        if upcoming:
            s = upcoming[0].item
            next_song = {
                "id": s.id,
                "user_id": s.user_id,
                "user_name": s.user_name,
                "title": s.metadata.title,
                "duration_seconds": s.metadata.duration_seconds,
            }

        # How long until the current user's next turn?
        my_next_turn = None
        is_next_me = next_song and current_user_id and next_song["user_id"] == current_user_id
        if current_user_id and not is_next_me:
            mine = next((u for u in upcoming if u.item.user_id == current_user_id), None)
            if mine:
                my_next_turn = {
                    "estimated_seconds": mine.wait_seconds,
                    "songs_away": mine.position - 1,
                }

        # Queue depth: count and total duration of upcoming items
        queue_depth_count = len(upcoming)
        queue_depth_seconds = sum(u.item.metadata.duration_seconds or 0 for u in upcoming)

        return {
            "queue": queue,
            "current_song_id": current_song_id,
            "next_song": next_song,
            "my_next_turn": my_next_turn,
            "queue_depth_seconds": queue_depth_seconds,
            "queue_depth_count": queue_depth_count,
        }

    @app.get("/api/queue/settings/{video_id:path}")
    def get_song_settings(
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

    def queue_slot(playback: PlaybackController, item_id: int):
        """Position and estimated wait of an upcoming queue item, or None if
        it isn't upcoming (e.g. already played)."""
        for u in playback.get_upcoming():
            if u.item.id == item_id:
                return {"position": u.position, "estimated_seconds": u.wait_seconds}
        return None

    @app.post("/api/queue")
    def add_song(
        request_data: AddSongRequest,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        user_mgr: UserManager = Depends(get_user_manager),
        playback: PlaybackController = Depends(get_playback_controller),
        current_user_id: str = Depends(require_user),
    ):
        """Add song to queue. Responds with the new item's queue slot."""
        try:
            # Get user from session, not the request body — the session is
            # the source of truth for which self-declared identity this
            # browser tab is currently acting as.
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
            playback.show_notification(f"{user.display_name} added a song", duration_seconds=5.0)

            return {
                "id": item_id,
                "status": "added",
                "slot": queue_slot(playback, item_id),
            }
        except HTTPException:
            raise  # Let HTTP exceptions pass through
        except Exception as e:
            logger.error("Error adding song: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/queue/{item_id}")
    def remove_song(
        item_id: int,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
        current_user_id: Optional[str] = Depends(get_current_user_id),
    ):
        """
        Remove song from queue.

        Operators can remove any song. Users can remove their own songs.
        User identity is determined from session (not query params) — identity is
        self-declared and unverified, so this just fixes which UUID a write is
        attributed to, not who it "really" belongs to.
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

    @app.post("/api/queue/{item_id}/replace")
    def replace_song(
        item_id: int,
        request_data: ReplaceSongRequest,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
        current_user_id: Optional[str] = Depends(get_current_user_id),
    ):
        """
        Replace a queue item's song in place (e.g. wrong version was added).

        Keeps the item's queue position and attribution unchanged. Operators
        can replace any song; users can only replace their own.
        User identity is determined from session — self-declared and unverified
        by design, so this gates which UUID the current session is acting as,
        not proof of who that UUID belongs to.
        """
        item = queue_mgr.get_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        if not is_operator:
            if not current_user_id or current_user_id != item.user_id:
                raise HTTPException(status_code=403, detail="You can only replace songs you added")

        if not playback.replace_song(
            item_id,
            video_id=request_data.video_id,
            title=request_data.title,
            duration_seconds=request_data.duration_seconds,
            thumbnail_url=request_data.thumbnail_url,
            channel=request_data.channel,
        ):
            raise HTTPException(status_code=404, detail="Queue item not found")

        return {"status": "replaced", "slot": queue_slot(playback, item_id)}

    @app.patch("/api/queue/{item_id}/position")
    def reorder_song(
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
    def update_queue_item(
        item_id: int,
        request_data: UpdateQueueItemRequest,
        queue_mgr: QueueManager = Depends(get_queue_manager),
        is_operator: bool = Depends(check_operator),
        current_user_id: Optional[str] = Depends(get_current_user_id),
    ):
        """
        Update properties of a queue item.
        Operators can update any song, users can only update their own.
        User identity is determined from session — self-declared and unverified
        by design, so this gates which UUID the current session is acting as,
        not proof of who that UUID belongs to.

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
    def play_next(
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
    def move_to_end(
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
    def move_down(
        item_id: int,
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Move song down 1 position. Operator only."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        result = playback.move_down(item_id)

        if result.outcome is MoveOutcome.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Queue item not found")
        elif result.outcome is MoveOutcome.FAILED:
            raise HTTPException(status_code=500, detail="Failed to move down song")
        elif result.outcome is MoveOutcome.ALREADY_AT_BOUNDARY:
            return {"status": "already_at_end", "position": result.old_position}

        return {
            "status": "moved_down",
            "old_position": result.old_position,
            "new_position": result.new_position,
        }

    @app.post("/api/queue/{item_id}/move-up")
    def move_up(
        item_id: int,
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Move song up 1 position. Operator only."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        result = playback.move_up(item_id)

        if result.outcome is MoveOutcome.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Queue item not found")
        elif result.outcome is MoveOutcome.FAILED:
            raise HTTPException(status_code=500, detail="Failed to move up song")
        elif result.outcome is MoveOutcome.ALREADY_AT_BOUNDARY:
            return {"status": "already_at_start", "position": result.old_position}

        return {
            "status": "moved_up",
            "old_position": result.old_position,
            "new_position": result.new_position,
        }

    @app.post("/api/queue/clear")
    def clear_queue(
        queue_mgr: QueueManager = Depends(get_queue_manager),
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Clear entire queue (operator only)."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        count = queue_mgr.clear_queue()
        playback.clear_cursor()
        return {"status": "cleared", "items_removed": count}

    # Video search endpoints
    @app.get("/api/search")
    def search_videos(
        q: str,
        max_results: int = 10,
        video_lib: VideoLibrary = Depends(get_video_library),
        current_user_id: Optional[str] = Depends(get_current_user_id),
        event_repo: EventRepository = Depends(get_event_repo),
    ):
        """Search for videos across all configured sources."""
        results = video_lib.search(q, max_results)

        if current_user_id:
            try:
                event_repo.record(
                    current_user_id,
                    "search",
                    {"query": q, "result_count": len(results)},
                )
            except Exception:
                logger.debug("Failed to log search event", exc_info=True)

        return {"results": results}

    @app.get("/api/search/suggest")
    def suggest_search_queries(
        q: str,
        max_results: int = 8,
        suggester: SearchSuggester = Depends(get_search_suggester),
    ):
        """
        Autocomplete the search box.

        Returns an empty list for the cases that are not errors: disabled by
        the operator, query too short, or Google unreachable (which the
        suggester logs and backs off from).  The search box treats "no
        suggestions" as the normal case, so none of those reach a guest.

        Anything else — a reply we cannot parse, a bug in the suggester —
        is deliberately left to raise, producing a 500 and a traceback in the
        log.  The search box treats a 500 the same as an empty list, so the
        guest still sees nothing broken, but the failure is on the record
        rather than silently disabling the feature.

        Deliberately not recorded as a user event: a search event is logged
        when someone actually searches, and logging every keystroke prefix
        would bury that signal.
        """
        if len(q.strip()) < MIN_SUGGEST_QUERY_LENGTH:
            return {"suggestions": []}

        return {"suggestions": suggester.suggest(q, max_results)}

    @app.get("/api/suggestions")
    def get_suggestions(
        max_results: int = 8,
        suggestion_engine: SuggestionEngine = Depends(get_suggestion_engine),
        current_user_id: str = Depends(require_user),
    ):
        """
        Get AI-powered song suggestions for current user.

        Suggestions are based on user's history, current queue, and operator theme.
        User identity is determined from session — self-declared and unverified
        by design, so this gates which UUID the current session is acting as,
        not proof of who that UUID belongs to.
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
    def get_video_info(
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
    def get_playback_status(
        playback: PlaybackController = Depends(get_playback_controller),
    ):
        """Get current playback status."""
        status = playback.get_status()
        return {
            "state": status.state.value,
            "current_song": (
                serialize_queue_item(status.current_song) if status.current_song else None
            ),
            "position_seconds": status.position_seconds,
        }

    @app.post("/api/playback/play")
    def play(
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
    def pause(
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
    def stop(
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Stop playback and return to idle (operator only)."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        # Stopping when already stopped/idle is not an error - the desired
        # end state is already achieved, so just report success.
        playback.stop_playback()
        return {"status": "stopped"}

    @app.post("/api/playback/skip")
    def skip(
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
    def previous(
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
    def jump_to_song(
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
    def set_pitch(
        request_data: PitchRequest,
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
        current_user_id: Optional[str] = Depends(get_current_user_id),
    ):
        """
        Set pitch adjustment for current song.
        Allowed if: user owns the song OR user is an operator.
        User identity is determined from session — self-declared and unverified
        by design, so this gates which UUID the current session is acting as,
        not proof of who that UUID belongs to.
        """
        status = playback.get_status()
        current_song = status.current_song

        if not current_song:
            raise HTTPException(status_code=400, detail="No song is currently playing")

        # Check authorization: user's own song OR operator
        is_own_song = current_song.user_id == current_user_id

        if not is_own_song and not is_operator:
            raise HTTPException(
                status_code=403, detail="You can only adjust pitch for your own song"
            )

        if playback.set_pitch(request_data.semitones):
            return {"status": "updated", "pitch": request_data.semitones}
        else:
            raise HTTPException(status_code=400, detail="Failed to update pitch")

    @app.post("/api/playback/restart")
    def restart(
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Restart current song from the beginning (operator only)."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        result = playback.restart()
        if result.outcome is SeekOutcome.FAILED:
            raise HTTPException(status_code=400, detail="Failed to restart song")
        if result.outcome is SeekOutcome.NOTHING_PLAYING:
            # Not an error: the operator controls are visible whatever the
            # state, so the frontend treats this as a silent no-op.
            return {"status": "nothing_playing"}
        return {"status": "restarted"}

    @app.post("/api/playback/seek")
    def seek(
        request_data: SeekRequest,
        playback: PlaybackController = Depends(get_playback_controller),
        is_operator: bool = Depends(check_operator),
    ):
        """Seek forward or backward in current song (operator only)."""
        if not is_operator:
            raise HTTPException(status_code=403, detail="Operator authentication required")

        result = playback.seek_relative(request_data.delta_seconds)
        if result.outcome is SeekOutcome.FAILED:
            raise HTTPException(status_code=400, detail="Failed to seek")
        if result.outcome is SeekOutcome.NOTHING_PLAYING:
            # See the note in restart(): an expected no-op, not a failure.
            return {"status": "nothing_playing"}
        return {"status": "seeked", "position": result.position}

    # Authentication endpoints
    @app.get("/api/auth/operator")
    def check_operator_status(request: Request):
        """Check if user is currently authenticated as operator."""
        is_operator = check_operator(request)
        return {"operator": is_operator}

    @app.post("/api/auth/operator")
    def authenticate_operator(
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
    def logout_operator(request: Request):
        """Exit operator mode."""
        request.session["operator"] = False
        return {"status": "logged_out", "operator": False}

    # Configuration endpoints
    @app.get("/api/config")
    def get_config(config: ConfigManager = Depends(get_config_manager)):
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
    def update_config(
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

        # Refresh the idle screen if the theme changed and we're currently
        # showing it. If a song is playing, the next idle transition will
        # pick up the new theme automatically.
        if request_data.key == "suggestion_theme" and playback.state == PlaybackState.STOPPED:
            playback.show_idle_screen()

        return {
            "status": "updated",
            "key": request_data.key,
            "value": request_data.value,
        }

    # User endpoints
    @app.post("/api/users")
    def register_user(
        request: Request,
        request_data: UserRequest,
        user_mgr: UserManager = Depends(get_user_manager),
    ):
        """
        Register or update a user, without switching identity.

        On first call, binds the provided user_id to the session. On
        subsequent calls, uses the session's user_id (ignores provided one) —
        this is a quiet resync, not a way to change who this session is
        acting as. It stops a stale or concurrent request from switching an
        already-bound session's identity out from under it (see the
        window.fetch race backstop in auth.js); it has nothing to do with
        guest-vs-guest impersonation, which this system doesn't defend
        against (see docs/development/guest-identity.md).

        To deliberately switch a session to a different (recognized or
        brand-new) identity, use POST /api/users/claim instead.
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

        if user_mgr.get_user(user_id) is None:
            # About to create a new identity. Log enough to tell apart the two
            # root causes of identity churn: the client never had a UUID to
            # send (real client-side storage loss) vs. the client sent a UUID
            # that we simply didn't recognize (a lookup/session bug).
            logger.debug(
                "[DEBUG] Creating new user identity: user_id=%r client_sent_uuid=%s "
                "session_already_bound=%s display_name=%r user_agent=%r",
                user_id,
                bool(user_id),
                bool(session_user_id),
                request_data.display_name,
                request.headers.get("user-agent"),
            )

        user = user_mgr.get_or_create_user(user_id=user_id, display_name=request_data.display_name)
        user_mgr.touch_last_seen(user.id)
        return user

    @app.get("/api/users/lookup")
    def lookup_user(
        name: str,
        user_mgr: UserManager = Depends(get_user_manager),
        history_mgr=Depends(get_history_manager),
    ):
        """
        Find existing identities matching a typed name.

        This is the recognition step from docs/development/guest-identity.md:
        a guest types a name, the client shows any matches so they can
        recognize themselves (or say "none of these, I'm new"), then calls
        POST /api/users/claim with whichever user_id they land on.

        Unauthenticated by design — it runs before any identity is
        established for this session, so there's no session identity to
        check permissions against yet. Read-only, and the context returned
        per candidate (last song, last visit) isn't new exposure: everything
        in kbox is already visible to every guest at the party.
        """
        candidates = user_mgr.lookup_candidates(name)
        candidate_dicts = []
        for user in candidates:
            last_song_title = None
            recent = history_mgr.get_user_history(user.id, limit=1)
            if recent:
                last_song_title = recent[0].metadata.title
            candidate_dicts.append(
                {
                    "user_id": user.id,
                    "display_name": user.display_name,
                    "icon": user.icon,
                    "color": user.color,
                    "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
                    "last_song_title": last_song_title,
                }
            )
        return {"normalized_name": normalize_name(name), "candidates": candidate_dicts}

    @app.post("/api/users/claim")
    def claim_user(
        request: Request,
        request_data: UserRequest,
        user_mgr: UserManager = Depends(get_user_manager),
    ):
        """
        Explicitly claim an identity, switching the session to it.

        Called from the recognition flow with either a user_id the guest
        just recognized themselves in from GET /api/users/lookup, or a
        freshly client-generated one for "none of these, I'm new."

        Unlike POST /api/users, this unconditionally (re)binds the session
        to the given user_id, even if the session was already bound to
        something else. That's intentional: this endpoint only runs from a
        human-driven action (name entry after localStorage was empty) —
        exactly when a prior session binding is no longer trustworthy. The
        window.fetch race backstop in auth.js never calls this; it only
        resends the client's already-resolved identity via POST /api/users,
        so it can't itself trigger an identity switch.

        Claiming an existing user_id requires no proof beyond typing/tapping
        a name — a deliberate choice (see docs/development/guest-identity.md),
        not an oversight. UUIDs stay effectively unguessable (122 bits from
        crypto.randomUUID()), and this system has already decided
        guest-vs-guest impersonation isn't worth defending against at these
        stakes.
        """
        user = user_mgr.get_or_create_user(
            user_id=request_data.user_id, display_name=request_data.display_name
        )
        request.session["user_id"] = user.id
        user_mgr.touch_last_seen(user.id)
        return user

    # History endpoints
    @app.get("/api/history/{user_id}")
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

    # Favorites endpoints
    @app.post("/api/favorites")
    def add_favorite(
        request_data: AddFavoriteRequest,
        favorites_mgr=Depends(get_favorites_manager),
        current_user_id: str = Depends(require_user),
    ):
        """Star a song for the current user."""
        from ..models import SongMetadata

        metadata = SongMetadata(
            title=request_data.title,
            duration_seconds=request_data.duration_seconds,
            thumbnail_url=request_data.thumbnail_url,
            channel=request_data.channel,
        )
        favorites_mgr.add_favorite(current_user_id, request_data.video_id, metadata)
        return {"status": "favorited"}

    @app.delete("/api/favorites/{video_id}")
    def remove_favorite(
        video_id: str,
        favorites_mgr=Depends(get_favorites_manager),
        current_user_id: str = Depends(require_user),
    ):
        """Unstar a song for the current user."""
        favorites_mgr.remove_favorite(current_user_id, video_id)
        return {"status": "unfavorited"}

    @app.get("/api/favorites/{user_id}")
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

    # Web UI
    @app.get("/display", response_class=HTMLResponse)
    def display(request: Request):
        """Fullscreen YouTube embed display for TV/monitor."""
        # Authenticate the session so API calls from this page work
        request.session["guest_authenticated"] = True
        return templates.TemplateResponse(request, "display.html")

    @app.post("/api/display/song-ended")
    def display_song_ended(
        playback: PlaybackController = Depends(get_playback_controller),
    ):
        """Signal that a song finished playing (called by /display when embed ends)."""
        playback.on_song_end()
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, config: ConfigManager = Depends(get_config_manager)):
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
            },
        )

    return app
