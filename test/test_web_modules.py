"""
Structural tests for the kbox.web package.

These assert that the web layer's pieces are importable on their own — the
point of splitting them out of create_app's scope. If a dependency provider
or request model drifts back into needing an application instance, these
fail before the API tests do.
"""

import importlib
import inspect
import logging
import pkgutil
import re

import pytest
from starlette.routing import Match

import kbox.web.routes

# Every router module under kbox/web/routes/, discovered rather than listed,
# so a newly added router is covered without anyone remembering to add it.
ROUTER_MODULES = sorted(m.name for m in pkgutil.iter_modules(kbox.web.routes.__path__))


def test_dependency_providers_import_without_an_app():
    """The Depends providers resolve app state at call time, so importing
    them must not require a FastAPI app to exist."""
    from kbox.web import dependencies

    for name in (
        "get_queue_manager",
        "get_video_library",
        "get_playback_controller",
        "get_config_manager",
        "get_streaming_controller",
        "get_user_manager",
        "get_history_manager",
        "get_suggestion_engine",
        "get_favorites_manager",
        "get_search_suggester",
        "get_event_repo",
        "check_operator",
        "get_current_user_id",
        "require_user",
    ):
        assert callable(getattr(dependencies, name)), name


@pytest.mark.parametrize("module", ROUTER_MODULES)
def test_routes_use_the_shared_dependency_providers(module):
    """Route handlers must depend on the providers in kbox.web.dependencies,
    not on private copies. Checked through FastAPI's resolved dependency
    graph, so it holds wherever a handler happens to live."""
    from kbox.web import dependencies

    router = importlib.import_module(f"kbox.web.routes.{module}").router
    shared = {f for _, f in inspect.getmembers(dependencies, inspect.isfunction)}
    used = {d.call for r in router.routes for d in r.dependant.dependencies}
    assert used, f"expected the {module} routes to declare dependencies"
    assert used <= shared, used - shared


def test_request_models_import_without_an_app():
    from kbox.web.models import AddSongRequest, ConfigUpdateRequest

    assert AddSongRequest(user_id="u", video_id="youtube:x", title="t").pitch_semitones == 0
    assert ConfigUpdateRequest(key="k", value="v").value == "v"


def test_guest_auth_middleware_runs_without_create_app():
    """GuestAuthMiddleware gets its collaborators through the constructor,
    so it can guard a bare app — no create_app, no closure scope."""
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kbox.web.middleware import ClockTolerantSessionMiddleware, GuestAuthMiddleware

    class StubConfig:
        def get(self, key):
            return None  # fall back to the token given at construction

    app = FastAPI()

    @app.get("/")
    def root():
        return {"ok": True}

    # Same LIFO order create_app uses: session added last so it runs first.
    app.add_middleware(
        GuestAuthMiddleware,
        config_manager=StubConfig(),
        access_token="tok",
        templates=Jinja2Templates(directory="kbox/web/templates"),
    )
    app.add_middleware(ClockTolerantSessionMiddleware, secret_key="s")

    client = TestClient(app)
    denied = client.get("/", follow_redirects=False)
    assert denied.status_code == 401
    assert "Scan the QR code" in denied.text

    assert client.get("/?key=tok", follow_redirects=False).status_code == 302
    assert client.get("/").json() == {"ok": True}


@pytest.fixture
def app(tmp_path):
    from support.app_factory import build_test_app

    app, db, queue = build_test_app(tmp_path / "kbox.db", tmp_path / "cache")
    yield app
    queue.stop_content_monitor()
    db.close()


def _route_keys(routes):
    """(path, method) for every HTTP route, ignoring HEAD and FastAPI's own docs."""
    return {
        (r.path, m)
        for r in routes
        if getattr(r, "methods", None) and r.path not in FASTAPI_OWN_PATHS
        for m in r.methods - {"HEAD"}
    }


FASTAPI_OWN_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def test_every_route_lives_in_exactly_one_router(app):
    """Each area's routes live on its own module-level APIRouter, the routers
    don't overlap, and create_app registers no route of its own outside them."""
    per_router = {
        m: _route_keys(importlib.import_module(f"kbox.web.routes.{m}").router.routes)
        for m in ROUTER_MODULES
    }
    for module, keys in per_router.items():
        assert keys, f"routes.{module} registers no routes"
    union = set().union(*per_router.values())
    assert sum(len(k) for k in per_router.values()) == len(union), "two routers share a route"
    assert _route_keys(app.routes) == union


@pytest.mark.parametrize("value", ["x", "1", "a/b"])
def test_no_two_routes_match_the_same_request(app, value):
    """create_app's include_router order doesn't decide which handler serves a
    request, because no request is matched by more than one route. Sampled per
    path parameter with a plain token, a number and a multi-segment value (the
    last catches ``:path`` converters)."""
    routes = [r for r in app.routes if getattr(r, "methods", None)]
    for route in routes:
        path = re.sub(r"\{[^}]+\}", value, route.path)
        for method in route.methods - {"HEAD"}:
            scope = {"type": "http", "path": path, "method": method, "root_path": ""}
            matches = [o.path for o in routes if o.matches(scope)[0] is Match.FULL]
            assert len(matches) <= 1, f"{method} {path} matched by {matches}"


def test_route_modules_log_at_debug():
    """The web layer opts into DEBUG on the kbox.web package logger. Before the
    router split every handler logged through kbox.web.server, which was set
    to DEBUG itself; the routers must inherit that, or debug lines such as
    register_user's identity-creation diagnostic silently disappear."""
    import kbox.web.server  # noqa: F401  (applies the level)

    for module in ROUTER_MODULES:
        assert logging.getLogger(f"kbox.web.routes.{module}").isEnabledFor(logging.DEBUG)
