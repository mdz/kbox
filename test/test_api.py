"""
API endpoint tests for kbox.

Tests all 30 API endpoints with:
- Smoke tests for basic request/response verification
- Detailed tests for critical endpoints (queue, playback, auth)
"""

import os
import tempfile
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.history import HistoryManager
from kbox.playback import PlaybackController
from kbox.queue import QueueManager
from kbox.user import UserManager
from kbox.web.server import create_app
from kbox.youtube import YouTubeClient

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
    streaming.server = None
    return streaming


@pytest.fixture
def mock_youtube(temp_cache_dir):
    """Create a mock YouTubeClient."""
    with patch("kbox.youtube.build") as mock_build:
        mock_yt = Mock()
        mock_build.return_value = mock_yt
        client = YouTubeClient("test_key", cache_directory=temp_cache_dir)
        client.youtube = mock_yt

        # Mock search results
        client.search = Mock(
            return_value=[{"video_id": "test123", "title": "Test Song", "duration_seconds": 180}]
        )
        client.get_video_info = Mock(
            return_value={
                "video_id": "test123",
                "title": "Test Song",
                "duration_seconds": 180,
                "thumbnail_url": "https://example.com/thumb.jpg",
                "channel": "Test Channel",
            }
        )
        yield client


@pytest.fixture
def app_components(temp_db, temp_cache_dir, mock_streaming, mock_youtube):
    """Create all app components with mocked dependencies."""
    config_manager = ConfigManager(temp_db)
    config_manager.set("youtube_api_key", "test_key")
    config_manager.set("cache_directory", temp_cache_dir)
    config_manager.set("transition_duration_seconds", "0")
    config_manager.set("operator_pin", "1234")

    user_manager = UserManager(temp_db)
    queue_manager = QueueManager(temp_db)
    history_manager = HistoryManager(temp_db)

    playback_controller = PlaybackController(queue_manager, mock_streaming, config_manager)
    # Stop position tracking thread
    playback_controller._tracking_position = False

    return {
        "config": config_manager,
        "queue": queue_manager,
        "user": user_manager,
        "youtube": mock_youtube,
        "streaming": mock_streaming,
        "playback": playback_controller,
        "history": history_manager,
    }


