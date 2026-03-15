"""Tests for user event logging (search queries, etc.)."""

import json
import os
import tempfile

import pytest

from kbox.database import Database, EventRepository


@pytest.fixture
def database():
    """Create a test database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def event_repo(database):
    """Create an EventRepository with a test database."""
    return EventRepository(database)


def test_user_events_table_created(database):
    """Schema v4 migration creates the user_events table."""
    conn = database.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_events'")
        assert cursor.fetchone() is not None
    finally:
        conn.close()


def test_user_events_indexes_created(database):
    """Schema v4 migration creates indexes on user_events."""
    conn = database.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_user_events%'"
        )
        indexes = {row["name"] for row in cursor.fetchall()}
        assert "idx_user_events_user" in indexes
        assert "idx_user_events_type" in indexes
    finally:
        conn.close()


def test_record_event(event_repo):
    """Record a basic event and verify it's stored."""
    event_id = event_repo.record(
        "user-1", "search", {"query": "bohemian rhapsody", "result_count": 5}
    )
    assert event_id > 0


def test_record_event_stored_correctly(event_repo, database):
    """Verify event data is stored and retrievable."""
    event_repo.record("user-1", "search", {"query": "adele", "result_count": 8})

    conn = database.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_events WHERE user_id = 'user-1'")
        row = cursor.fetchone()
        assert row is not None
        assert row["event_type"] == "search"
        data = json.loads(row["data_json"])
        assert data["query"] == "adele"
        assert data["result_count"] == 8
        assert row["created_at"] is not None
    finally:
        conn.close()


def test_record_multiple_events(event_repo, database):
    """Multiple events for the same user are all stored."""
    event_repo.record("user-1", "search", {"query": "bohemian rhapsody"})
    event_repo.record("user-1", "search", {"query": "dont stop me now"})
    event_repo.record("user-1", "search", {"query": "somebody to love"})

    conn = database.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM user_events WHERE user_id = 'user-1'")
        assert cursor.fetchone()["count"] == 3
    finally:
        conn.close()


def test_record_event_no_data(event_repo, database):
    """Events can be recorded with no data payload."""
    event_id = event_repo.record("user-1", "search")
    assert event_id > 0

    conn = database.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT data_json FROM user_events WHERE id = ?", (event_id,))
        row = cursor.fetchone()
        assert row["data_json"] is None
    finally:
        conn.close()


def test_events_isolated_by_user(event_repo, database):
    """Events from different users don't interfere."""
    event_repo.record("user-1", "search", {"query": "adele"})
    event_repo.record("user-2", "search", {"query": "beyonce"})

    conn = database.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT json_extract(data_json, '$.query') as query "
            "FROM user_events WHERE user_id = 'user-1'"
        )
        assert cursor.fetchone()["query"] == "adele"

        cursor.execute(
            "SELECT json_extract(data_json, '$.query') as query "
            "FROM user_events WHERE user_id = 'user-2'"
        )
        assert cursor.fetchone()["query"] == "beyonce"
    finally:
        conn.close()


def test_schema_version_is_4(database):
    """Database schema version is 4 after migration."""
    conn = database.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT version FROM schema_version LIMIT 1")
        assert cursor.fetchone()["version"] == 4
    finally:
        conn.close()
