"""
Tests for the add-song width meter (kbox/song_width.py) and the USDB client
(kbox/usdb.py). USDB is never contacted: the client takes an injectable
POST function, and the service takes an injectable client factory. Background
lookups run inline through an injected ``submit``.
"""

import os
import tempfile
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.models import HistoryRecord, SongMetadata, SongSettings
from kbox.song_width import (
    MIN_NOTES,
    NOT_FOUND_RETRY_SECONDS,
    SongWidthService,
    VocalSpan,
    normalize,
    parse_ultrastar_notes,
    pick_match,
    song_key,
    title_candidates,
    vocal_span,
    width_caption,
)
from kbox.usdb import (
    MIN_REQUEST_INTERVAL_SECONDS,
    UsdbAuthError,
    UsdbClient,
    UsdbError,
    UsdbSong,
    parse_notes_page,
    parse_search_results,
    search_keyword,
)
from kbox.web.routes import search as search_routes

# =============================================================================
# Helpers
# =============================================================================


def note_file(pitches, duration=4, header="#ARTIST:A\n#TITLE:T\n#BPM:300\n#GAP:0\n"):
    """An UltraStar file with one normal note per pitch (UltraStar pitch, 0 = middle C)."""
    lines = [header.rstrip("\n")]
    for i, p in enumerate(pitches):
        lines.append(f": {i * 8} {duration} {p} la ")
    lines.append("E")
    return "\n".join(lines)


