"""
Structural tests for the kbox.web package.

These assert that the web layer's pieces are importable on their own — the
point of splitting them out of create_app's scope. If a dependency provider
or request model drifts back into needing an application instance, these
fail before the API tests do.
"""


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


def test_server_uses_the_shared_dependency_providers():
    """create_app's handlers must depend on the providers in
    kbox.web.dependencies, not on private copies."""
    from kbox.web import dependencies, server

    assert server.get_queue_manager is dependencies.get_queue_manager
    assert server.require_user is dependencies.require_user


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
