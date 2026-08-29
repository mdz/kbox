"""Tests for SessionRepository, SessionManager, and the clear-queue hook."""

import os
import sqlite3
import tempfile
import time
from unittest.mock import MagicMock

import pytest

from kbox.database import (
    Database,
    HistoryRepository,
    QueueRepository,
    SessionRepository,
)
from kbox.history import HistoryManager
from kbox.models import SongMetadata, SongSettings, User
from kbox.queue import QueueManager
from kbox.session import SessionManager
from kbox.user import UserManager


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path=path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def session_repo(temp_db):
    return SessionRepository(temp_db)


@pytest.fixture
def mock_config():
    """Minimal ConfigManager stand-in with a `.get(key)` method."""
    mock = MagicMock()
    mock.get.return_value = None
    return mock


@pytest.fixture
def session_manager(temp_db, mock_config):
    return SessionManager(temp_db, config_manager=mock_config)


@pytest.fixture
def mock_video_library():
    mock = MagicMock()
    mock.request.return_value = None
    mock.get_path.return_value = None
    mock.has_provider = False
    mock.manage_storage.return_value = 0
    return mock


def _make_user(uid="u1", name="Alice"):
    return User(id=uid, display_name=name)


def _make_meta(title="Test Song"):
    return SongMetadata(title=title, duration_seconds=200)


# ============================================================================
# Schema
# ============================================================================


class TestSessionSchema:
    def test_sessions_table_exists(self, temp_db):
        conn = temp_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
            assert cursor.fetchone() is not None
        finally:
            conn.close()

    def test_sessions_columns(self, temp_db):
        conn = temp_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(sessions)")
            cols = {row["name"] for row in cursor.fetchall()}
            assert cols == {"id", "created_at", "ended_at", "theme"}
        finally:
            conn.close()

    def test_queue_items_has_session_id_column(self, temp_db):
        conn = temp_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(queue_items)")
            cols = {row["name"] for row in cursor.fetchall()}
            assert "session_id" in cols
        finally:
            conn.close()

    def test_playback_history_has_session_id_column(self, temp_db):
        conn = temp_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(playback_history)")
            cols = {row["name"] for row in cursor.fetchall()}
            assert "session_id" in cols
        finally:
            conn.close()

    def test_fresh_db_has_no_sessions(self, temp_db, session_repo):
        # No backfill on a fresh DB — there's nothing to attribute.
        assert session_repo.get_current() is None
        conn = temp_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS c FROM sessions")
            assert cursor.fetchone()["c"] == 0
        finally:
            conn.close()


# ============================================================================
# Migration from v4
# ============================================================================