@pytest.fixture
def client(app_components):
    """Create test client with all components."""
    app = create_app(
        queue_manager=app_components["queue"],
        youtube_client=app_components["youtube"],
        playback_controller=app_components["playback"],
        config_manager=app_components["config"],
        user_manager=app_components["user"],
        history_manager=app_components["history"],
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
        response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "test123",
                "title": "Test Song",
                "duration_seconds": 180,
                "pitch_semitones": 0,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "added"
        assert "id" in data

    def test_add_song_user_not_found(self, client):
        """POST /api/queue - user not found returns 400."""
        response = client.post(
            "/api/queue",
            json={
                "user_id": "nonexistent-user",
                "youtube_video_id": "test123",
                "title": "Test Song",
            },
        )
        assert response.status_code == 400
        assert "User not found" in response.json()["detail"]

    def test_get_queue_with_songs(self, client, alice, bob):
        """GET /api/queue - queue with songs."""
        # Add songs
        client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "song1",
                "title": "Alice's Song",
            },
        )
        client.post(
            "/api/queue",
            json={
                "user_id": BOB_ID,
                "youtube_video_id": "song2",
                "title": "Bob's Song",
            },
        )

        response = client.get("/api/queue")
        assert response.status_code == 200
        data = response.json()
        assert len(data["queue"]) == 2

    def test_remove_song_as_owner(self, client, alice):
        """DELETE /api/queue/{id} - owner can remove their song."""
        # Add song
        add_response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "test123",
                "title": "Test Song",
            },
        )
        item_id = add_response.json()["id"]

        # Remove as owner
        response = client.delete(f"/api/queue/{item_id}?user_id={ALICE_ID}")
        assert response.status_code == 200
        assert response.json()["status"] == "removed"

    def test_remove_song_not_owner(self, client, alice, bob):
        """DELETE /api/queue/{id} - non-owner cannot remove song."""
        # Alice adds song
        add_response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "test123",
                "title": "Test Song",
            },
        )
        item_id = add_response.json()["id"]

        # Bob tries to remove
        response = client.delete(f"/api/queue/{item_id}?user_id={BOB_ID}")
        assert response.status_code == 403

    def test_remove_song_as_operator(self, client, alice):
        """DELETE /api/queue/{id} - operator can remove any song."""
        # Add song
        add_response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "test123",
                "title": "Test Song",
            },
        )
        item_id = add_response.json()["id"]

        # Auth as operator and remove
        set_operator(client)
        response = client.delete(f"/api/queue/{item_id}")
        assert response.status_code == 200

    def test_reorder_song_requires_operator(self, client, alice):
        """PATCH /api/queue/{id}/position - requires operator."""
        add_response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "test123",
                "title": "Test Song",
            },
        )
        item_id = add_response.json()["id"]

        response = client.patch(f"/api/queue/{item_id}/position", json={"new_position": 1})
        assert response.status_code == 403

    def test_update_queue_item_pitch(self, client, alice):
        """PATCH /api/queue/{id} - update pitch as owner."""
        add_response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "test123",
                "title": "Test Song",
                "pitch_semitones": 0,
            },
        )
        item_id = add_response.json()["id"]

        response = client.patch(
            f"/api/queue/{item_id}",
            json={"pitch_semitones": 3, "user_id": ALICE_ID},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "updated"

    def test_clear_queue_requires_operator(self, client, alice):
        """POST /api/queue/clear - requires operator."""
        client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "test123",
                "title": "Test Song",
            },
        )

        response = client.post("/api/queue/clear")
        assert response.status_code == 403

    def test_clear_queue_as_operator(self, client, alice):
        """POST /api/queue/clear - operator can clear queue."""
        client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "test123",
                "title": "Test Song",
            },
        )

        set_operator(client)
        response = client.post("/api/queue/clear")
        assert response.status_code == 200
        assert response.json()["status"] == "cleared"

    def test_get_song_settings(self, client, alice):
        """GET /api/queue/settings/{video_id} - get saved settings."""
        response = client.get(f"/api/queue/settings/test123?user_id={ALICE_ID}")
        assert response.status_code == 200
        # No history yet, so settings should be None
        assert response.json()["settings"] is None

    def test_play_next_requires_operator(self, client, alice):
        """POST /api/queue/{id}/play-next - requires operator."""
        add_response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "test123",
                "title": "Test Song",
            },
        )
        item_id = add_response.json()["id"]

        response = client.post(f"/api/queue/{item_id}/play-next")
        assert response.status_code == 403

    def test_move_to_end_requires_operator(self, client, alice):
        """POST /api/queue/{id}/move-to-end - requires operator."""
        add_response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "test123",
                "title": "Test Song",
            },
        )
        item_id = add_response.json()["id"]

        response = client.post(f"/api/queue/{item_id}/move-to-end")
        assert response.status_code == 403

    def test_bump_down_requires_operator(self, client, alice):
        """POST /api/queue/{id}/bump-down - requires operator."""
        add_response = client.post(
            "/api/queue",
            json={
                "user_id": ALICE_ID,
                "youtube_video_id": "test123",
                "title": "Test Song",
            },
        )
        item_id = add_response.json()["id"]

        response = client.post(f"/api/queue/{item_id}/bump-down")
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
# YouTube Endpoints - Smoke Tests
# =============================================================================


class TestYouTubeEndpoints:
    """Smoke tests for YouTube endpoints."""

    def test_search(self, client):
        """GET /api/youtube/search - search YouTube."""
        response = client.get("/api/youtube/search?q=test")
        assert response.status_code == 200
        assert "results" in response.json()

    def test_get_video_info(self, client):
        """GET /api/youtube/video/{id} - get video info."""
        response = client.get("/api/youtube/video/test123")
        assert response.status_code == 200
        data = response.json()
        assert "video_id" in data


# =============================================================================
# Config Endpoints - Smoke Tests
# =============================================================================


class TestConfigEndpoints:
    """Smoke tests for configuration endpoints."""

    def test_get_config(self, client):
        """GET /api/config - get configuration."""
        response = client.get("/api/config")
        assert response.status_code == 200
        data = response.json()
        assert "values" in data
        assert "editable_keys" in data

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
