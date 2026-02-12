"""
API endpoint tests for kbox.

Tests all API endpoints with:
- Smoke tests for basic request/response verification
- Detailed tests for critical endpoints (queue, playback, auth)
"""

import os
import tempfile
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.history import HistoryManager
from kbox.playback import PlaybackController, PlaybackState
from kbox.queue import QueueManager
from kbox.suggestions import SuggestionEngine, SuggestionError
from kbox.user import UserManager
from kbox.video_library import VideoLibrary
from kbox.web.server import create_app

# Test user IDs
ALICE_ID = "alice-uuid-1234"
BOB_ID = "bob-uuid-5678"


@pytest.fixture
def temp_db():
    """Create a temporary database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path=path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_streaming():
    """Create a mock StreamingController."""
    streaming = Mock()
    streaming.set_pitch_shift = Mock()
    streaming.load_file = Mock()
    streaming.pause = Mock()
    streaming.resume = Mock()
    streaming.stop = Mock()
    streaming.stop_playback = Mock()
    streaming.set_eos_callback = Mock()
    streaming.get_position = Mock(return_value=0)
    streaming.seek = Mock(return_value=True)
    streaming.show_notification = Mock()
    streaming.display_image = Mock()
    streaming.reinitialize_pipeline = Mock()
    streaming.server = None
    return streaming


@pytest.fixture
def mock_playback():
    """Create a mock PlaybackController.

    API tests should use a mock playback controller since they're testing
    the API layer, not playback logic. Only test_playback and test_integration
    should use the real PlaybackController.
    """
    playback = Mock(spec=PlaybackController)
    # get_status returns a dict with state info
    playback.get_status.return_value = {
        "state": PlaybackState.IDLE.value,
        "current_song": None,
        "position_seconds": 0,
        "duration_seconds": 0,
    }
    # Cursor defaults (no cursor set)
    playback.get_cursor.return_value = None
    playback.get_cursor_position.return_value = None
    # Movement operations return bool
    playback.move_to_next.return_value = True
    playback.move_to_end.return_value = True
    playback.move_down.return_value = True
    playback.move_up.return_value = True
    # Playback control operations return bool
    playback.play.return_value = False  # False = no songs to play
    playback.pause.return_value = True
    playback.stop_playback.return_value = True
    playback.skip.return_value = True
    playback.previous.return_value = True
    playback.jump_to_song.return_value = True
    playback.restart.return_value = True
    playback.seek_relative.return_value = True
    # Pitch control
    playback.set_pitch.return_value = False  # False = no song playing
    # Properties
    playback.state = PlaybackState.IDLE
    playback.current_song_id = None
    return playback


@pytest.fixture
def mock_video_library():
    """Create a mock VideoLibrary for API testing.

    API tests focus on the HTTP layer, not video logic, so we use a simple mock.
    """
    video_library = Mock(spec=VideoLibrary)

    # Configure search to return test results with opaque IDs
    video_library.search.return_value = [
        {"id": "youtube:test123", "title": "Test Song", "duration_seconds": 180}
    ]

    # Configure get_info to return test data with opaque ID
    video_library.get_info.return_value = {
        "id": "youtube:test123",
        "title": "Test Song",
        "duration_seconds": 180,
        "thumbnail_url": "https://example.com/thumb.jpg",
        "channel": "Test Channel",
    }

    # Configure other methods
    video_library.request.return_value = None  # Async download
    video_library.get_path.return_value = None
    video_library.is_available.return_value = False
    video_library.is_source_configured.return_value = True
    video_library.manage_storage.return_value = 0

    return video_library


@pytest.fixture
def mock_suggestion_engine():
    """Create a mock SuggestionEngine for API testing.

    API tests focus on the HTTP layer, not suggestion logic.
    """
    engine = Mock(spec=SuggestionEngine)

    # Default: not configured
    engine.is_configured.return_value = False
    engine.get_suggestions.side_effect = SuggestionError("AI suggestions not configured")

    return engine


@pytest.fixture
def app_components(
    temp_db,
    temp_cache_dir,
    mock_streaming,
    mock_video_library,
    mock_playback,
    mock_suggestion_engine,
):
    """Create all app components with mocked dependencies.

    Uses mock playback, video_library, and suggestion_engine since API tests
    focus on the HTTP layer, not business logic.
    """
    config_manager = ConfigManager(temp_db)
    config_manager.set("youtube_api_key", "test_key")
    config_manager.set("cache_directory", temp_cache_dir)
    config_manager.set("transition_duration_seconds", "0")
    config_manager.set("operator_pin", "1234")

    user_manager = UserManager(temp_db)
    queue_manager = QueueManager(temp_db, video_library=mock_video_library)
    history_manager = HistoryManager(temp_db)

    yield {
        "config": config_manager,
        "queue": queue_manager,
        "user": user_manager,
        "video_library": mock_video_library,
        "streaming": mock_streaming,
        "playback": mock_playback,
        "history": history_manager,
        "suggestion_engine": mock_suggestion_engine,
    }

    # Cleanup
    queue_manager.stop_download_monitor()


@pytest.fixture
def client(app_components):
    """Create test client with all components."""
    app = create_app(
        queue_manager=app_components["queue"],
        video_library=app_components["video_library"],
        playback_controller=app_components["playback"],
        config_manager=app_components["config"],
        user_manager=app_components["user"],
        history_manager=app_components["history"],
        suggestion_engine=app_components["suggestion_engine"],
        streaming_controller=app_components["streaming"],
    )
    return TestClient(app)


@pytest.fixture
def alice(app_components):
    """Create test user Alice."""
    return app_components["user"].get_or_create_user(ALICE_ID, "Alice")


@pytest.fixture
def bob(app_components):
    """Create test user Bob."""
    return app_components["user"].get_or_create_user(BOB_ID, "Bob")


def set_operator(client):
    """Authenticate as operator."""
    response = client.post("/api/auth/operator", json={"pin": "1234"})
    assert response.status_code == 200


def set_user(client, user_id, display_name):
    """Register user and establish session identity.

    This binds the user_id to the session cookie, preventing impersonation.
    Must be called before making authenticated API requests.
    """
    response = client.post(
        "/api/users",
        json={"user_id": user_id, "display_name": display_name},
    )
    assert response.status_code == 200
    return response.json()


# =============================================================================
# Queue Endpoints - Detailed Tests
# =============================================================================


class TestQueueEndpoints:
    """Tests for queue management endpoints."""

    def test_get_queue_empty(self, client):
        """GET /api/queue - empty queue."""
        response = client.get("/api/queue")
        assert response.status_code == 200
        data = response.json()
        assert "queue" in data
        assert data["queue"] == []

    def test_add_song(self, client, alice):
        """POST /api/queue - add song to queue."""
        # Establish session identity
        set_user(client, ALICE_ID, "Alice")

        response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "video_id": "youtube:test123",
                "title": "Test Song",
                "duration_seconds": 180,
                "pitch_semitones": 0,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "added"
        assert "id" in data

    def test_add_song_not_authenticated(self, client):
        """POST /api/queue - unauthenticated request returns 401."""
        # Without establishing session identity first, requests are rejected
        response = client.post(
            "/api/queue",
            json={
                "user_id": "nonexistent-user",
                "video_id": "youtube:test123",
                "title": "Test Song",
            },
        )
        assert response.status_code == 401
        assert "Not authenticated" in response.json()["detail"]

    def test_get_queue_with_songs(self, client, alice, bob, app_components):
        """GET /api/queue - queue with songs."""
        # Add songs directly via queue manager (bypasses session auth for test setup)
        queue_mgr = app_components["queue"]
        queue_mgr.add_song(
            user=alice,
            video_id="youtube:song1",
            title="Alice's Song",
        )
        queue_mgr.add_song(
            user=bob,
            video_id="youtube:song2",
            title="Bob's Song",
        )

        response = client.get("/api/queue")
        assert response.status_code == 200
        data = response.json()
        assert len(data["queue"]) == 2

    def test_remove_song_as_owner(self, client, alice):
        """DELETE /api/queue/{id} - owner can remove their song."""
        # Establish session identity
        set_user(client, ALICE_ID, "Alice")

        # Add song
        add_response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "video_id": "youtube:test123",
                "title": "Test Song",
            },
        )
        item_id = add_response.json()["id"]

        # Remove as owner (session identity is used, not query param)
        response = client.delete(f"/api/queue/{item_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "removed"

    def test_remove_song_not_owner(self, client, alice, bob, app_components):
        """DELETE /api/queue/{id} - non-owner cannot remove song."""
        # Add Alice's song directly via queue manager
        queue_mgr = app_components["queue"]
        item_id = queue_mgr.add_song(
            user=alice,
            video_id="youtube:test123",
            title="Test Song",
        )

        # Bob tries to remove (establish Bob's session)
        set_user(client, BOB_ID, "Bob")
        response = client.delete(f"/api/queue/{item_id}")
        assert response.status_code == 403

    def test_remove_song_as_operator(self, client, alice, app_components):
        """DELETE /api/queue/{id} - operator can remove any song."""
        # Add song directly via queue manager
        queue_mgr = app_components["queue"]
        item_id = queue_mgr.add_song(
            user=alice,
            video_id="youtube:test123",
            title="Test Song",
        )

        # Auth as operator and remove
        set_operator(client)
        response = client.delete(f"/api/queue/{item_id}")
        assert response.status_code == 200

    def test_reorder_song_requires_operator(self, client, alice, app_components):
        """PATCH /api/queue/{id}/position - requires operator."""
        # Add song directly via queue manager
        queue_mgr = app_components["queue"]
        item_id = queue_mgr.add_song(
            user=alice,
            video_id="youtube:test123",
            title="Test Song",
        )

        response = client.patch(f"/api/queue/{item_id}/position", json={"new_position": 1})
        assert response.status_code == 403

    def test_update_queue_item_pitch(self, client, alice):
        """PATCH /api/queue/{id} - update pitch as owner."""
        # Establish session identity
        set_user(client, ALICE_ID, "Alice")

        add_response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "video_id": "youtube:test123",
                "title": "Test Song",
                "pitch_semitones": 0,
            },
        )
        item_id = add_response.json()["id"]

        response = client.patch(
            f"/api/queue/{item_id}",
            json={"pitch_semitones": 3},  # user_id from session, not body
        )
        assert response.status_code == 200
        assert response.json()["status"] == "updated"

    def test_clear_queue_requires_operator(self, client, alice, app_components):
        """POST /api/queue/clear - requires operator."""
        # Add song directly via queue manager
        queue_mgr = app_components["queue"]
        queue_mgr.add_song(
            user=alice,
            video_id="youtube:test123",
            title="Test Song",
        )

        response = client.post("/api/queue/clear")
        assert response.status_code == 403

    def test_clear_queue_as_operator(self, client, alice, app_components):
        """POST /api/queue/clear - operator can clear queue."""
        # Add song directly via queue manager
        queue_mgr = app_components["queue"]
        queue_mgr.add_song(
            user=alice,
            video_id="youtube:test123",
            title="Test Song",
        )

        set_operator(client)
        response = client.post("/api/queue/clear")
        assert response.status_code == 200
        assert response.json()["status"] == "cleared"

    def test_get_song_settings(self, client, alice):
        """GET /api/queue/settings/{video_id} - get saved settings."""
        # Establish session identity (no longer uses query param)
        set_user(client, ALICE_ID, "Alice")

        response = client.get("/api/queue/settings/youtube:test123")
        assert response.status_code == 200
        # No history yet, so settings should be None
        assert response.json()["settings"] is None

    def test_play_next_requires_operator(self, client, alice, app_components):
        """POST /api/queue/{id}/play-next - requires operator."""
        # Add song directly via queue manager
        queue_mgr = app_components["queue"]
        item_id = queue_mgr.add_song(
            user=alice,
            video_id="youtube:test123",
            title="Test Song",
        )

        response = client.post(f"/api/queue/{item_id}/play-next")
        assert response.status_code == 403

    def test_move_to_end_requires_operator(self, client, alice, app_components):
        """POST /api/queue/{id}/move-to-end - requires operator."""
        # Add song directly via queue manager
        queue_mgr = app_components["queue"]
        item_id = queue_mgr.add_song(
            user=alice,
            video_id="youtube:test123",
            title="Test Song",
        )

        response = client.post(f"/api/queue/{item_id}/move-to-end")
        assert response.status_code == 403

    def test_move_down_requires_operator(self, client, alice, app_components):
        """POST /api/queue/{id}/move-down - requires operator."""
        # Add song directly via queue manager
        queue_mgr = app_components["queue"]
        item_id = queue_mgr.add_song(
            user=alice,
            video_id="youtube:test123",
            title="Test Song",
        )

        response = client.post(f"/api/queue/{item_id}/move-down")
        assert response.status_code == 403

    def test_move_up_requires_operator(self, client, alice, app_components):
        """POST /api/queue/{id}/move-up - requires operator."""
        # Add song directly via queue manager
        queue_mgr = app_components["queue"]
        item_id = queue_mgr.add_song(
            user=alice,
            video_id="youtube:test123",
            title="Test Song",
        )

        response = client.post(f"/api/queue/{item_id}/move-up")
        assert response.status_code == 403


# =============================================================================
# Playback Endpoints - Detailed Tests
# =============================================================================


class TestPlaybackEndpoints:
    """Tests for playback control endpoints."""

    def test_get_playback_status(self, client):
        """GET /api/playback/status - get current status."""
        response = client.get("/api/playback/status")
        assert response.status_code == 200
        data = response.json()
        assert "state" in data

    def test_play_requires_operator(self, client):
        """POST /api/playback/play - requires operator."""
        response = client.post("/api/playback/play")
        assert response.status_code == 403

    def test_pause_requires_operator(self, client):
        """POST /api/playback/pause - requires operator."""
        response = client.post("/api/playback/pause")
        assert response.status_code == 403

    def test_stop_requires_operator(self, client):
        """POST /api/playback/stop - requires operator."""
        response = client.post("/api/playback/stop")
        assert response.status_code == 403

    def test_skip_requires_operator(self, client):
        """POST /api/playback/skip - requires operator."""
        response = client.post("/api/playback/skip")
        assert response.status_code == 403

    def test_previous_requires_operator(self, client):
        """POST /api/playback/previous - requires operator."""
        response = client.post("/api/playback/previous")
        assert response.status_code == 403

    def test_jump_requires_operator(self, client):
        """POST /api/playback/jump/{id} - requires operator."""
        response = client.post("/api/playback/jump/1")
        assert response.status_code == 403

    def test_restart_requires_operator(self, client):
        """POST /api/playback/restart - requires operator."""
        response = client.post("/api/playback/restart")
        assert response.status_code == 403

    def test_seek_requires_operator(self, client):
        """POST /api/playback/seek - requires operator."""
        response = client.post("/api/playback/seek", json={"delta_seconds": 10})
        assert response.status_code == 403

    def test_pitch_no_song_playing(self, client, alice):
        """POST /api/playback/pitch - fails when no song playing."""
        response = client.post(
            "/api/playback/pitch",
            json={"semitones": 3, "user_id": ALICE_ID},
        )
        assert response.status_code == 400
        assert "No song is currently playing" in response.json()["detail"]

    def test_play_as_operator(self, client):
        """POST /api/playback/play - operator can play."""
        set_operator(client)
        # Will fail with 400 because no songs in queue, but not 403
        response = client.post("/api/playback/play")
        assert response.status_code == 400  # No songs to play

    def test_skip_as_operator(self, client):
        """POST /api/playback/skip - operator can skip."""
        set_operator(client)
        response = client.post("/api/playback/skip")
        # Returns 200 with status even if no next song
        assert response.status_code == 200

    def test_previous_as_operator(self, client):
        """POST /api/playback/previous - operator can go previous."""
        set_operator(client)
        response = client.post("/api/playback/previous")
        # Returns 200 with status even if no previous song
        assert response.status_code == 200


# =============================================================================
# Auth Endpoints - Detailed Tests
# =============================================================================


class TestAuthEndpoints:
    """Tests for authentication endpoints."""

    def test_check_operator_not_authenticated(self, client):
        """GET /api/auth/operator - not authenticated."""
        response = client.get("/api/auth/operator")
        assert response.status_code == 200
        assert response.json()["operator"] is False

    def test_authenticate_operator_success(self, client):
        """POST /api/auth/operator - successful auth."""
        response = client.post("/api/auth/operator", json={"pin": "1234"})
        assert response.status_code == 200
        assert response.json()["operator"] is True

    def test_authenticate_operator_wrong_pin(self, client):
        """POST /api/auth/operator - wrong PIN."""
        response = client.post("/api/auth/operator", json={"pin": "9999"})
        assert response.status_code == 401

    def test_check_operator_after_auth(self, client):
        """GET /api/auth/operator - after authentication."""
        client.post("/api/auth/operator", json={"pin": "1234"})
        response = client.get("/api/auth/operator")
        assert response.status_code == 200
        assert response.json()["operator"] is True

    def test_logout_operator(self, client):
        """POST /api/auth/logout - logout."""
        client.post("/api/auth/operator", json={"pin": "1234"})
        response = client.post("/api/auth/logout")
        assert response.status_code == 200
        assert response.json()["operator"] is False

    def test_check_operator_after_logout(self, client):
        """GET /api/auth/operator - after logout."""
        client.post("/api/auth/operator", json={"pin": "1234"})
        client.post("/api/auth/logout")
        response = client.get("/api/auth/operator")
        assert response.status_code == 200
        assert response.json()["operator"] is False


# =============================================================================
# Video Search Endpoints - Smoke Tests
# =============================================================================


class TestVideoSearchEndpoints:
    """Smoke tests for video search endpoints."""

    def test_search(self, client):
        """GET /api/search - search videos."""
        response = client.get("/api/search?q=test")
        assert response.status_code == 200
        assert "results" in response.json()

    def test_get_video_info(self, client):
        """GET /api/video/{source}/{id} - get video info."""
        response = client.get("/api/video/youtube/test123")
        assert response.status_code == 200
        data = response.json()
        assert "id" in data


# =============================================================================
# Config Endpoints - Smoke Tests
# =============================================================================


class TestConfigEndpoints:
    """Smoke tests for configuration endpoints."""

    def test_get_config(self, client):
        """GET /api/config - get configuration with schema metadata."""
        response = client.get("/api/config")
        assert response.status_code == 200
        data = response.json()
        assert "values" in data
        assert "schema" in data
        assert "groups" in data
        # Verify schema contains expected keys
        assert "audio_output_device" in data["schema"]
        assert "operator_pin" in data["schema"]
        # Verify groups are defined
        assert "audio" in data["groups"]
        assert "security" in data["groups"]

    def test_update_config_requires_operator(self, client):
        """PATCH /api/config - requires operator."""
        response = client.patch("/api/config", json={"key": "test_key", "value": "test_value"})
        assert response.status_code == 403

    def test_update_config_as_operator(self, client):
        """PATCH /api/config - operator can update."""
        set_operator(client)
        response = client.patch("/api/config", json={"key": "test_key", "value": "test_value"})
        assert response.status_code == 200
        assert response.json()["status"] == "updated"

    def test_update_non_streaming_config_no_restart(self, client):
        """PATCH /api/config - non-streaming config changes don't restart streaming."""
        set_operator(client)
        response = client.patch("/api/config", json={"key": "operator_pin", "value": "5678"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert "restarted" not in data  # Should not include restart flag

    def test_update_audio_config_restarts_streaming(self, client, mock_streaming):
        """PATCH /api/config - audio config changes reinitialize pipeline."""
        set_operator(client)

        # Get the streaming controller
        streaming = client.app.state.streaming_controller

        # Update audio config
        response = client.patch(
            "/api/config", json={"key": "audio_output_device", "value": "test_device"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data.get("restarted") is True
        assert "message" in data

        # Verify reinitialize_pipeline was called on the same controller
        streaming.reinitialize_pipeline.assert_called_once()

    def test_update_video_config_restarts_streaming(self, client, mock_streaming):
        """PATCH /api/config - video overlay config changes reinitialize pipeline."""
        set_operator(client)

        # Get the streaming controller
        streaming = client.app.state.streaming_controller

        # Update video config
        response = client.patch(
            "/api/config", json={"key": "overlay_qr_position", "value": "bottom-right"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data.get("restarted") is True

        # Verify reinitialize_pipeline was called on the same controller
        streaming.reinitialize_pipeline.assert_called_once()


# =============================================================================
# User Endpoints - Smoke Tests
# =============================================================================


class TestUserEndpoints:
    """Smoke tests for user endpoints."""

    def test_register_user(self, client):
        """POST /api/users - register user."""
        response = client.post(
            "/api/users",
            json={"user_id": "new-user-123", "display_name": "New User"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "new-user-123"  # User model uses 'id' not 'user_id'
        assert data["display_name"] == "New User"

    def test_update_user_name(self, client):
        """POST /api/users - update existing user."""
        client.post(
            "/api/users",
            json={"user_id": "user-123", "display_name": "Original Name"},
        )
        response = client.post(
            "/api/users",
            json={"user_id": "user-123", "display_name": "Updated Name"},
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "Updated Name"


# =============================================================================
# History Endpoints - Smoke Tests
# =============================================================================


class TestHistoryEndpoints:
    """Smoke tests for history endpoints."""

    def test_get_user_history_empty(self, client, alice):
        """GET /api/history/{user_id} - empty history."""
        # Establish session identity (users can only view their own history)
        set_user(client, ALICE_ID, "Alice")

        response = client.get(f"/api/history/{ALICE_ID}")
        assert response.status_code == 200
        assert response.json()["history"] == []


# =============================================================================
# Web UI Endpoint - Smoke Test
# =============================================================================


class TestWebUIEndpoint:
    """Smoke test for web UI endpoint."""

    def test_index(self, client):
        """GET / - serve web UI."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


# =============================================================================
# Authentication Tests
# =============================================================================


class TestGuestAuthentication:
    """Tests for guest token authentication."""

    @pytest.fixture
    def auth_app(self, app_components):
        """Create app with access token enabled."""
        return create_app(
            queue_manager=app_components["queue"],
            video_library=app_components["video_library"],
            playback_controller=app_components["playback"],
            config_manager=app_components["config"],
            user_manager=app_components["user"],
            history_manager=app_components["history"],
            streaming_controller=app_components["streaming"],
            access_token="test-secret-token-123",
            session_secret="test-session-secret",
        )

    @pytest.fixture
    def auth_client(self, auth_app):
        """Create test client for auth-enabled app."""
        return TestClient(auth_app)

    def test_unauthenticated_request_returns_401(self, auth_client):
        """Request without token returns 401 with friendly page."""
        response = auth_client.get("/", follow_redirects=False)
        assert response.status_code == 401
        assert "Scan the QR code" in response.text

    def test_unauthenticated_api_request_returns_401(self, auth_client):
        """API request without token returns 401."""
        response = auth_client.get("/api/queue", follow_redirects=False)
        assert response.status_code == 401

    def test_valid_token_redirects_and_sets_session(self, auth_client):
        """Valid token in query param redirects to clean URL."""
        response = auth_client.get("/?key=test-secret-token-123", follow_redirects=False)
        assert response.status_code == 302
        # Redirect location may include host (http://testserver/) or just path (/)
        assert response.headers["location"].endswith("/")

    def test_valid_token_establishes_session(self, auth_client):
        """After valid token, subsequent requests work via session."""
        # First request with token - should redirect
        response = auth_client.get("/?key=test-secret-token-123", follow_redirects=False)
        assert response.status_code == 302

        # Follow the redirect (session cookie should be set)
        response = auth_client.get("/", follow_redirects=False)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_invalid_token_returns_401(self, auth_client):
        """Invalid token returns 401."""
        response = auth_client.get("/?key=wrong-token", follow_redirects=False)
        assert response.status_code == 401

    def test_session_persists_for_api_calls(self, auth_client):
        """Once authenticated, API calls work via session."""
        # Authenticate first
        auth_client.get("/?key=test-secret-token-123", follow_redirects=False)

        # API call should work
        response = auth_client.get("/api/queue")
        assert response.status_code == 200

    def test_no_token_configured_allows_all_requests(self, client):
        """When no access token configured, all requests allowed."""
        # The default 'client' fixture has no access_token
        response = client.get("/")
        assert response.status_code == 200

        response = client.get("/api/queue")
        assert response.status_code == 200


# =============================================================================
# Suggestions Endpoints
# =============================================================================


class TestSuggestionsEndpoints:
    """Tests for AI suggestion endpoints.

    Uses mock SuggestionEngine since API tests focus on HTTP layer.
    """

    def test_suggestions_returns_503_when_not_configured(self, client, alice):
        """GET /api/suggestions - returns 503 when AI not configured."""
        # Establish session identity (user_id from session, not query param)
        set_user(client, ALICE_ID, "Alice")

        # Default mock raises SuggestionError("not configured")
        response = client.get("/api/suggestions")
        assert response.status_code == 503
        data = response.json()
        assert "not configured" in data["detail"].lower()

    def test_suggestions_requires_authentication(self, client):
        """GET /api/suggestions - requires session authentication."""
        # Without session, should return 401
        response = client.get("/api/suggestions")
        assert response.status_code == 401

    def test_suggestions_returns_results_when_configured(self, client, alice, app_components):
        """GET /api/suggestions - returns suggestions when AI is configured."""
        # Establish session identity
        set_user(client, ALICE_ID, "Alice")

        # Configure mock to return results
        app_components["suggestion_engine"].get_suggestions.side_effect = None
        app_components["suggestion_engine"].get_suggestions.return_value = [
            {
                "id": "youtube:result1",
                "title": "Suggested Song Karaoke",
                "channel": "Karaoke Channel",
                "thumbnail": "https://example.com/thumb.jpg",
            },
            {
                "id": "youtube:result2",
                "title": "Another Song Karaoke",
                "channel": "Karaoke Channel",
                "thumbnail": "https://example.com/thumb2.jpg",
            },
        ]

        response = client.get("/api/suggestions")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert data["source"] == "ai"
        assert len(data["results"]) == 2

    def test_suggestions_passes_max_results(self, client, alice, app_components):
        """GET /api/suggestions - passes max_results to engine."""
        # Establish session identity
        set_user(client, ALICE_ID, "Alice")

        # Configure mock to return results
        app_components["suggestion_engine"].get_suggestions.side_effect = None
        app_components["suggestion_engine"].get_suggestions.return_value = [
            {"id": "youtube:vid1", "title": "Song 1", "channel": "Test"},
        ]

        response = client.get("/api/suggestions?max_results=5")
        assert response.status_code == 200

        # Verify max_results was passed to engine
        app_components["suggestion_engine"].get_suggestions.assert_called_once_with(ALICE_ID, 5)

    def test_suggestions_handles_engine_error(self, client, alice, app_components):
        """GET /api/suggestions - returns 503 on engine error."""
        # Establish session identity
        set_user(client, ALICE_ID, "Alice")

        # Configure mock to raise error
        app_components["suggestion_engine"].get_suggestions.side_effect = SuggestionError(
            "Could not find karaoke videos"
        )

        response = client.get("/api/suggestions")
        assert response.status_code == 503
        data = response.json()
        assert "could not find" in data["detail"].lower()
