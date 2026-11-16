"""
Song vocal-range cues for choosing a pitch shift (issue #215, part 1).

Guests set a pitch shift when they add a song, but rarely know how high or
low the song sits, so the control mostly stays at 0. This module gives them
a short plain-language cue about the song, without knowing anything about
the singer:

* "Most voices can sing this in the original key"
* "Most voices will find it easier 3 steps down"
* "Wide range: has both high and low parts"

Where the numbers come from
---------------------------
**The song.** UltraStar note files from USDB (see :mod:`kbox.usdb`), which
covered about two thirds of the songs sung on kbox in the research on the
issue. The notes are summarised as a *comfort zone*: the duration-weighted
10th-90th percentile of note pitches. That was the most stable measure in
the research, and answers the singer's question better than the extremes.

**USDB's octave is unreliable** (UltraStar scoring ignores octave; three of
six files checked were an octave off), so every cue here is
octave-independent: only the zone's width and its position *within an
octave* are used. That matches how people sing anyway: a man singing a song
by a woman drops an octave without thinking about it.

**The population.** "Most voices" means the comfortable range for untrained
group singing that Greg Scheer gives in *The Art of Worship* (Baker Books,
2006): B-flat below middle C to the D an octave and a third above it
(B♭3-D5), sung at pitch by higher voices and an octave down by lower ones.
Because the two halves of that guidance are an octave apart, they give the
same answer once octaves are ignored, so there is one cue for everyone
rather than separate "lower voices" / "higher voices" lines. (Measured
speaking pitch suggests men and women differ by nearer 10 semitones than 12,
i.e. 2 semitones apart within the octave, but that is inside the "within a
semitone or two" accuracy the cues aim for, so splitting would add false
precision.)

Cues that would need the octave, such as "sits high: the original singer has
a high voice", are left out. They would need the octave anchored some other
way: a short pitch-tracking pass over the original recording, or the
original singer's voice type from the LLM metadata step.

Known limitation: transposed karaoke tracks
-------------------------------------------
Some karaoke tracks are in a different key from the original, and USDB
describes the original. The research found a chroma comparison of karaoke
vs. original audio that detects this in about a second, but it needs the
original recording downloaded, so it is not done here. For a transposed
track the cue is off by the transposition.
"""

from __future__ import annotations

import difflib
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence, Tuple

from .usdb import UsdbAuthError, UsdbClient, UsdbError, UsdbSong

if TYPE_CHECKING:
    from .config_manager import ConfigManager
    from .database import Database
    from .song_metadata import SongMetadataExtractor

# MIDI note numbers. UltraStar pitch 0 is middle C (MIDI 60), but only
# positions within the octave matter here.
MIDDLE_C = 60

# Scheer's comfortable congregational range, B♭3-D5 (see module docstring).
COMFORT_RANGE_LOW = 58
COMFORT_RANGE_HIGH = 74

# p10-p90 zones this wide or wider have notable high and low parts, whatever
# the key: the *middle 80%* of notes spans more than an octave, so the
# extremes reach well beyond that. Over 113 USDB files from the research on
# #215, the median zone was 9 semitones and 17% were 13 or more; at 12 it was
# 29%, too common for the cue to mean much. Stray notes entered in the wrong
# octave (harmless to UltraStar scoring) also inflate some zones.
WIDE_ZONE_SEMITONES = 13

# A note file with fewer notes than this is a stub or a broken upload.
MIN_NOTES = 30

# Room left at each end of the range when suggesting a shift (semitones).
SHIFT_MARGIN = 1

# Largest shift ever suggested. A shift of 6 either way lands on the same
# notes, and big shifts sound worse, so stop just short.
MAX_SHIFT = 5

# How long "USDB doesn't have this song" is trusted before asking again.
NOT_FOUND_RETRY_SECONDS = 30 * 24 * 3600

# After a network or login failure, leave USDB alone for this long. Guests
# just see no cue in the meantime.
FAILURE_BACKOFF_SECONDS = 300.0

# Words in a USDB title that mark a different arrangement of the song.
_VARIANT_WORDS = re.compile(r"\b(live|acoustic|remix|duet|version|unplugged|demo|edit)\b", re.I)


