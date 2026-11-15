"""
User identity routes: register/resync, look up existing identities by name,
and explicitly claim one. See docs/development/guest-identity.md.
"""

import logging

from fastapi import APIRouter, Depends, Request

from ...identity import normalize_name
from ...user import UserManager
from ..dependencies import get_history_manager, get_user_manager
from ..models import UserRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/users")
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


@router.get("/api/users/lookup")
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


@router.post("/api/users/claim")
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
