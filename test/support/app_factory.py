"""
Shared test app factory for both API (TestClient) and E2E (Playwright) tests.

Build real DB + managers with mocked external dependencies (streaming,
playback, video_library, suggestion_engine). Mirrors the component wiring in
test_api.py's app_components fixture so the two suites can't drift apart.
"""

from unittest.mock import Mock

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.history import HistoryManager
from kbox.playback import PlaybackController, PlaybackState
from kbox.queue import QueueManager
from kbox.suggestions import SuggestionEngine, SuggestionError
from kbox.user import UserManager
from kbox.video_library import VideoLibrary
from kbox.web.server import create_app


def _mock_streaming():
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


def _mock_playback():
    playback = Mock(spec=PlaybackController)
    playback.get_status.return_value = {
        "state": PlaybackState.IDLE.value,
        "current_song": None,
        "position_seconds": 0,
        "duration_seconds": 0,
    }
    playback.get_cursor.return_value = None
    playback.move_to_next.return_value = True
    playback.move_to_end.return_value = True
    playback.move_down.return_value = True
    playback.move_up.return_value = True
    playback.play.return_value = False
    playback.pause.return_value = True
    playback.stop_playback.return_value = True
    playback.skip.return_value = True
    playback.previous.return_value = True
    playback.jump_to_song.return_value = True
    playback.restart.return_value = True
    playback.seek_relative.return_value = True
    playback.set_pitch.return_value = False
    playback.state = PlaybackState.IDLE
    playback.current_song_id = None
    return playback


def _mock_video_library():
    video_library = Mock(spec=VideoLibrary)
    video_library.search.return_value = [
        {"id": "youtube:test123", "title": "Test Song", "duration_seconds": 180}
    ]
    video_library.get_info.return_value = {
        "id": "youtube:test123",
        "title": "Test Song",
        "duration_seconds": 180,
        "thumbnail_url": "https://example.com/thumb.jpg",
        "channel": "Test Channel",
    }
    video_library.request.return_value = None
    video_library.get_path.return_value = None
    video_library.is_available.return_value = False
    video_library.is_source_configured.return_value = True
    video_library.manage_storage.return_value = 0
    return video_library


def _mock_suggestions():
    engine = Mock(spec=SuggestionEngine)
    engine.is_configured.return_value = False
    engine.get_suggestions.side_effect = SuggestionError("AI suggestions not configured")
    return engine


def build_test_app(
    db_path,
    cache_dir,
    *,
    access_token=None,
    video_library=None,
    playback=None,
    streaming=None,
    suggestion_engine=None,
):
    """Real DB + managers, mocked externals.

    Returns (app, db, queue) — the three objects the E2E fixture needs for
    teardown: app to serve, db to close, queue to stop the content monitor.

    video_library defaults to a mock with has_provider=False so the content
    monitor thread never touches SQLite — eliminates teardown races in E2E
    tests. Pass a custom mock with has_provider=True to test content-prep flows.
    """
    db = Database(db_path=db_path)
    config = ConfigManager(db)
    config.set("youtube_api_key", "test_key")
    config.set("cache_directory", cache_dir)
    config.set("operator_pin", "1234")
    config.set("transition_duration_seconds", "0")

    if video_library is None:
        video_library = _mock_video_library()
        video_library.has_provider = False

    if playback is None:
        playback = _mock_playback()
    if streaming is None:
        streaming = _mock_streaming()
    if suggestion_engine is None:
        suggestion_engine = _mock_suggestions()

    queue = QueueManager(db, video_library=video_library)
    app = create_app(
        queue_manager=queue,
        video_library=video_library,
        playback_controller=playback,
        config_manager=config,
        user_manager=UserManager(db),
        history_manager=HistoryManager(db),
        suggestion_engine=suggestion_engine,
        streaming_controller=streaming,
        access_token=access_token,
        session_secret="test-secret",
    )
    return app, db, queue