class TestMigrationV4ToV5:
    """When an existing v4 DB with queue/history rows is upgraded, all
    existing rows must be backfilled to a single retroactive session."""

    def _create_v4_db(self, path):
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        cursor.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        cursor.execute("INSERT INTO schema_version (version) VALUES (4)")
        cursor.execute(
            "CREATE TABLE users (id TEXT PRIMARY KEY, display_name TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        cursor.execute(
            "CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        cursor.execute(
            """
            CREATE TABLE queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position INTEGER NOT NULL,
                download_status TEXT DEFAULT 'pending',
                played_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                video_id TEXT NOT NULL,
                song_metadata_json TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                download_json TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE playback_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                song_metadata_json TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                performance_json TEXT NOT NULL,
                theme TEXT
            )
            """
        )
        cursor.execute(
            "CREATE TABLE song_metadata_cache (video_id TEXT PRIMARY KEY, "
            "artist TEXT NOT NULL, song_name TEXT NOT NULL, "
            "extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        cursor.execute(
            "CREATE TABLE user_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id TEXT NOT NULL, event_type TEXT NOT NULL, data_json TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )

        # Seed some pre-existing queue and history rows with distinct timestamps.
        cursor.execute(
            """
            INSERT INTO queue_items (position, user_id, user_name, video_id,
                                     song_metadata_json, settings_json, created_at)
            VALUES (1, 'u1', 'Alice', 'youtube:abc',
                    '{"title": "Old Queue Song"}', '{}', '2025-01-10 10:00:00')
            """
        )
        cursor.execute(
            """
            INSERT INTO playback_history (video_id, user_id, user_name,
                                          song_metadata_json, settings_json,
                                          performance_json, performed_at)
            VALUES ('youtube:xyz', 'u1', 'Alice',
                    '{"title": "Old History Song"}', '{}', '{}',
                    '2025-01-05 09:00:00')
            """
        )

        conn.commit()
        conn.close()

    def test_migration_backfills_existing_rows(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        self._create_v4_db(path)

        try:
            db = Database(db_path=path)
            conn = db.get_connection()
            try:
                cursor = conn.cursor()

                # Exactly one retroactive session was created.
                cursor.execute("SELECT id, created_at, ended_at FROM sessions")
                sessions = cursor.fetchall()
                assert len(sessions) == 1
                retro = sessions[0]
                assert retro["ended_at"] is not None  # closed
                # created_at is the earliest pre-existing timestamp (history was earlier)
                assert str(retro["created_at"]).startswith("2025-01-05")

                retro_id = retro["id"]

                cursor.execute("SELECT session_id FROM queue_items")
                assert cursor.fetchone()["session_id"] == retro_id

                cursor.execute("SELECT session_id FROM playback_history")
                assert cursor.fetchone()["session_id"] == retro_id
            finally:
                conn.close()
            db.close()
        finally:
            os.unlink(path)


# ============================================================================
# SessionRepository
# ============================================================================


class TestSessionRepository:
    def test_create_returns_session(self, session_repo):
        s = session_repo.create(theme="80s night")
        assert s.id > 0
        assert s.theme == "80s night"
        assert s.ended_at is None
        assert s.created_at is not None

    def test_create_without_theme(self, session_repo):
        s = session_repo.create()
        assert s.theme is None

    def test_get_current_returns_most_recent_open(self, session_repo):
        s1 = session_repo.create()
        # Ensure ordering by created_at is deterministic
        time.sleep(0.01)
        s2 = session_repo.create()
        current = session_repo.get_current()
        assert current is not None
        assert current.id == s2.id

    def test_get_current_skips_closed(self, session_repo):
        s1 = session_repo.create()
        session_repo.end(s1.id)
        assert session_repo.get_current() is None

    def test_end_closes_session(self, session_repo):
        s = session_repo.create()
        assert session_repo.end(s.id) is True
        fetched = session_repo.get_by_id(s.id)
        assert fetched.ended_at is not None

    def test_end_is_idempotent(self, session_repo):
        s = session_repo.create()
        assert session_repo.end(s.id) is True
        # Second call no-ops (no open session matching the WHERE clause)
        assert session_repo.end(s.id) is False

    def test_get_by_id_missing(self, session_repo):
        assert session_repo.get_by_id(9999) is None


# ============================================================================
# SessionManager
# ============================================================================


class TestSessionManager:
    def test_get_or_create_current_creates_on_first_call(self, session_manager):
        s = session_manager.get_or_create_current()
        assert s.id > 0
        assert s.ended_at is None

    def test_get_or_create_current_returns_existing(self, session_manager):
        s1 = session_manager.get_or_create_current()
        s2 = session_manager.get_or_create_current()
        assert s1.id == s2.id

    def test_lazy_create_snapshots_theme(self, temp_db, mock_config):
        mock_config.get.return_value = "disco"
        mgr = SessionManager(temp_db, config_manager=mock_config)
        s = mgr.get_or_create_current()
        assert s.theme == "disco"

    def test_end_and_rotate_creates_new_session(self, session_manager):
        s1 = session_manager.get_or_create_current()
        s2 = session_manager.end_and_rotate()
        assert s2.id != s1.id
        # s1 is closed
        refetched = session_manager.repository.get_by_id(s1.id)
        assert refetched.ended_at is not None
        # s2 is current
        current = session_manager.get_current()
        assert current.id == s2.id

    def test_end_and_rotate_with_no_current_still_creates(self, session_manager):
        # Never-created current: rotate should just start a fresh one.
        s = session_manager.end_and_rotate()
        assert s.id > 0
        assert s.ended_at is None

    def test_end_and_rotate_snapshots_current_theme(self, temp_db, mock_config):
        mock_config.get.return_value = "80s"
        mgr = SessionManager(temp_db, config_manager=mock_config)
        mgr.get_or_create_current()

        mock_config.get.return_value = "90s"
        s2 = mgr.end_and_rotate()
        assert s2.theme == "90s"

    def test_works_without_config_manager(self, temp_db):
        mgr = SessionManager(temp_db, config_manager=None)
        s = mgr.get_or_create_current()
        assert s.theme is None

    def test_end_and_rotate_rotates_access_token(self, temp_db, mock_config):
        mgr = SessionManager(temp_db, config_manager=mock_config)
        mgr.get_or_create_current()
        mgr.end_and_rotate()

        token_calls = [c for c in mock_config.set.call_args_list if c.args[0] == "access_token"]
        assert len(token_calls) == 1
        new_token = token_calls[0].args[1]
        assert isinstance(new_token, str)
        assert len(new_token) > 10

    def test_end_and_rotate_calls_token_callback_with_new_token(self, temp_db, mock_config):
        received = []
        mgr = SessionManager(temp_db, config_manager=mock_config, on_token_rotated=received.append)
        mgr.end_and_rotate()
        assert len(received) == 1
        assert isinstance(received[0], str)

    def test_end_and_rotate_without_config_manager_skips_token_rotation(self, temp_db):
        received = []
        mgr = SessionManager(temp_db, config_manager=None, on_token_rotated=received.append)
        mgr.end_and_rotate()
        assert received == []

    def test_end_and_rotate_survives_callback_failure(self, temp_db, mock_config):
        def boom(token):
            raise RuntimeError("QR regeneration failed")

        mgr = SessionManager(temp_db, config_manager=mock_config, on_token_rotated=boom)
        s = mgr.end_and_rotate()
        assert s.id > 0


# ============================================================================
# Queue/History integration
# ============================================================================


class TestQueueSessionIntegration:
    def test_add_song_tags_session(self, temp_db, mock_video_library, session_manager):
        user_mgr = UserManager(temp_db)
        alice = user_mgr.get_or_create_user("u1", "Alice")

        qm = QueueManager(
            temp_db,
            video_library=mock_video_library,
            session_manager=session_manager,
        )
        qm.stop_content_monitor()
        item_id = qm.add_song(alice, "youtube:v1", "Song 1")

        item = qm.get_item(item_id)
        current = session_manager.get_current()
        assert current is not None
        assert item.session_id == current.id

    def test_add_song_without_session_manager_has_null_session(self, temp_db, mock_video_library):
        user_mgr = UserManager(temp_db)
        alice = user_mgr.get_or_create_user("u1", "Alice")

        qm = QueueManager(temp_db, video_library=mock_video_library)
        qm.stop_content_monitor()
        item_id = qm.add_song(alice, "youtube:v1", "Song 1")
        item = qm.get_item(item_id)
        assert item.session_id is None

    def test_clear_queue_rotates_session(self, temp_db, mock_video_library, session_manager):
        user_mgr = UserManager(temp_db)
        alice = user_mgr.get_or_create_user("u1", "Alice")

        qm = QueueManager(
            temp_db,
            video_library=mock_video_library,
            session_manager=session_manager,
        )
        qm.stop_content_monitor()

        first_id = qm.add_song(alice, "youtube:v1", "Song 1")
        first_session = qm.get_item(first_id).session_id

        removed = qm.clear_queue()
        assert removed == 1

        # Old session is now closed
        old = session_manager.repository.get_by_id(first_session)
        assert old.ended_at is not None

        # New song goes into a brand-new session
        second_id = qm.add_song(alice, "youtube:v2", "Song 2")
        second_session = qm.get_item(second_id).session_id
        assert second_session != first_session

    def test_clear_queue_new_session_snapshots_current_theme(
        self, temp_db, mock_video_library, mock_config
    ):
        user_mgr = UserManager(temp_db)
        alice = user_mgr.get_or_create_user("u1", "Alice")

        mock_config.get.return_value = "party one"
        mgr = SessionManager(temp_db, config_manager=mock_config)

        qm = QueueManager(temp_db, video_library=mock_video_library, session_manager=mgr)
        qm.stop_content_monitor()

        qm.add_song(alice, "youtube:v1", "Song 1")
        first_session = mgr.get_current()
        assert first_session.theme == "party one"

        # Operator changes the theme before rotating
        mock_config.get.return_value = "party two"
        qm.clear_queue()

        new_session = mgr.get_current()
        assert new_session.id != first_session.id
        assert new_session.theme == "party two"

    def test_clear_queue_without_session_manager_still_clears(self, temp_db, mock_video_library):
        user_mgr = UserManager(temp_db)
        alice = user_mgr.get_or_create_user("u1", "Alice")

        qm = QueueManager(temp_db, video_library=mock_video_library)
        qm.stop_content_monitor()
        qm.add_song(alice, "youtube:v1", "Song 1")

        assert qm.clear_queue() == 1
        assert qm.get_queue() == []


class TestHistorySessionIntegration:
    def test_record_performance_tags_session(self, temp_db, session_manager):
        hm = HistoryManager(temp_db, session_manager=session_manager)
        hm.record_performance(
            user_id="u1",
            user_name="Alice",
            video_id="youtube:v1",
            metadata=SongMetadata(title="Song"),
            settings=SongSettings(),
            played_duration_seconds=120,
            playback_end_position_seconds=120,
            completion_percentage=100.0,
        )

        records = hm.get_user_history("u1")
        current = session_manager.get_current()
        assert current is not None
        assert records[0].session_id == current.id

    def test_record_performance_without_session_manager(self, temp_db):
        hm = HistoryManager(temp_db)
        hm.record_performance(
            user_id="u1",
            user_name="Alice",
            video_id="youtube:v1",
            metadata=SongMetadata(title="Song"),
            settings=SongSettings(),
            played_duration_seconds=60,
            playback_end_position_seconds=60,
            completion_percentage=50.0,
        )
        records = hm.get_user_history("u1")
        assert records[0].session_id is None


# ============================================================================
# Direct repo tests for new session_id parameter
# ============================================================================


def test_queue_repo_add_accepts_session_id(temp_db):
    session_repo = SessionRepository(temp_db)
    s = session_repo.create(theme="test")

    queue_repo = QueueRepository(temp_db)
    item_id = queue_repo.add(_make_user(), "v:1", _make_meta(), SongSettings(), session_id=s.id)
    item = queue_repo.get_item(item_id)
    assert item.session_id == s.id


def test_history_repo_record_accepts_session_id(temp_db):
    session_repo = SessionRepository(temp_db)
    s = session_repo.create()

    history_repo = HistoryRepository(temp_db)
    history_repo.record("u1", "Alice", "v:1", _make_meta(), SongSettings(), {}, session_id=s.id)

    records = history_repo.get_user_history("u1")
    assert records[0].session_id == s.id