@dataclass(frozen=True)
class ComfortZone:
    """Duration-weighted 10th-90th percentile of a song's sung notes (MIDI)."""

    low: float
    high: float

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(frozen=True)
class Cue:
    """One line of advice. ``shift`` is set when the line suggests a shift."""

    text: str
    shift: Optional[int] = None

    def to_dict(self) -> dict:
        return {"text": self.text, "shift": self.shift}


# ---------------------------------------------------------------------------
# Note files and range maths
# ---------------------------------------------------------------------------


def parse_ultrastar_notes(txt: str) -> Optional[List[Tuple[int, int]]]:
    """
    Pull (pitch, duration) pairs out of an UltraStar note file.

    Only sung notes count: normal (``:``) and golden (``*``). Freestyle and
    rap notes have no real pitch. Returns None for files this can't
    summarise as one singer's line: duets (``P1``/``P2`` sections) and files
    with relative timing.
    """
    notes = []
    for raw in txt.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("#RELATIVE:") and line.split(":", 1)[1].strip().lower() in (
            "yes",
            "true",
            "1",
        ):
            return None
        if re.match(r"P\s*\d", line):
            return None
        if line[0] in ":*":
            parts = line[1:].split()
            if len(parts) < 3:
                continue
            try:
                duration, pitch = int(parts[1]), int(parts[2])
            except ValueError:
                continue
            notes.append((pitch + MIDDLE_C, max(duration, 1)))
    return notes


def comfort_zone(notes: Sequence[Tuple[int, int]]) -> Optional[ComfortZone]:
    """Duration-weighted 10th-90th percentile of note pitches, or None if too few notes."""
    if len(notes) < MIN_NOTES:
        return None
    ordered = sorted(notes)
    total = sum(d for _, d in ordered)

    def percentile(p: float) -> float:
        running = 0
        for pitch, duration in ordered:
            running += duration
            if running >= p * total:
                return float(pitch)
        return float(ordered[-1][0])

    return ComfortZone(low=percentile(0.1), high=percentile(0.9))


