"""
Tests for the database module.

Covers schema creation/migrations, repository CRUD operations,
and JSON encode/decode edge cases.
"""

import json
import os
import sqlite3
import tempfile

import pytest

from kbox.database import (
    ConfigRepository,
    Database,
    HistoryRepository,
    QueueRepository,
    UserRepository,
    _decode_metadata,
    _decode_settings,
    _encode_metadata,
    _encode_settings,
)
from kbox.models import SongMetadata, SongSettings, User


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
def user_repo(temp_db):
    return UserRepository(temp_db)


@pytest.fixture
def config_repo(temp_db):
    return ConfigRepository(temp_db)


@pytest.fixture
def history_repo(temp_db):
    return HistoryRepository(temp_db)


@pytest.fixture
def queue_repo(temp_db):
    return QueueRepository(temp_db)


# ============================================================================
# Schema and Migration Tests
# ============================================================================


class TestSchemaCreation:
    def test_fresh_db_has_current_version(self, temp_db):
        conn = temp_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT version FROM schema_version LIMIT 1")
            row = cursor.fetchone()
            assert row["version"] == Database.SCHEMA_VERSION
        finally:
            conn.close()

    def test_fresh_db_has_all_tables(self, temp_db):
        conn = temp_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = {row["name"] for row in cursor.fetchall()}
            assert "users" in tables
            assert "config" in tables
            assert "queue_items" in tables
            assert "playback_history" in tables
            assert "song_metadata_cache" in tables
            assert "user_events" in tables
            assert "schema_version" in tables
        finally:
            conn.close()

    def test_queue_items_has_video_id_column(self, temp_db):
        conn = temp_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(queue_items)")
            columns = {row["name"] for row in cursor.fetchall()}
            assert "video_id" in columns
            assert "source" not in columns
            assert "source_id" not in columns
        finally:
            conn.close()

    def test_context_manager(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with Database(db_path=path) as db:
                conn = db.get_connection()
                conn.close()
        finally:
            os.unlink(path)


class TestSchemaMigrationV1ToV2:
    """Test migration from old (source, source_id) schema to video_id."""

    def _create_v1_db(self, path):
        """Create a database with the v1 schema (source + source_id columns)."""
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY)
        """)
        cursor.execute("INSERT INTO schema_version (version) VALUES (1)")

        cursor.execute("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Old schema with source and source_id
        cursor.execute("""
            CREATE TABLE queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position INTEGER NOT NULL,
                download_status TEXT DEFAULT 'pending',
                played_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                song_metadata_json TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                download_json TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE playback_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                song_metadata_json TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                performance_json TEXT NOT NULL
            )
        """)

        # Insert test data
        cursor.execute("""
            INSERT INTO queue_items (position, user_id, user_name, source, source_id,
                                     song_metadata_json, settings_json)
            VALUES (1, 'user1', 'Alice', 'youtube', 'abc123',
                    '{"title": "Test Song"}', '{}')
        """)
        cursor.execute("""
            INSERT INTO playback_history (source, source_id, user_id, user_name,
                                          song_metadata_json, performance_json)
            VALUES ('youtube', 'xyz789', 'user1', 'Alice',
                    '{"title": "Old Song"}', '{}')
        """)

        conn.commit()
        conn.close()

    def test_migrates_queue_items(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        self._create_v1_db(path)

        try:
            db = Database(db_path=path)
            conn = db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT video_id FROM queue_items WHERE id = 1")
                row = cursor.fetchone()
                assert row["video_id"] == "youtube:abc123"
            finally:
                conn.close()
            db.close()
        finally:
            os.unlink(path)

    def test_migrates_playback_history(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        self._create_v1_db(path)

        try:
            db = Database(db_path=path)
            conn = db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT video_id FROM playback_history WHERE id = 1")
                row = cursor.fetchone()
                assert row["video_id"] == "youtube:xyz789"
            finally:
                conn.close()
            db.close()
        finally:
            os.unlink(path)

    def test_migration_creates_song_metadata_cache(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        self._create_v1_db(path)

        try:
            db = Database(db_path=path)
            conn = db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='song_metadata_cache'"
                )
                assert cursor.fetchone() is not None
            finally:
                conn.close()
            db.close()
        finally:
            os.unlink(path)

    def test_migration_updates_version_to_current(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        self._create_v1_db(path)

        try:
            db = Database(db_path=path)
            conn = db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT version FROM schema_version")
                assert cursor.fetchone()["version"] == Database.SCHEMA_VERSION
            finally:
                conn.close()
            db.close()
        finally:
            os.unlink(path)


class TestSchemaMigrationV2ToV3:
    """Test migration from v2 (video_id exists, no cache table) to v3."""

    def _create_v2_db(self, path):
        """Create a database with the v2 schema (has video_id, no cache)."""
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        cursor.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        cursor.execute("INSERT INTO schema_version (version) VALUES (2)")
        cursor.execute("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE config (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position INTEGER NOT NULL,
                download_status TEXT DEFAULT 'pending',
                played_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT NOT NULL, user_name TEXT NOT NULL,
                video_id TEXT NOT NULL,
                song_metadata_json TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                download_json TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE playback_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                user_id TEXT NOT NULL, user_name TEXT NOT NULL,
                performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                song_metadata_json TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                performance_json TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def test_adds_song_metadata_cache_table(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        self._create_v2_db(path)

        try:
            db = Database(db_path=path)
            conn = db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='song_metadata_cache'"
                )
                assert cursor.fetchone() is not None
            finally:
                conn.close()
            db.close()
        finally:
            os.unlink(path)

    def test_preserves_existing_data(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        self._create_v2_db(path)

        # Insert data before migration
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO queue_items (position, user_id, user_name, video_id, "
            "song_metadata_json) VALUES (1, 'u1', 'Alice', 'youtube:abc', '{\"title\":\"X\"}')"
        )
        conn.commit()
        conn.close()

        try:
            db = Database(db_path=path)
            repo = QueueRepository(db)
            items = repo.get_all()
            assert len(items) == 1
            assert items[0].video_id == "youtube:abc"
            db.close()
        finally:
            os.unlink(path)


# ============================================================================
# UserRepository Tests
# ============================================================================


class TestUserRepository:
    def test_create_and_get(self, user_repo):
        user = user_repo.create("user-1", "Alice")
        assert user.id == "user-1"
        assert user.display_name == "Alice"
        assert user.created_at is not None

        fetched = user_repo.get_by_id("user-1")
        assert fetched is not None
        assert fetched.display_name == "Alice"

    def test_get_nonexistent_returns_none(self, user_repo):
        assert user_repo.get_by_id("nonexistent") is None

    def test_update_display_name(self, user_repo):
        user_repo.create("user-1", "Alice")
        updated = user_repo.update_display_name("user-1", "Bob")
        assert updated is True

        fetched = user_repo.get_by_id("user-1")
        assert fetched.display_name == "Bob"

    def test_update_nonexistent_returns_false(self, user_repo):
        assert user_repo.update_display_name("ghost", "Nobody") is False

    def test_create_multiple_users(self, user_repo):
        user_repo.create("u1", "Alice")
        user_repo.create("u2", "Bob")
        assert user_repo.get_by_id("u1").display_name == "Alice"
        assert user_repo.get_by_id("u2").display_name == "Bob"


# ============================================================================
# ConfigRepository Tests
# ============================================================================


class TestConfigRepository:
    def test_set_and_get(self, config_repo):
        config_repo.set("key1", "value1")
        entry = config_repo.get("key1")
        assert entry is not None
        assert entry.key == "key1"
        assert entry.value == "value1"

    def test_get_nonexistent_returns_none(self, config_repo):
        assert config_repo.get("missing") is None

    def test_upsert_overwrites(self, config_repo):
        config_repo.set("key1", "v1")
        config_repo.set("key1", "v2")
        assert config_repo.get("key1").value == "v2"

    def test_get_all(self, config_repo):
        config_repo.set("a", "1")
        config_repo.set("b", "2")
        entries = config_repo.get_all()
        keys = {e.key for e in entries}
        assert "a" in keys
        assert "b" in keys

    def test_get_all_includes_updated_at(self, config_repo):
        config_repo.set("k", "v")
        entries = config_repo.get_all()
        assert len(entries) >= 1
        assert entries[0].updated_at is not None

    def test_initialize_defaults_inserts_missing(self, config_repo):
        config_repo.initialize_defaults({"x": "10", "y": "20"})
        assert config_repo.get("x").value == "10"
        assert config_repo.get("y").value == "20"

    def test_initialize_defaults_does_not_overwrite(self, config_repo):
        config_repo.set("x", "existing")
        config_repo.initialize_defaults({"x": "new", "y": "fresh"})
        assert config_repo.get("x").value == "existing"
        assert config_repo.get("y").value == "fresh"

    def test_initialize_defaults_converts_none_to_empty_string(self, config_repo):
        config_repo.initialize_defaults({"nullable": None})
        assert config_repo.get("nullable").value == ""


# ============================================================================
# HistoryRepository Tests
# ============================================================================


class TestSharedCodecs:
    """Test the module-level JSON encode/decode helpers."""

    def test_metadata_round_trip(self):
        meta = SongMetadata(
            title="Bohemian Rhapsody",
            duration_seconds=354,
            thumbnail_url="https://img.example.com/thumb.jpg",
            channel="Queen Official",
            artist="Queen",
            song_name="Bohemian Rhapsody",
        )
        encoded = _encode_metadata(meta)
        decoded = _decode_metadata(encoded)
        assert decoded.title == "Bohemian Rhapsody"
        assert decoded.duration_seconds == 354
        assert decoded.thumbnail_url == "https://img.example.com/thumb.jpg"
        assert decoded.channel == "Queen Official"
        assert decoded.artist == "Queen"
        assert decoded.song_name == "Bohemian Rhapsody"

    def test_metadata_round_trip_minimal(self):
        meta = SongMetadata(title="Untitled")
        encoded = _encode_metadata(meta)
        decoded = _decode_metadata(encoded)
        assert decoded.title == "Untitled"
        assert decoded.duration_seconds is None
        assert decoded.artist is None

    def test_decode_metadata_empty_string(self):
        assert _decode_metadata("").title == "Unknown"

    def test_decode_metadata_none(self):
        assert _decode_metadata(None).title == "Unknown"

    def test_decode_metadata_invalid_json(self):
        assert _decode_metadata("{bad json").title == "Unknown"

    def test_decode_metadata_missing_fields(self):
        result = _decode_metadata('{"channel": "foo"}')
        assert result.title == "Unknown"
        assert result.channel == "foo"

    def test_settings_round_trip(self):
        settings = SongSettings(pitch_semitones=-3)
        encoded = _encode_settings(settings)
        decoded = _decode_settings(encoded)
        assert decoded.pitch_semitones == -3

    def test_decode_settings_empty(self):
        assert _decode_settings("").pitch_semitones == 0

    def test_decode_settings_none(self):
        assert _decode_settings(None).pitch_semitones == 0

    def test_decode_settings_invalid_json(self):
        assert _decode_settings("not json").pitch_semitones == 0


class TestHistoryRepositoryEncodeDecode:
    """Test HistoryRepository-specific encode/decode helpers."""

    def test_performance_round_trip(self):
        perf = {"duration_played": 180, "completion_pct": 0.85}
        encoded = HistoryRepository._encode_performance(perf)
        decoded = HistoryRepository._decode_performance(encoded)
        assert decoded == perf

    def test_decode_performance_empty(self):
        assert HistoryRepository._decode_performance("") == {}

    def test_decode_performance_none(self):
        assert HistoryRepository._decode_performance(None) == {}

    def test_decode_performance_invalid_json(self):
        assert HistoryRepository._decode_performance("nope") == {}


class TestHistoryRepository:
    def test_record_and_get_user_history(self, history_repo):
        meta = SongMetadata(title="Test Song", artist="Artist")
        settings = SongSettings(pitch_semitones=2)
        perf = {"duration_played": 120}

        history_id = history_repo.record("u1", "Alice", "youtube:abc", meta, settings, perf)
        assert history_id > 0

        history = history_repo.get_user_history("u1")
        assert len(history) == 1
        assert history[0].video_id == "youtube:abc"
        assert history[0].user_name == "Alice"
        assert history[0].metadata.title == "Test Song"
        assert history[0].settings.pitch_semitones == 2
        assert history[0].performance["duration_played"] == 120

    def test_get_last_settings(self, history_repo):
        meta = SongMetadata(title="Song")
        history_repo.record("u1", "A", "vid:1", meta, SongSettings(pitch_semitones=-2), {})
        history_repo.record("u1", "A", "vid:1", meta, SongSettings(pitch_semitones=3), {})

        last = history_repo.get_last_settings("vid:1", "u1")
        assert last is not None
        assert last.pitch_semitones == 3

    def test_get_last_settings_per_user(self, history_repo):
        meta = SongMetadata(title="Song")
        history_repo.record("u1", "Alice", "vid:1", meta, SongSettings(pitch_semitones=1), {})
        history_repo.record("u2", "Bob", "vid:1", meta, SongSettings(pitch_semitones=5), {})

        assert history_repo.get_last_settings("vid:1", "u1").pitch_semitones == 1
        assert history_repo.get_last_settings("vid:1", "u2").pitch_semitones == 5

    def test_get_last_settings_no_history(self, history_repo):
        assert history_repo.get_last_settings("vid:none", "u1") is None

    def test_user_history_ordering(self, history_repo):
        meta = SongMetadata(title="Song")
        history_repo.record("u1", "A", "vid:1", meta, SongSettings(), {})
        history_repo.record("u1", "A", "vid:2", meta, SongSettings(), {})
        history_repo.record("u1", "A", "vid:3", meta, SongSettings(), {})

        history = history_repo.get_user_history("u1")
        assert len(history) == 3
        # Most recent first
        assert history[0].video_id == "vid:3"
        assert history[2].video_id == "vid:1"

    def test_user_history_limit(self, history_repo):
        meta = SongMetadata(title="Song")
        for i in range(5):
            history_repo.record("u1", "A", f"vid:{i}", meta, SongSettings(), {})

        history = history_repo.get_user_history("u1", limit=2)
        assert len(history) == 2

    def test_user_history_empty(self, history_repo):
        assert history_repo.get_user_history("nobody") == []


# ============================================================================
# QueueRepository Tests
# ============================================================================


def _make_user(uid="u1", name="Alice"):
    return User(id=uid, display_name=name)


def _make_meta(title="Test Song"):
    return SongMetadata(title=title, duration_seconds=200, channel="TestChannel")


class TestQueueRepositoryEncodeDecode:
    """Test QueueRepository-specific encode/decode helpers."""

    def test_decode_content_info_none(self):
        assert QueueRepository._decode_content_info(None) == {}

    def test_decode_content_info_empty(self):
        assert QueueRepository._decode_content_info("") == {}

    def test_decode_content_info_invalid(self):
        assert QueueRepository._decode_content_info("bad") == {}

    def test_decode_content_info_valid(self):
        data = json.dumps({"download_path": "/tmp/video.mp4"})
        result = QueueRepository._decode_content_info(data)
        assert result["download_path"] == "/tmp/video.mp4"

    def test_encode_content_info_round_trip(self):
        info = {"download_path": "/tmp/v.mp4", "error_message": "fail"}
        encoded = QueueRepository._encode_content_info(info)
        decoded = QueueRepository._decode_content_info(encoded)
        assert decoded == info


class TestQueueRepository:
    def test_add_and_get_all(self, queue_repo):
        item_id = queue_repo.add(_make_user(), "youtube:abc", _make_meta(), SongSettings())
        assert item_id > 0

        items = queue_repo.get_all()
        assert len(items) == 1
        assert items[0].video_id == "youtube:abc"
        assert items[0].position == 1
        assert items[0].content_status == "pending"

    def test_add_assigns_incrementing_positions(self, queue_repo):
        queue_repo.add(_make_user(), "vid:1", _make_meta("S1"), SongSettings())
        queue_repo.add(_make_user("u2", "Bob"), "vid:2", _make_meta("S2"), SongSettings())
        queue_repo.add(_make_user("u3", "Carol"), "vid:3", _make_meta("S3"), SongSettings())

        items = queue_repo.get_all()
        assert [i.position for i in items] == [1, 2, 3]

    def test_get_item(self, queue_repo):
        item_id = queue_repo.add(_make_user(), "vid:1", _make_meta(), SongSettings())
        item = queue_repo.get_item(item_id)
        assert item is not None
        assert item.id == item_id
        assert item.video_id == "vid:1"

    def test_get_item_nonexistent(self, queue_repo):
        assert queue_repo.get_item(9999) is None

    def test_remove_recompacts_positions(self, queue_repo):
        id1 = queue_repo.add(_make_user(), "vid:1", _make_meta("S1"), SongSettings())
        queue_repo.add(_make_user(), "vid:2", _make_meta("S2"), SongSettings())
        id3 = queue_repo.add(_make_user(), "vid:3", _make_meta("S3"), SongSettings())

        assert queue_repo.remove(id1) is True
        items = queue_repo.get_all()
        assert len(items) == 2
        assert [i.position for i in items] == [1, 2]
        assert items[1].id == id3

    def test_remove_nonexistent_returns_false(self, queue_repo):
        assert queue_repo.remove(9999) is False

    def test_update_status_ready(self, queue_repo):
        item_id = queue_repo.add(_make_user(), "vid:1", _make_meta(), SongSettings())
        result = queue_repo.update_status(
            item_id, QueueRepository.STATUS_READY, content_path="/tmp/video.mp4"
        )
        assert result is True

        item = queue_repo.get_item(item_id)
        assert item.content_status == "ready"
        assert item.content_path == "/tmp/video.mp4"

    def test_update_status_error(self, queue_repo):
        item_id = queue_repo.add(_make_user(), "vid:1", _make_meta(), SongSettings())
        queue_repo.update_status(
            item_id, QueueRepository.STATUS_ERROR, error_message="Download failed"
        )

        item = queue_repo.get_item(item_id)
        assert item.content_status == "error"
        assert item.error_message == "Download failed"

    def test_update_status_clears_error_on_non_error(self, queue_repo):
        item_id = queue_repo.add(_make_user(), "vid:1", _make_meta(), SongSettings())
        queue_repo.update_status(item_id, QueueRepository.STATUS_ERROR, error_message="Oops")
        queue_repo.update_status(item_id, QueueRepository.STATUS_READY, content_path="/tmp/v.mp4")

        item = queue_repo.get_item(item_id)
        assert item.error_message is None

    def test_update_status_nonexistent(self, queue_repo):
        assert queue_repo.update_status(9999, "ready") is False

    def test_reorder_move_down(self, queue_repo):
        queue_repo.add(_make_user(), "vid:1", _make_meta("A"), SongSettings())
        id2 = queue_repo.add(_make_user(), "vid:2", _make_meta("B"), SongSettings())
        queue_repo.add(_make_user(), "vid:3", _make_meta("C"), SongSettings())

        # Move item 2 (pos 2) to position 3 (down)
        # Expected: 1=A, 2=C, 3=B  (C shifts up)
        # Wait, let me reconsider: moving id2 from pos 2 to pos 3:
        # items at pos > 2 and <= 3 shift up by 1: C goes from 3 to 2
        # id2 goes to pos 3
        assert queue_repo.reorder(id2, 3) is True

        items = queue_repo.get_all()
        titles = [i.metadata.title for i in items]
        assert titles == ["A", "C", "B"]

    def test_reorder_move_up(self, queue_repo):
        queue_repo.add(_make_user(), "vid:1", _make_meta("A"), SongSettings())
        queue_repo.add(_make_user(), "vid:2", _make_meta("B"), SongSettings())
        id3 = queue_repo.add(_make_user(), "vid:3", _make_meta("C"), SongSettings())

        assert queue_repo.reorder(id3, 1) is True

        items = queue_repo.get_all()
        titles = [i.metadata.title for i in items]
        assert titles == ["C", "A", "B"]

    def test_reorder_same_position_is_noop(self, queue_repo):
        id1 = queue_repo.add(_make_user(), "vid:1", _make_meta("A"), SongSettings())
        assert queue_repo.reorder(id1, 1) is True

    def test_reorder_beyond_max_returns_false(self, queue_repo):
        id1 = queue_repo.add(_make_user(), "vid:1", _make_meta("A"), SongSettings())
        queue_repo.add(_make_user(), "vid:2", _make_meta("B"), SongSettings())
        assert queue_repo.reorder(id1, 5) is False

    def test_reorder_below_1_returns_false(self, queue_repo):
        id1 = queue_repo.add(_make_user(), "vid:1", _make_meta("A"), SongSettings())
        assert queue_repo.reorder(id1, 0) is False

    def test_reorder_nonexistent_returns_false(self, queue_repo):
        assert queue_repo.reorder(9999, 1) is False

    def test_update_pitch(self, queue_repo):
        item_id = queue_repo.add(
            _make_user(), "vid:1", _make_meta(), SongSettings(pitch_semitones=0)
        )
        assert queue_repo.update_pitch(item_id, 4) is True

        item = queue_repo.get_item(item_id)
        assert item.settings.pitch_semitones == 4

    def test_update_pitch_nonexistent(self, queue_repo):
        assert queue_repo.update_pitch(9999, 2) is False

    def test_update_extracted_metadata(self, queue_repo):
        item_id = queue_repo.add(
            _make_user(), "vid:1", _make_meta("Don't Stop Believin Karaoke"), SongSettings()
        )
        assert queue_repo.update_extracted_metadata(item_id, "Journey", "Don't Stop Believin'")

        item = queue_repo.get_item(item_id)
        assert item.metadata.artist == "Journey"
        assert item.metadata.song_name == "Don't Stop Believin'"
        # Original title preserved
        assert item.metadata.title == "Don't Stop Believin Karaoke"

    def test_update_extracted_metadata_nonexistent(self, queue_repo):
        assert queue_repo.update_extracted_metadata(9999, "X", "Y") is False

    def test_clear(self, queue_repo):
        queue_repo.add(_make_user(), "vid:1", _make_meta(), SongSettings())
        queue_repo.add(_make_user(), "vid:2", _make_meta(), SongSettings())

        count = queue_repo.clear()
        assert count == 2
        assert queue_repo.get_all() == []

    def test_clear_empty_queue(self, queue_repo):
        count = queue_repo.clear()
        assert count == 0

    def test_add_preserves_settings(self, queue_repo):
        item_id = queue_repo.add(
            _make_user(), "vid:1", _make_meta(), SongSettings(pitch_semitones=-3)
        )
        item = queue_repo.get_item(item_id)
        assert item.settings.pitch_semitones == -3

    def test_add_preserves_full_metadata(self, queue_repo):
        meta = SongMetadata(
            title="Full Song",
            duration_seconds=300,
            thumbnail_url="https://example.com/thumb.jpg",
            channel="TestCh",
            artist="TestArtist",
            song_name="TestSong",
        )
        item_id = queue_repo.add(_make_user(), "vid:1", meta, SongSettings())
        item = queue_repo.get_item(item_id)
        assert item.metadata.title == "Full Song"
        assert item.metadata.duration_seconds == 300
        assert item.metadata.thumbnail_url == "https://example.com/thumb.jpg"
        assert item.metadata.channel == "TestCh"
        assert item.metadata.artist == "TestArtist"
        assert item.metadata.song_name == "TestSong"
