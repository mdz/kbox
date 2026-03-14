"""
Tests for browser playback mode (no GStreamer).

Covers StreamingController graceful degradation, the content monitor no-op
behavior, and the /api/display/played endpoint.
"""

import os
import tempfile
from unittest.mock import MagicMock, Mock, create_autospec

import pytest

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.queue import QueueManager
from kbox.user import UserManager

# =========================================================================
# StreamingController without GStreamer
# =========================================================================


@pytest.fixture
def mock_config_manager():
    db = create_autospec(Database, instance=True)
    config = ConfigManager(db)
    config.set("rubberband_plugin", "")
    config.set("audio_output_device", None)
    return config


def test_streaming_controller_no_gstreamer(mock_config_manager, monkeypatch):
    """StreamingController sets _gst_missing=True and no-ops when GStreamer is absent."""
    import kbox.streaming as streaming_mod

    monkeypatch.setattr(streaming_mod, "_gst_available", None)
    monkeypatch.setattr(streaming_mod, "_Gst", None)

    def fake_get_gst():
        streaming_mod._gst_available = False
        return None

    monkeypatch.setattr(streaming_mod, "_get_gst", fake_get_gst)

    from kbox.streaming import StreamingController

    ctrl = StreamingController(mock_config_manager, None, use_fakesinks=True)

    assert ctrl._gst_missing is True
    assert ctrl.playbin is None
    assert ctrl.get_pipeline_state() == "null"
    assert ctrl.get_position() is None
    assert ctrl.seek(10) is False

    ctrl.load_file("/fake/path.mp4")
    ctrl.stop_playback()
    ctrl.pause()
    ctrl.resume()
    ctrl.stop()
    ctrl.reinitialize_pipeline()
    ctrl.set_pitch_shift(3)
    ctrl.show_notification("hello")
    ctrl.set_overlay_text("text")
    ctrl.update_qr_overlay("/fake/qr.png")
    ctrl.set_qr_visible(True)
    ctrl.display_image("/fake/image.png")

    def noop():
        pass

    ctrl.set_eos_callback(noop)
    assert ctrl.eos_callback is noop


# =========================================================================
# Content monitor no-op when no provider configured
# =========================================================================


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path=path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def user_manager(temp_db):
    return UserManager(temp_db)


def test_process_pending_content_noop_without_provider(temp_db, user_manager):
    """_process_pending_content returns immediately when no provider is set."""
    video_library = MagicMock()
    video_library.has_provider = False
    video_library.request.return_value = None

    qm = QueueManager(temp_db, video_library=video_library)
    try:
        user = user_manager.get_or_create_user("test-user", "Test")
        qm.add_song(
            user=user,
            video_id="youtube:abc123",
            title="Test Song",
            duration_seconds=120,
        )

        qm._process_pending_content()

        # request() should NOT have been called since there's no provider
        video_library.request.assert_not_called()
    finally:
        qm.stop_download_monitor()


# =========================================================================
# /api/display/played endpoint
# =========================================================================


def test_display_mark_played_endpoint(temp_db, user_manager):
    """POST /api/display/played/{item_id} marks the item as played."""
    from fastapi.testclient import TestClient

    from kbox.history import HistoryManager
    from kbox.playback import PlaybackController
    from kbox.video_library import VideoLibrary
    from kbox.web.server import create_app

    config = ConfigManager(temp_db)
    config.set("youtube_api_key", "test_key")
    config.set("cache_directory", tempfile.mkdtemp())
    config.set("operator_pin", "1234")

    video_library = MagicMock(spec=VideoLibrary)
    video_library.request.return_value = None
    video_library.get_path.return_value = None
    video_library.is_available.return_value = False
    video_library.is_source_configured.return_value = True

    queue_manager = QueueManager(temp_db, video_library=video_library)
    history_manager = HistoryManager(temp_db)

    mock_streaming = Mock()
    mock_streaming.set_eos_callback = Mock()
    mock_streaming.server = None
    mock_streaming.get_position = Mock(return_value=0)

    mock_playback = Mock(spec=PlaybackController)
    mock_playback.get_status.return_value = {
        "state": "idle",
        "current_song": None,
        "position_seconds": 0,
        "duration_seconds": 0,
    }

    try:
        app = create_app(
            queue_manager=queue_manager,
            video_library=video_library,
            playback_controller=mock_playback,
            config_manager=config,
            user_manager=user_manager,
            history_manager=history_manager,
            streaming_controller=mock_streaming,
        )
        client = TestClient(app)

        user = user_manager.get_or_create_user("singer-1", "Singer")
        item_id = queue_manager.add_song(
            user=user,
            video_id="youtube:xyz789",
            title="Karaoke Hit",
            duration_seconds=200,
        )

        # /display sets guest_authenticated, simulate that
        client.get("/display")
        response = client.post(f"/api/display/played/{item_id}")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        # Verify the item is now marked played
        item = queue_manager.get_item(item_id)
        assert item.played_at is not None

        # Non-existent item returns 404
        response = client.post("/api/display/played/99999")
        assert response.status_code == 404
    finally:
        queue_manager.stop_download_monitor()