def _fits(low: float, high: float, range_low: float, range_high: float) -> bool:
    """True if [low, high] fits in [range_low, range_high] after moving it some whole octaves."""
    # Need an integer k with range_low <= low + 12k and high + 12k <= range_high.
    k_min = -((low - range_low) // 12)  # ceil((range_low - low) / 12)
    return low + 12 * k_min >= range_low and high + 12 * k_min <= range_high


def suggested_shift(
    zone: ComfortZone,
    range_low: float = COMFORT_RANGE_LOW,
    range_high: float = COMFORT_RANGE_HIGH,
) -> Optional[int]:
    """
    Pitch shift that puts the zone in the comfortable range, ignoring octaves.

    0 if the original key already fits (in some octave): no point nudging a
    guest off a key that works. Otherwise the smallest shift that fits the
    zone inside the range with ``SHIFT_MARGIN`` to spare at each end (or with
    none, if the zone is too wide for a margin). Smallest rather than the one
    that centres the zone exactly, because centring typically asks for 4-5
    steps where 1-2 do, and big shifts sound worse; the margin keeps a
    suggested shift from landing right on the edge of the range, since the
    zone is only good to a semitone or two. If shifting up and down by the
    same amount both fit, the one that centres the zone better wins, then
    down (untrained voices strain more at the top than the bottom).

    None if no shift of at most ``MAX_SHIFT`` makes it fit.
    """
    if zone.width > range_high - range_low:
        return None
    if _fits(zone.low, zone.high, range_low, range_high):
        return 0
    margin = SHIFT_MARGIN if zone.width <= range_high - range_low - 2 * SHIFT_MARGIN else 0
    lo, hi = range_low + margin, range_high - margin
    range_centre = (range_low + range_high) / 2
    zone_centre = (zone.low + zone.high) / 2

    def off_centre(shift: int) -> float:
        d = (zone_centre + shift - range_centre) % 12
        return min(d, 12 - d)

    for size in range(1, MAX_SHIFT + 1):
        fitting = [s for s in (-size, size) if _fits(zone.low + s, zone.high + s, lo, hi)]
        if fitting:
            return min(fitting, key=lambda s: (off_centre(s), s))
    return None


def _steps(n: int) -> str:
    return f"{n} step" if n == 1 else f"{n} steps"


def range_cues(zone: ComfortZone) -> List[Cue]:
    """Plain-language cues for a song's comfort zone."""
    cues = []
    shift = suggested_shift(zone)
    if shift == 0:
        cues.append(Cue("Most voices can sing this in the original key"))
    elif shift is not None:
        direction = "down" if shift < 0 else "up"
        cues.append(
            Cue(f"Most voices will find it easier {_steps(abs(shift))} {direction}", shift=shift)
        )
    if zone.width >= WIDE_ZONE_SEMITONES:
        cues.append(Cue("Wide range: has both high and low parts"))
    return cues


# ---------------------------------------------------------------------------
# Matching USDB search results to a song
# ---------------------------------------------------------------------------


def normalize(text: str, keep_brackets: bool = False) -> str:
    """Lower-case ASCII words only, for comparing artist/title strings."""
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\b(feat|ft|featuring)\b.*", " ", s)
    if not keep_brackets:
        s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"['`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"^the ", "", s)


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def pick_match(candidates: Sequence[UsdbSong], artist: str, title: str) -> Optional[UsdbSong]:
    """
    The USDB entry that is this song, or None if none clearly is.

    Title must match (allowing a near-miss spelling), artist must match or
    contain one another ("Queen" / "Queen & David Bowie"). Live, acoustic,
    duet etc. versions are skipped. Among the rest, the most viewed wins.
    """
    want_artist, want_title = normalize(artist), normalize(title)
    if not want_artist or not want_title:
        return None
    matches = []
    for song in candidates:
        if _VARIANT_WORDS.search(song.title) and not _VARIANT_WORDS.search(title):
            continue
        got_title, got_artist = normalize(song.title), normalize(song.artist)
        title_ok = got_title == want_title or _similar(got_title, want_title) >= 0.9
        artist_ok = (
            got_artist == want_artist
            or (got_artist and (got_artist in want_artist or want_artist in got_artist))
            or _similar(got_artist, want_artist) >= 0.85
        )
        if title_ok and artist_ok:
            matches.append(song)
    if not matches:
        return None
    return max(matches, key=lambda s: s.views)


def song_key(artist: str, title: str) -> str:
    """Cache key for a song, independent of which karaoke video it came from."""
    return f"{normalize(artist)}|{normalize(title)}"


# ---------------------------------------------------------------------------
# Service: metadata -> USDB -> cache -> cues
# ---------------------------------------------------------------------------


class SongRangeService:
    """
    Looks up a song's comfort zone and turns it into cues.

    Answers (including "not found") are cached per song in the
    ``song_range_cache`` table, keyed by normalised artist/title rather than
    by video, since many karaoke videos are the same song. Only the zone is
    stored; cues are computed on every request, so changing the cue rules
    never needs USDB again.
    """

    def __init__(
        self,
        database: "Database",
        config_manager: "ConfigManager",
        metadata_extractor: Optional["SongMetadataExtractor"],
        client_factory: Optional[Callable[[str, str], UsdbClient]] = None,
        now: Optional[Callable[[], float]] = None,
    ):
        self.logger = logging.getLogger(__name__)
        self.database = database
        self.config_manager = config_manager
        self.metadata_extractor = metadata_extractor
        self._client_factory = client_factory or UsdbClient
        self._now = now or time.time
        self._lock = threading.Lock()
        self._client: Optional[UsdbClient] = None
        self._client_credentials: Optional[Tuple[str, str]] = None
        self._backoff_until = 0.0
        # One lock per song so two guests opening the same song don't both
        # hit USDB; the second waits and then reads the cache.
        self._song_locks: Dict[str, threading.Lock] = {}

    def _credentials(self) -> Optional[Tuple[str, str]]:
        username = (self.config_manager.get("usdb_username") or "").strip()
        password = self.config_manager.get("usdb_password") or ""
        if not username or not password:
            return None
        return username, password

    def is_configured(self) -> bool:
        return self._credentials() is not None

    def cues_for_video(
        self, video_id: str, title: str, channel: Optional[str] = None
    ) -> Optional[dict]:
        """
        Cues for a karaoke video, or None if there is nothing to say.

        Nothing to say covers: USDB not configured, artist/title unknown,
        song not in USDB or not clearly matched, USDB unreachable. In every
        one of those cases the guest sees what they saw before this feature.
        """
        if not self.is_configured() or self.metadata_extractor is None:
            return None
        artist, song_name = self.metadata_extractor.extract(
            video_id=video_id, title=title, description=None, channel=channel
        )
        if not artist or not song_name:
            return None
        zone = self.zone_for_song(artist, song_name)
        if zone is None:
            return None
        cues = range_cues(zone)
        if not cues:
            return None
        return {"artist": artist, "song_name": song_name, "cues": [c.to_dict() for c in cues]}

    def zone_for_song(self, artist: str, title: str) -> Optional[ComfortZone]:
        """Comfort zone from the cache, looking it up in USDB if needed."""
        key = song_key(artist, title)
        if key.startswith("|") or key.endswith("|"):
            return None
        with self._lock:
            song_lock = self._song_locks.setdefault(key, threading.Lock())
        with song_lock:
            cached = self._get_cached(key)
            if cached is not None:
                _found, zone = cached
                return zone
            return self._look_up(key, artist, title)

    def _look_up(self, key: str, artist: str, title: str) -> Optional[ComfortZone]:
        if self._now() < self._backoff_until:
            return None
        client = self._get_client()
        if client is None:
            return None
        try:
            song = pick_match(client.search(artist, title), artist, title)
            zone = None
            if song is not None:
                notes = parse_ultrastar_notes(client.get_notes(song.song_id))
                zone = comfort_zone(notes) if notes is not None else None
        except UsdbAuthError:
            self.logger.warning("USDB login failed; check the USDB username/password in settings")
            self._backoff_until = self._now() + FAILURE_BACKOFF_SECONDS
            return None
        except UsdbError as e:
            self.logger.warning("USDB lookup failed for '%s' by '%s': %s", title, artist, e)
            self._backoff_until = self._now() + FAILURE_BACKOFF_SECONDS
            return None

        self._store(key, artist, title, song.song_id if song else None, zone)
        if zone is None:
            self.logger.info(
                "No usable USDB range for '%s' by '%s'%s",
                title,
                artist,
                "" if song is None else f" (USDB song {song.song_id} unusable)",
            )
        else:
            self.logger.info(
                "USDB range for '%s' by '%s': song %s, comfort zone %.0f-%.0f",
                title,
                artist,
                song.song_id if song else None,
                zone.low,
                zone.high,
            )
        return zone

    def _get_client(self) -> Optional[UsdbClient]:
        creds = self._credentials()
        if creds is None:
            return None
        with self._lock:
            if self._client is None or self._client_credentials != creds:
                self._client = self._client_factory(*creds)
                self._client_credentials = creds
            return self._client

    def _get_cached(self, key: str) -> Optional[Tuple[bool, Optional[ComfortZone]]]:
        """(found, zone) if there is a cache entry still worth trusting, else None."""
        conn = self.database.get_connection()
        try:
            row = conn.execute(
                """
                SELECT zone_low, zone_high, CAST(strftime('%s', looked_up_at) AS INTEGER) AS ts
                FROM song_range_cache WHERE song_key = ?
                """,
                (key,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        if row["zone_low"] is not None and row["zone_high"] is not None:
            return True, ComfortZone(low=row["zone_low"], high=row["zone_high"])
        if row["ts"] is not None and self._now() - row["ts"] < NOT_FOUND_RETRY_SECONDS:
            return False, None
        return None

    def _store(
        self,
        key: str,
        artist: str,
        title: str,
        usdb_song_id: Optional[int],
        zone: Optional[ComfortZone],
    ) -> None:
        conn = self.database.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO song_range_cache
                    (song_key, artist, song_name, usdb_song_id, zone_low, zone_high, looked_up_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime(?, 'unixepoch'))
                ON CONFLICT(song_key) DO UPDATE SET
                    artist = excluded.artist,
                    song_name = excluded.song_name,
                    usdb_song_id = excluded.usdb_song_id,
                    zone_low = excluded.zone_low,
                    zone_high = excluded.zone_high,
                    looked_up_at = excluded.looked_up_at
                """,
                (
                    key,
                    artist,
                    title,
                    usdb_song_id,
                    zone.low if zone else None,
                    zone.high if zone else None,
                    int(self._now()),
                ),
            )
            conn.commit()
        finally:
            conn.close()
