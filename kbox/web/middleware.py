"""
Middleware and static-file handling for the kbox web app.

Everything here is a real class that owns its state: collaborators arrive
through the constructor (``app.add_middleware(Cls, **kwargs)`` forwards
keyword arguments), not from an enclosing scope. Ordering is not decided
here -- :func:`kbox.web.app.create_app` adds these in a specific LIFO
order that guest authentication depends on.
"""

import logging

import itsdangerous
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp

from ..config_manager import ConfigManager
from ..user import UserManager

logger = logging.getLogger(__name__)


class _ClockTolerantTimestampSigner(itsdangerous.TimestampSigner):
    """Accepts a session cookie whose embedded signing timestamp is later
    than the server's current clock, instead of treating it as expired.

    Hosts without a battery-backed RTC (e.g. a Raspberry Pi) boot with the
    system clock set to a stale time until NTP catches up. Any guest
    session cookie signed before that reboot then looks "signed in the
    future" to itsdangerous, which raises SignatureExpired the same as a
    tampered or genuinely-too-old cookie -- and SessionMiddleware responds
    by deleting the cookie from the guest's browser, permanently logging
    them out. The HMAC already proves the cookie wasn't tampered with, so
    a clock that's merely behind isn't a reason to distrust it.
    """

    def unsign(self, signed_value, max_age=None, return_timestamp=False):
        try:
            return super().unsign(signed_value, max_age=max_age, return_timestamp=return_timestamp)
        except itsdangerous.SignatureExpired as exc:
            if exc.date_signed is not None:
                age = self.get_timestamp() - int(exc.date_signed.timestamp())
                if age < 0:
                    return super().unsign(
                        signed_value, max_age=None, return_timestamp=return_timestamp
                    )
            raise


class ClockTolerantSessionMiddleware(SessionMiddleware):
    """SessionMiddleware that won't log guests out just because the
    server's clock is temporarily behind where it was when the cookie was
    signed. See _ClockTolerantTimestampSigner."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.signer.__class__ = _ClockTolerantTimestampSigner


class RequestLogMiddleware(BaseHTTPMiddleware):
    """Logs each request with the guest's user_id/display_name instead of IP.

    All guest traffic arrives via a Cloudflare Tunnel sidecar, so every
    request in uvicorn's access log shows the same source IP. user_id
    (from the session cookie) is the identity that actually distinguishes
    guests/devices.
    """

    def __init__(self, app: ASGIApp, user_manager: UserManager):
        super().__init__(app)
        self.user_manager = user_manager

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        user_id = request.session.get("user_id")
        who = "anonymous"
        if user_id:
            user = self.user_manager.get_user(user_id)
            who = f"{user.display_name} ({user_id})" if user else user_id
        logger.info(
            "%s %s -> %s user=%s",
            request.method,
            request.url.path,
            response.status_code,
            who,
        )
        return response


class GuestAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to authenticate guests via access token."""

    def __init__(
        self,
        app: ASGIApp,
        config_manager: ConfigManager,
        access_token: str,
        templates: Jinja2Templates,
    ):
        super().__init__(app)
        self.config_manager = config_manager
        self.access_token = access_token
        self.templates = templates

    async def dispatch(self, request: Request, call_next):
        # Allow display page and its API without authentication
        # (passive viewer on TV/monitor, no user session)
        if request.url.path == "/display" or request.url.path.startswith("/api/display/"):
            return await call_next(request)

        # Allow icon probes without authentication (browsers request
        # these automatically at the site root, before any session exists)
        if request.url.path in (
            "/favicon.ico",
            "/apple-touch-icon.png",
            "/apple-touch-icon-precomposed.png",
        ):
            return await call_next(request)

        # Check if already authenticated via session
        if request.session.get("guest_authenticated"):
            return await call_next(request)

        # Check for access token in query params. Read the current
        # value from config on each request (not the `access_token`
        # passed at construction) since it can be rotated live when a new
        # party session starts.
        key = request.query_params.get("key")
        current_token = self.config_manager.get("access_token") or self.access_token
        if key == current_token:
            # Valid token - set session and redirect to clean URL
            request.session["guest_authenticated"] = True
            # Redirect to same path without the key param
            clean_url = str(request.url).split("?")[0]
            return RedirectResponse(url=clean_url, status_code=302)

        # Not authenticated - return friendly error page
        return self.templates.TemplateResponse(request, "access_required.html", status_code=401)


class ImmutableStaticFiles(StaticFiles):
    """StaticFiles that tells browsers to cache forever.

    Only safe because create_app mounts it under a per-process version
    segment, so a deploy changes every asset URL; see the comment on the
    mount there for why that matters.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
