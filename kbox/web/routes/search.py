"""
Search routes: video search, search-box autocomplete, AI song suggestions,
and video info lookup.

Handlers get their collaborators through the providers in
:mod:`kbox.web.dependencies`, so this router can be imported and inspected
without building an app.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ...database import EventRepository
from ...history import HistoryManager
from ...search_suggest import SearchSuggester
from ...song_width import SongWidthService
from ...suggestions import SuggestionEngine, SuggestionError
from ...user_events import SearchPerformed
from ...video_library import VideoLibrary
from ..dependencies import (
    get_current_user_id,
    get_event_repo,
    get_history_manager,
    get_search_suggester,
    get_song_width_service,
    get_suggestion_engine,
    get_video_library,
    require_user,
)

logger = logging.getLogger(__name__)

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

router = APIRouter()


def _mark_performed_before(results: List[Dict[str, Any]], history_mgr: HistoryManager) -> None:
    """
    Set ``performed_before`` on each result whose exact video has a recorded
    performance on this install (see issue #216).

    Deliberately a yes/no rather than a count: a count reads as how popular
    the song is, which could put guests off a crowd favourite, when the point
    is only that this version is known to work. It is a label, not a ranking
    input, so an untried and possibly better version keeps its place.

    Best effort: a failed lookup leaves the results unlabelled rather than
    failing the search.
    """
    try:
        performed = history_mgr.performed_video_ids(r["id"] for r in results if r.get("id"))
    except Exception:
        logger.warning("Failed to look up performance history for results", exc_info=True)
        return
    for result in results:
        result["performed_before"] = result.get("id") in performed


@router.get("/api/search")
def search_videos(
    q: str,
    max_results: int = 10,
    video_lib: VideoLibrary = Depends(get_video_library),
    current_user_id: Optional[str] = Depends(get_current_user_id),
    event_repo: EventRepository = Depends(get_event_repo),
    history_mgr: HistoryManager = Depends(get_history_manager),
):
    """Search for videos across all configured sources."""
    results = video_lib.search(q, max_results)
    _mark_performed_before(results, history_mgr)

    if current_user_id:
        try:
            event_repo.record(
                current_user_id,
                SearchPerformed(query=q, result_count=len(results)),
            )
        except Exception:
            logger.debug("Failed to log search event", exc_info=True)

    return {"results": results}


@router.get("/api/search/suggest")
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


@router.get("/api/suggestions")
def get_suggestions(
    max_results: int = 8,
    suggestion_engine: SuggestionEngine = Depends(get_suggestion_engine),
    current_user_id: str = Depends(require_user),
    history_mgr: HistoryManager = Depends(get_history_manager),
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
        _mark_performed_before(results, history_mgr)
        return {"results": results, "source": "ai"}
    except SuggestionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("Unexpected error generating suggestions: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500, detail="An error occurred while generating suggestions."
        )


@router.get("/api/song-width")
def get_song_width(
    video_id: str,
    title: str,
    channel: Optional[str] = None,
    user_id: Optional[str] = Depends(get_current_user_id),
    service: Optional[SongWidthService] = Depends(get_song_width_service),
):
    """
    How wide a song's vocal line is, beside the singer's past songs (#215).

    The add-song dialog asks for this after it opens, so a first lookup
    (a couple of USDB requests, about a second apart) never delays it. An
    empty object means "show nothing": USDB not configured, song unknown or
    not clearly matched, or USDB unreachable. A guest then sees exactly what
    they saw before this existed, so failures are logged by the service
    rather than surfaced here.

    title/channel come from the search result the guest tapped, saving a
    second YouTube lookup; they only feed working out artist and title.
    """
    if service is None:
        return {}
    return service.meter_for_video(user_id, video_id, title, channel) or {}


@router.get("/api/video/{video_id:path}")
def get_video_info(
    video_id: str,
    video_lib: VideoLibrary = Depends(get_video_library),
):
    """Get video information by opaque video ID."""
    info = video_lib.get_info(video_id)
    if not info:
        raise HTTPException(status_code=404, detail="Video not found")
    return info