def notes_spanning(semitones):
    """A note file whose 5th-95th percentile span is exactly ``semitones``."""
    return note_file([0] * 3 + [semitones // 2] * 44 + [semitones] * 3)


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def usdb_song(song_id, artist, title, views=0):
    return UsdbSong(song_id=song_id, artist=artist, title=title, views=views)


# =============================================================================
# Note files and width maths
# =============================================================================


class TestParseNotes:
    def test_reads_normal_and_golden_notes(self):
        txt = "#TITLE:x\n: 0 4 0 a\n* 8 2 -3 b\nF 12 4 7 free\nR 16 4 9 rap\n- 20\nE\n"
        assert parse_ultrastar_notes(txt) == [(0, 4), (-3, 2)]

    def test_duet_files_are_refused(self):
        txt = "#TITLE:x\nP1\n: 0 4 0 a\nP2\n: 8 4 12 b\nE\n"
        assert parse_ultrastar_notes(txt) is None

    def test_relative_timing_files_are_refused(self):
        assert parse_ultrastar_notes("#RELATIVE:yes\n: 0 4 0 a\n") is None

    def test_malformed_note_lines_are_skipped(self):
        assert parse_ultrastar_notes(": 0 4 x a\n: 0\n: 0 4 2 ok\n") == [(2, 4)]


class TestVocalSpan:
    def test_too_few_notes_is_no_span(self):
        assert vocal_span([(0, 4)] * (MIN_NOTES - 1)) is None

    def test_keeps_highs_and_lows_held_for_a_tenth_of_the_song(self):
        # A chorus peak held 8% of the time: a 10th-90th measure would miss
        # it, the 5th-95th one keeps it.
        notes = [(0, 8)] + [(5, 10)] * 8 + [(7, 10)] * 1 + [(12, 8)] + [(5, 1)] * 30
        span = vocal_span(notes)
        assert span.low == 0
        assert span.high == 12

    def test_drops_stray_extremes(self):
        # Under 5% of the sung time at either extreme doesn't count.
        notes = [(-5, 1)] * 2 + [(0, 10)] * 20 + [(9, 10)] * 20 + [(20, 1)] * 2
        assert vocal_span(notes) == VocalSpan(0, 9)

    def test_weighted_by_duration_not_count(self):
        # Many short high notes, a few long low ones: the long notes dominate.
        notes = [(12, 1)] * 24 + [(0, 100)] * 6
        assert vocal_span(notes).width == 0

    def test_notes_octaves_off_are_folded_back(self):
        # 20% of the notes entered two octaves up by mistake (5 -> 29). They
        # are folded to within an octave of the middle (7), i.e. to 17,
        # instead of adding 24 semitones of width.
        notes = [(0, 4)] * 20 + [(7, 4)] * 20 + [(5 + 24, 4)] * 10
        assert vocal_span(notes).width == 17

    def test_width_ignores_octave_and_transposition(self):
        base = parse_ultrastar_notes(note_file([0, 3, 5, 7, 10, 12] * 6))
        for shift in (-12, -3, 0, 5, 24):
            moved = [(p + shift, d) for p, d in base]
            assert vocal_span(moved).width == vocal_span(base).width == 12


class TestWidthCaption:
    @pytest.mark.parametrize(
        "semitones, caption",
        [
            (5, "This song spans less than an octave"),
            (8.9, "This song spans less than an octave"),
            (9, "This song spans about an octave"),
            (12, "This song spans about an octave"),
            (15, "This song spans about 1½ octaves"),
            (19, "This song spans about 1½ octaves"),
            (21, "This song spans about 2 octaves"),
            (27, "This song spans about 2½ octaves"),
        ],
    )
    def test_rounds_to_half_octaves(self, semitones, caption):
        assert width_caption(semitones) == caption

    def test_no_note_names_or_judgement(self):
        for w in range(0, 30):
            text = width_caption(w).lower()
            for word in ("hard", "easy", "you", "major", "minor", "fifth", "#"):
                assert word not in text


# =============================================================================
# Working out which song a video is
# =============================================================================


class TestTitleCandidates:
    def test_artist_first(self):
        assert title_candidates("Adele - Someone Like You (Karaoke Version)")[0] == (
            "Adele",
            "Someone Like You",
        )

    def test_both_orders_are_offered(self):
        cands = title_candidates("Someone Like You - Adele | Karaoke Version | KaraFun")
        assert cands == [("Someone Like You", "Adele"), ("Adele", "Someone Like You")]

    def test_trailing_karaoke_word_and_channel_are_dropped(self):
        cands = title_candidates(
            "Journey - Don't Stop Believin' Karaoke | Sing King", channel="Sing King"
        )
        assert ("Journey", "Don't Stop Believin'") in cands
        assert len(cands) == 2

    def test_in_the_style_of(self):
        assert title_candidates("Dreams (In the Style of Fleetwood Mac) [Karaoke Version]") == [
            ("Fleetwood Mac", "Dreams")
        ]

    def test_unparseable_title_gives_nothing(self):
        assert title_candidates("Karaoke night!!") == []
        assert title_candidates("") == []


class TestPickMatch:
    def test_apostrophes_and_case_dont_matter(self):
        cands = [usdb_song(1, "Journey", "Don’t Stop Believin’", 2190)]
        assert pick_match(cands, "Journey", "Don't Stop Believin'").song_id == 1

    def test_most_viewed_wins(self):
        cands = [
            usdb_song(1, "Adele", "Someone Like You", 10),
            usdb_song(2, "Adele", "Someone Like You", 99),
        ]
        assert pick_match(cands, "Adele", "Someone Like You").song_id == 2

    def test_live_version_is_skipped(self):
        cands = [
            usdb_song(1, "Adele", "Someone Like You (Live at Brit Awards 2011)", 999),
            usdb_song(2, "Adele", "Someone Like You", 10),
        ]
        assert pick_match(cands, "Adele", "Someone Like You").song_id == 2

    def test_other_artist_same_title_is_not_a_match(self):
        assert (
            pick_match([usdb_song(1, "Gloria Estefan", "Dreams", 500)], "Fleetwood Mac", "Dreams")
            is None
        )

    def test_swapped_artist_and_title_is_not_a_match(self):
        cands = [usdb_song(1, "Adele", "Someone Like You", 5)]
        assert pick_match(cands, "Someone Like You", "Adele") is None

    def test_the_prefix_and_featuring_are_ignored(self):
        assert normalize("The Doors") == "doors"
        assert normalize("Song (feat. Someone)") == "song"
        assert song_key("The Doors", "Light My Fire") == song_key("Doors", "light my fire!")


# =============================================================================
# USDB client
# =============================================================================

SEARCH_PAGE = """
<table><tr><td>header</td></tr>
<tr class="list_tr2" data-songid="14953" data-lastchange="1355436781" onmouseover="x"><td style="text-align: center"><audio></audio></td><td onclick="show_detail(14953)" style="text-align: center"><img src="data/cover/14953.jpg"></td><td onclick="show_detail(14953)">Adele</td>
<td onclick="show_detail(14953)"><a href="?link=detail&id=14953">Someone Like You</td>
<td onclick="show_detail(14953)">Pop</td>
<td onclick="show_detail(14953)">2011</td>
<td onclick="show_detail(14953)"></td>
<td onclick="show_detail(14953)">Yes</td>
<td onclick="show_detail(14953)">English</td>
<td onclick="show_detail(14953)">someone</td>
<td onclick="show_detail(14953)"><img src="images/star.png"></td>
<td onclick="show_detail(14953)">2316</td>
<td><a href="#"><img src="images/mini-zip.png"></a></td>
</tr><tr class="list_tr1" data-songid="7185" data-lastchange="1" onmouseover="x"><td></td><td onclick="show_detail(7185)"><img src="c.jpg"></td><td onclick="show_detail(7185)">Journey</td>
<td onclick="show_detail(7185)"><a href="?link=detail&id=7185">Don&rsquo;t Stop Believin&rsquo;</td>
<td onclick="show_detail(7185)">Rock</td><td onclick="show_detail(7185)">1981</td><td onclick="show_detail(7185)"></td><td onclick="show_detail(7185)">Yes</td><td onclick="show_detail(7185)">English</td><td onclick="show_detail(7185)">c</td><td onclick="show_detail(7185)"></td><td onclick="show_detail(7185)">2190</td>
</tr></table>
"""


class TestUsdbParsing:
    def test_search_results(self):
        assert parse_search_results(SEARCH_PAGE) == [
            UsdbSong(14953, "Adele", "Someone Like You", 2316),
            UsdbSong(7185, "Journey", "Don’t Stop Believin’", 2190),
        ]

    def test_notes_page(self):
        page = '<form><textarea name="txt" rows="20">#TITLE:Rock &amp; Roll\n: 0 4 0 a\n</textarea></form>'
        assert parse_notes_page(page) == "#TITLE:Rock & Roll\n: 0 4 0 a\n"

    def test_notes_page_without_textarea_is_an_error(self):
        with pytest.raises(UsdbError):
            parse_notes_page("<html>nope</html>")

    def test_search_keyword_avoids_apostrophes(self):
        assert search_keyword("Don't Stop Believin'") == "Believin"
        assert search_keyword("The Doors") == "Doors"
        assert search_keyword("") == ""


class ScriptedPost:
    """Records POSTs and answers from a list of pages."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def __call__(self, params, form):
        self.calls.append((dict(params), dict(form)))
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def make_client(post, clock=None, sleeps=None):
    clock = clock or FakeClock()
    sleeps = sleeps if sleeps is not None else []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.advance(seconds)

    return UsdbClient("user", "pass", post=post, now=clock, sleep=sleep)


class TestUsdbClient:
    def test_logs_in_once_then_searches(self):
        post = ScriptedPost(["welcome", SEARCH_PAGE, SEARCH_PAGE])
        client = make_client(post)
        client.search("Adele", "Someone Like You")
        client.search("Journey", "Don't Stop Believin'")
        assert [c[0] for c in post.calls] == [{}, {"link": "list"}, {"link": "list"}]
        assert post.calls[0][1]["login"] == "Login"
        assert post.calls[2][1]["title"] == "Believin"
        assert post.calls[2][1]["interpret"] == "Journey"

    def test_rejected_login(self):
        client = make_client(ScriptedPost(["Login or Password invalid"]))
        with pytest.raises(UsdbAuthError):
            client.search("a", "b")

    def test_lapsed_session_logs_in_again(self):
        post = ScriptedPost(["welcome", "You are not logged in", "welcome", SEARCH_PAGE])
        assert len(make_client(post).search("Adele", "Someone")) == 2
        assert len(post.calls) == 4

    def test_network_failure_is_a_usdb_error(self):
        client = make_client(ScriptedPost([OSError("connection refused")]))
        with pytest.raises(UsdbError):
            client.search("a", "b")

    def test_requests_are_spaced_out(self):
        sleeps = []
        post = ScriptedPost(["welcome", SEARCH_PAGE, "<textarea>x</textarea>"])
        client = make_client(post, sleeps=sleeps)
        client.search("a", "b")
        client.get_notes(1)
        # Instant fake responses: every request after the first waits the full gap.
        assert sleeps == [MIN_REQUEST_INTERVAL_SECONDS] * 2


# =============================================================================
# Service
# =============================================================================


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(db_path=path)
    yield database
    database.close()
    os.unlink(path)


@pytest.fixture
def config(db):
    cm = ConfigManager(db)
    cm.set("usdb_username", "user")
    cm.set("usdb_password", "pass")
    return cm


class FakeUsdb:
    """
    Stands in for UsdbClient. ``songs`` maps (artist, title) to a note file;
    search returns every known song whose artist and title share the
    searched keywords, the way USDB's one-keyword search does.
    """

    def __init__(self, songs=None, error=None):
        self.songs = dict(songs or {})
        self.error = error
        self.searches = []
        self.downloads = 0

    def search(self, artist, title):
        self.searches.append((artist, title))
        if self.error:
            raise self.error
        a_kw, t_kw = search_keyword(artist).lower(), search_keyword(title).lower()
        return [
            usdb_song(i, a, t, 1)
            for i, (a, t) in enumerate(self.songs)
            if a_kw in a.lower() and t_kw in t.lower()
        ]

    def get_notes(self, song_id):
        self.downloads += 1
        return list(self.songs.values())[song_id]


def history_record(artist, song, completion=100.0, title=None, video_id=None):
    return HistoryRecord(
        id=0,
        video_id=video_id or f"youtube:{song}",
        user_id="u1",
        user_name="Singer",
        metadata=SongMetadata(
            title=title or f"{artist} - {song} (Karaoke Version)",
            artist=artist,
            song_name=song,
        ),
        settings=SongSettings(),
        performance={"completion_percentage": completion},
        performed_at=None,
    )


def make_service(db, config, usdb, history=(), clock=None, submitted=None):
    history_manager = MagicMock()
    history_manager.get_user_history.return_value = list(history)

    def submit(fn):
        if submitted is not None:
            submitted.append(fn)
        else:
            fn()

    return SongWidthService(
        db,
        config,
        history_manager,
        client_factory=lambda u, p: usdb,
        now=clock or FakeClock(),
        submit=submit,
    )


ADELE = ("Adele", "Someone Like You")
ADELE_TITLE = "Adele - Someone Like You (Karaoke Version)"
PAST = {
    ("Journey", "Don't Stop Believin'"): notes_spanning(10),
    ("Fleetwood Mac", "Dreams"): notes_spanning(9),
    ("The Doors", "Light My Fire"): notes_spanning(14),
}
PAST_HISTORY = [history_record(a, t) for a, t in PAST]


class TestSongWidthService:
    def test_found_song_gives_width_and_caption(self, db, config):
        usdb = FakeUsdb({ADELE: notes_spanning(15)})
        result = make_service(db, config, usdb).meter_for_video("u1", "youtube:x", ADELE_TITLE)
        assert result == {
            "width": 15.0,
            "caption": "This song spans about 1½ octaves",
            "past": [],
        }

    def test_title_read_the_other_way_round(self, db, config):
        usdb = FakeUsdb({ADELE: notes_spanning(15)})
        result = make_service(db, config, usdb).meter_for_video(
            "u1", "youtube:x", "Someone Like You - Adele | Karaoke Version | KaraFun"
        )
        assert result["width"] == 15.0

    def test_earlier_llm_extraction_is_used_first(self, db, config):
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO song_metadata_cache (video_id, artist, song_name) VALUES (?, ?, ?)",
            ("youtube:x", "Adele", "Someone Like You"),
        )
        conn.commit()
        conn.close()
        usdb = FakeUsdb({ADELE: notes_spanning(15)})
        result = make_service(db, config, usdb).meter_for_video("u1", "youtube:x", "SLY karaoke")
        assert result["width"] == 15.0

    def test_result_is_cached_per_song_across_videos(self, db, config):
        usdb = FakeUsdb({ADELE: notes_spanning(15)})
        service = make_service(db, config, usdb)
        service.meter_for_video("u1", "youtube:a", ADELE_TITLE)
        service.meter_for_video("u1", "youtube:b", "Adele - Someone Like You | KaraFun")
        assert len(usdb.searches) == 1
        assert usdb.downloads == 1

    def test_not_found_shows_nothing_and_is_cached_for_a_while(self, db, config):
        usdb = FakeUsdb()
        clock = FakeClock()
        service = make_service(db, config, usdb, clock=clock)
        assert service.meter_for_video("u1", "youtube:x", ADELE_TITLE) is None
        assert len(usdb.searches) == 2  # both readings of the title
        service.meter_for_video("u1", "youtube:x", ADELE_TITLE)
        assert len(usdb.searches) == 2
        clock.advance(NOT_FOUND_RETRY_SECONDS + 1)
        service.meter_for_video("u1", "youtube:x", ADELE_TITLE)
        assert len(usdb.searches) == 4

    def test_duet_file_shows_nothing(self, db, config):
        usdb = FakeUsdb({ADELE: "P1\n" + notes_spanning(12)})
        assert make_service(db, config, usdb).meter_for_video("u1", "x", ADELE_TITLE) is None

    def test_failure_backs_off_and_is_not_cached(self, db, config):
        usdb = FakeUsdb(error=UsdbError("down"))
        clock = FakeClock()
        service = make_service(db, config, usdb, clock=clock)
        assert service.meter_for_video("u1", "x", ADELE_TITLE) is None
        assert service.meter_for_video("u1", "x", ADELE_TITLE) is None
        assert len(usdb.searches) == 1
        clock.advance(301)
        usdb.error = None
        usdb.songs = {ADELE: notes_spanning(15)}
        assert service.meter_for_video("u1", "x", ADELE_TITLE)["width"] == 15.0

    def test_unconfigured_does_nothing(self, db):
        usdb = FakeUsdb({ADELE: notes_spanning(15)})
        service = make_service(db, ConfigManager(db), usdb)
        assert service.meter_for_video("u1", "x", ADELE_TITLE) is None
        assert usdb.searches == []


class TestPastSongs:
    def test_past_songs_are_shown_once_known(self, db, config):
        usdb = FakeUsdb({ADELE: notes_spanning(15), **PAST})
        submitted = []
        service = make_service(db, config, usdb, history=PAST_HISTORY, submitted=submitted)

        # First visit: past songs aren't known yet, so they are queued for
        # the background and the meter shows only this song.
        assert service.meter_for_video("u1", "x", ADELE_TITLE)["past"] == []
        assert len(submitted) == 3
        for fn in submitted:
            fn()

        # Next visit: all three are known.
        result = service.meter_for_video("u1", "x", ADELE_TITLE)
        assert sorted(result["past"]) == [9.0, 10.0, 14.0]

    def test_fewer_than_three_known_shows_only_this_song(self, db, config):
        songs = dict(list(PAST.items())[:2])
        usdb = FakeUsdb({ADELE: notes_spanning(15), **songs})
        service = make_service(db, config, usdb, history=PAST_HISTORY)
        service.meter_for_video("u1", "x", ADELE_TITLE)  # fills inline
        result = service.meter_for_video("u1", "x", ADELE_TITLE)
        assert result["width"] == 15.0
        assert result["past"] == []

    def test_same_song_counts_once_and_this_song_is_left_out(self, db, config):
        usdb = FakeUsdb({ADELE: notes_spanning(15), **PAST})
        history = PAST_HISTORY + [PAST_HISTORY[0], history_record(*ADELE)]
        service = make_service(db, config, usdb, history=history)
        service.meter_for_video("u1", "x", ADELE_TITLE)
        assert sorted(service.meter_for_video("u1", "x", ADELE_TITLE)["past"]) == [9.0, 10.0, 14.0]

    def test_songs_skipped_early_dont_count(self, db, config):
        usdb = FakeUsdb({ADELE: notes_spanning(15), **PAST})
        history = PAST_HISTORY[:2] + [history_record("The Doors", "Light My Fire", completion=10)]
        service = make_service(db, config, usdb, history=history)
        service.meter_for_video("u1", "x", ADELE_TITLE)
        assert service.meter_for_video("u1", "x", ADELE_TITLE)["past"] == []

    def test_history_without_extracted_metadata_uses_the_title(self, db, config):
        usdb = FakeUsdb({ADELE: notes_spanning(15), **PAST})
        history = [
            HistoryRecord(
                id=0,
                video_id=f"youtube:{i}",
                user_id="u1",
                user_name="Singer",
                metadata=SongMetadata(title=f"{a} - {t} (Karaoke Version)"),
                settings=SongSettings(),
                performance={"completion_percentage": 100.0},
                performed_at=None,
            )
            for i, (a, t) in enumerate(PAST)
        ]
        service = make_service(db, config, usdb, history=history)
        service.meter_for_video("u1", "x", ADELE_TITLE)
        assert len(service.meter_for_video("u1", "x", ADELE_TITLE)["past"]) == 3

    def test_known_missing_past_songs_are_not_requeued(self, db, config):
        usdb = FakeUsdb({ADELE: notes_spanning(15)})  # past songs not in USDB
        service = make_service(db, config, usdb, history=PAST_HISTORY)
        service.meter_for_video("u1", "x", ADELE_TITLE)
        searches = len(usdb.searches)
        service.meter_for_video("u1", "x", ADELE_TITLE)
        assert len(usdb.searches) == searches

    def test_no_user_shows_only_this_song(self, db, config):
        usdb = FakeUsdb({ADELE: notes_spanning(15)})
        service = make_service(db, config, usdb, history=PAST_HISTORY)
        assert service.meter_for_video(None, "x", ADELE_TITLE)["past"] == []
        service.history_manager.get_user_history.assert_not_called()


# =============================================================================
# Route
# =============================================================================


def route_client(service, user_id=None):
    app = FastAPI()
    app.include_router(search_routes.router)
    app.state.song_width_service = service
    app.dependency_overrides[search_routes.get_current_user_id] = lambda: user_id
    return TestClient(app)


class TestSongWidthRoute:
    def test_returns_meter_for_the_session_user(self):
        service = MagicMock()
        service.meter_for_video.return_value = {"width": 12.0, "caption": "c", "past": []}
        response = route_client(service, user_id="u1").get(
            "/api/song-width", params={"video_id": "youtube:x", "title": "T", "channel": "C"}
        )
        assert response.status_code == 200
        assert response.json()["width"] == 12.0
        service.meter_for_video.assert_called_once_with("u1", "youtube:x", "T", "C")

    def test_nothing_to_show_is_an_empty_object(self):
        service = MagicMock()
        service.meter_for_video.return_value = None
        response = route_client(service, user_id="u1").get(
            "/api/song-width", params={"video_id": "v", "title": "T"}
        )
        assert response.json() == {}

    def test_without_service_is_an_empty_object(self):
        response = route_client(None).get("/api/song-width", params={"video_id": "v", "title": "T"})
        assert response.json() == {}
