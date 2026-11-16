"""
Tests for song vocal-range cues (kbox/song_range.py) and the USDB client
(kbox/usdb.py). USDB is never contacted: the client takes an injectable
POST function, and the service takes an injectable client factory.
"""

import os
import tempfile
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.song_range import (
    COMFORT_RANGE_HIGH,
    COMFORT_RANGE_LOW,
    NOT_FOUND_RETRY_SECONDS,
    ComfortZone,
    SongRangeService,
    comfort_zone,
    normalize,
    parse_ultrastar_notes,
    pick_match,
    range_cues,
    song_key,
    suggested_shift,
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


def zone(low, high):
    return ComfortZone(low=float(low), high=float(high))


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


# =============================================================================
# Note files and comfort zone
# =============================================================================


class TestParseNotes:
    def test_reads_normal_and_golden_notes_as_midi(self):
        txt = "#TITLE:x\n: 0 4 0 a\n* 8 2 -3 b\nF 12 4 7 free\nR 16 4 9 rap\n- 20\nE\n"
        assert parse_ultrastar_notes(txt) == [(60, 4), (57, 2)]

    def test_duet_files_are_refused(self):
        txt = "#TITLE:x\nP1\n: 0 4 0 a\nP2\n: 8 4 12 b\nE\n"
        assert parse_ultrastar_notes(txt) is None

    def test_relative_timing_files_are_refused(self):
        assert parse_ultrastar_notes("#RELATIVE:yes\n: 0 4 0 a\n") is None

    def test_malformed_note_lines_are_skipped(self):
        assert parse_ultrastar_notes(": 0 4 x a\n: 0\n: 0 4 2 ok\n") == [(62, 4)]


class TestComfortZone:
    def test_too_few_notes_is_no_zone(self):
        assert comfort_zone([(60, 4)] * 29) is None

    def test_percentiles_ignore_rare_extremes(self):
        # 10% of the time at the very bottom and top; the middle 80% is 60-67.
        notes = [(40, 1)] * 5 + [(60, 10)] * 10 + [(67, 10)] * 10 + [(90, 1)] * 5
        assert comfort_zone(notes) == zone(60, 67)

    def test_weighted_by_duration_not_count(self):
        # Many short high notes, a few long low ones: the long notes dominate.
        notes = [(72, 1)] * 40 + [(60, 100)] * 4
        z = comfort_zone(notes)
        assert z.low == 60
        assert z.high == 60

    def test_from_a_note_file(self):
        pitches = [0, 2, 4, 5, 7] * 10  # C4..G4
        z = comfort_zone(parse_ultrastar_notes(note_file(pitches)))
        assert z == zone(60, 67)


# =============================================================================
# Shift and cue logic
# =============================================================================


class TestSuggestedShift:
    def test_zone_inside_range_needs_no_shift(self):
        assert suggested_shift(zone(62, 70)) == 0

    @pytest.mark.parametrize("octaves", [-2, -1, 1])
    def test_octave_is_ignored(self, octaves):
        """USDB's octave is unreliable, so a zone an octave off gets the same answer."""
        assert suggested_shift(zone(62 + 12 * octaves, 70 + 12 * octaves)) == 0
        high = zone(66, 78)
        assert suggested_shift(zone(66 + 12 * octaves, 78 + 12 * octaves)) == (
            suggested_shift(high)
        )

    def test_shift_lands_inside_range_with_margin(self):
        # 12 wide, starting at G: doesn't fit B♭-D at any octave as is.
        z = zone(55, 67)
        shift = suggested_shift(z)
        assert shift not in (0, None)
        lo, hi = z.low + shift, z.high + shift
        # Some octave of the shifted zone sits inside the range, a semitone in.
        assert any(
            COMFORT_RANGE_LOW + 1 <= lo + 12 * k and hi + 12 * k <= COMFORT_RANGE_HIGH - 1
            for k in range(-3, 4)
        )

    def test_smallest_shift_that_fits_is_chosen(self):
        # 15 wide, one semitone over the top of B♭3-D5 (58-74) as is. Too
        # wide to leave a margin, so the smallest shift that fits: 1 down.
        assert suggested_shift(zone(60, 75)) == -1

    def test_shift_leaves_a_margin_when_there_is_room(self):
        # 14 wide, one over the top: 1 down would sit right on the edge.
        assert suggested_shift(zone(61, 75)) == -2

    def test_prefers_down_when_up_and_down_are_equally_good(self):
        # Centre 60 is a tritone from the range centre (66): 3 up and 3 down
        # both fit and centre it equally well.
        assert suggested_shift(zone(56, 64)) == -3

    def test_wider_than_range_has_no_shift(self):
        width = COMFORT_RANGE_HIGH - COMFORT_RANGE_LOW + 1
        assert suggested_shift(zone(50, 50 + width)) is None


class TestRangeCues:
    def test_original_key_fine(self):
        cues = range_cues(zone(62, 70))
        assert [c.text for c in cues] == ["Most voices can sing this in the original key"]
        assert cues[0].shift is None

    def test_shift_cue_is_plain_and_carries_the_number(self):
        z = zone(55, 67)
        shift = suggested_shift(z)
        cue = range_cues(z)[0]
        assert cue.shift == shift
        direction = "down" if shift < 0 else "up"
        assert cue.text == f"Most voices will find it easier {abs(shift)} steps {direction}"

    def test_one_step_is_singular(self):
        cue = range_cues(zone(60, 75))[0]
        assert cue.text == "Most voices will find it easier 1 step down"

    def test_wide_range_cue(self):
        texts = [c.text for c in range_cues(zone(58, 72))]
        assert "Wide range: has both high and low parts" in texts

    def test_too_wide_for_any_shift_only_warns(self):
        cues = range_cues(zone(50, 70))
        assert [c.text for c in cues] == ["Wide range: has both high and low parts"]

    def test_no_note_names_or_jargon(self):
        for low in range(48, 72):
            for width in (4, 9, 13):
                for cue in range_cues(zone(low, low + width)):
                    for word in ("semitone", "major", "minor", "fifth", "octave", "♭", "#"):
                        assert word not in cue.text.lower()


# =============================================================================
# Matching USDB results
# =============================================================================


def usdb_song(song_id, artist, title, views=0):
    return UsdbSong(song_id=song_id, artist=artist, title=title, views=views)


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
        cands = [usdb_song(1, "Gloria Estefan", "Dreams", 500)]
        assert pick_match(cands, "Fleetwood Mac", "Dreams") is None

    def test_artist_containment_matches(self):
        cands = [usdb_song(1, "Queen & David Bowie", "Under Pressure", 5)]
        assert pick_match(cands, "Queen", "Under Pressure").song_id == 1

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
        clock = FakeClock()
        post = ScriptedPost(["welcome", SEARCH_PAGE, "<textarea>x</textarea>"])
        client = make_client(post, clock=clock, sleeps=sleeps)
        client.search("a", "b")
        client.get_notes(1)
        # Instant fake responses: every request after the first waits the full gap.
        assert sleeps == [MIN_REQUEST_INTERVAL_SECONDS] * 2


# =============================================================================
# Service: metadata -> USDB -> cache -> cues
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


def fake_extractor(artist="Adele", song="Someone Like You"):
    extractor = MagicMock()
    extractor.extract.return_value = (artist, song)
    return extractor


class FakeUsdb:
    """Stands in for UsdbClient."""

    def __init__(self, results=None, notes="", error=None):
        self.results = results if results is not None else []
        self.notes = notes
        self.error = error
        self.searches = 0
        self.downloads = 0

    def search(self, artist, title):
        self.searches += 1
        if self.error:
            raise self.error
        return self.results

    def get_notes(self, song_id):
        self.downloads += 1
        return self.notes


IN_RANGE_NOTES = note_file([2, 4, 5, 7, 9] * 10)  # D4-A4: fits as is


def make_service(db, config, usdb, extractor=None, clock=None):
    return SongRangeService(
        db,
        config,
        extractor or fake_extractor(),
        client_factory=lambda u, p: usdb,
        now=clock or FakeClock(),
    )


class TestSongRangeService:
    def test_found_song_gives_cues(self, db, config):
        usdb = FakeUsdb([usdb_song(5, "Adele", "Someone Like You", 1)], IN_RANGE_NOTES)
        result = make_service(db, config, usdb).cues_for_video(
            "youtube:x", "Adele - Someone Like You (Karaoke)"
        )
        assert result["cues"] == [
            {"text": "Most voices can sing this in the original key", "shift": None}
        ]

    def test_result_is_cached_per_song_across_videos(self, db, config):
        usdb = FakeUsdb([usdb_song(5, "Adele", "Someone Like You", 1)], IN_RANGE_NOTES)
        service = make_service(db, config, usdb)
        service.cues_for_video("youtube:a", "t")
        service.cues_for_video("youtube:b", "t")  # another karaoke video, same song
        assert (usdb.searches, usdb.downloads) == (1, 1)

    def test_not_found_says_nothing_and_is_cached_for_a_while(self, db, config):
        clock = FakeClock()
        usdb = FakeUsdb([])
        service = make_service(db, config, usdb, clock=clock)
        assert service.cues_for_video("youtube:x", "t") is None
        assert service.cues_for_video("youtube:x", "t") is None
        assert usdb.searches == 1
        clock.advance(NOT_FOUND_RETRY_SECONDS + 1)
        service.cues_for_video("youtube:x", "t")
        assert usdb.searches == 2

    def test_doubtful_match_says_nothing(self, db, config):
        usdb = FakeUsdb([usdb_song(5, "Someone Else", "Someone Like You", 1)], IN_RANGE_NOTES)
        assert make_service(db, config, usdb).cues_for_video("youtube:x", "t") is None
        assert usdb.downloads == 0

    def test_duet_file_says_nothing(self, db, config):
        usdb = FakeUsdb([usdb_song(5, "Adele", "Someone Like You", 1)], "P1\n: 0 4 0 a\nP2\n")
        assert make_service(db, config, usdb).cues_for_video("youtube:x", "t") is None

    def test_failure_backs_off_and_is_not_cached(self, db, config):
        clock = FakeClock()
        usdb = FakeUsdb(error=UsdbError("down"))
        service = make_service(db, config, usdb, clock=clock)
        assert service.cues_for_video("youtube:x", "t") is None
        assert service.cues_for_video("youtube:x", "t") is None
        assert usdb.searches == 1  # backing off
        clock.advance(10_000)
        usdb.error = None
        usdb.results = [usdb_song(5, "Adele", "Someone Like You", 1)]
        usdb.notes = IN_RANGE_NOTES
        assert service.cues_for_video("youtube:x", "t") is not None

    def test_unconfigured_does_nothing(self, db):
        usdb = FakeUsdb()
        extractor = fake_extractor()
        service = make_service(db, ConfigManager(db), usdb, extractor=extractor)
        assert service.cues_for_video("youtube:x", "t") is None
        extractor.extract.assert_not_called()
        assert usdb.searches == 0

    def test_unknown_artist_title_does_nothing(self, db, config):
        usdb = FakeUsdb()
        service = make_service(db, config, usdb, extractor=fake_extractor(None, None))
        assert service.cues_for_video("youtube:x", "t") is None
        assert usdb.searches == 0


# =============================================================================
# Route
# =============================================================================


def route_client(service):
    app = FastAPI()
    app.include_router(search_routes.router)
    app.state.song_range_service = service
    return TestClient(app)


class TestSongRangeRoute:
    def test_returns_cues(self):
        service = MagicMock()
        service.cues_for_video.return_value = {"cues": [{"text": "x", "shift": -2}]}
        response = route_client(service).get(
            "/api/song-range", params={"video_id": "youtube:x", "title": "T", "channel": "C"}
        )
        assert response.status_code == 200
        assert response.json()["cues"] == [{"text": "x", "shift": -2}]
        service.cues_for_video.assert_called_once_with("youtube:x", "T", "C")

    def test_nothing_to_say_is_an_empty_list(self):
        service = MagicMock()
        service.cues_for_video.return_value = None
        response = route_client(service).get(
            "/api/song-range", params={"video_id": "v", "title": "T"}
        )
        assert response.json() == {"cues": []}

    def test_without_service_is_an_empty_list(self):
        response = route_client(None).get("/api/song-range", params={"video_id": "v", "title": "T"})
        assert response.json() == {"cues": []}
