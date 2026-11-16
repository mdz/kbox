"""Tests for PitchLog: pitch choices recorded to user_events (#215)."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from kbox.database import Database
from kbox.models import QueueItem, SongMetadata, SongSettings
from kbox.pitch_log import PitchLog
from kbox.user_events import PHASE_PLAYING, PHASE_QUEUED

SINGER = "singer-1"


@pytest.fixture
def database():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def pitch_log(database):
    return PitchLog(database)


def _song(pitch_semitones=0):
    return QueueItem(
        id=7,
        position=1,
        user_id=SINGER,
        user_name="Singer",
        video_id="youtube:abc",
        metadata=SongMetadata(title="Song"),
        settings=SongSettings(pitch_semitones=pitch_semitones),
    )


def _events(database):
    conn = database.get_connection()
    try:
        rows = conn.execute("SELECT user_id, event_type, data_json FROM user_events").fetchall()
        return [(r["user_id"], r["event_type"], json.loads(r["data_json"])) for r in rows]
    finally:
        conn.close()


def test_song_started(pitch_log, database):
    pitch_log.song_started(_song(pitch_semitones=-2))

    assert _events(database) == [
        (
            SINGER,
            "pitch_start",
            {"queue_item_id": 7, "video_id": "youtube:abc", "semitones": -2},
        )
    ]


def test_mid_song_change_by_singer(pitch_log, database):
    pitch_log.pitch_changed(
        _song(pitch_semitones=-1),
        1,
        phase=PHASE_PLAYING,
        changed_by=SINGER,
        start_semitones=-2,
        position_seconds=42,
    )

    assert _events(database) == [
        (
            SINGER,
            "pitch_change",
            {
                "queue_item_id": 7,
                "video_id": "youtube:abc",
                "old_semitones": -1,
                "new_semitones": 1,
                "phase": "playing",
                "changed_by": SINGER,
                "changed_by_singer": True,
                "start_semitones": -2,
                "position_seconds": 42,
            },
        )
    ]


def test_change_by_someone_else_stays_under_singer(pitch_log, database):
    pitch_log.pitch_changed(_song(), 2, phase=PHASE_QUEUED, changed_by=None)

    [(user_id, _, data)] = _events(database)
    assert user_id == SINGER
    assert data["changed_by"] is None
    assert data["changed_by_singer"] is False
    assert data["start_semitones"] is None
    assert data["position_seconds"] is None


def test_unchanged_pitch_is_not_logged(pitch_log, database):
    pitch_log.pitch_changed(_song(pitch_semitones=2), 2, phase=PHASE_QUEUED, changed_by=SINGER)

    assert _events(database) == []


def test_write_failure_is_swallowed(pitch_log):
    with patch.object(pitch_log.repository, "record", side_effect=RuntimeError("locked")):
        pitch_log.song_started(_song())
        pitch_log.pitch_changed(_song(), 1, phase=PHASE_QUEUED, changed_by=SINGER)
