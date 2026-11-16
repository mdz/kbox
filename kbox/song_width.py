"""
How wide a song's vocal line is, next to the songs a singer has sung here
(issue #215).

The add-song dialog shows a small meter: one line with a dot for each song
this guest has sung before, and the chosen song highlighted on it, plus a
caption such as "This song spans about 1½ octaves". It helps a guest judge
for themselves whether a song is singable. kbox does not interpret it ("too
hard for you") and does not suggest a pitch shift.

Why width, not position
-----------------------
The notes come from USDB UltraStar files (see :mod:`kbox.usdb`), which cover
about two thirds of the songs sung on kbox. Their octave is unreliable
(UltraStar scoring ignores it), and kbox can't know which octave a singer
sang an earlier song in. How *spread out* the notes are doesn't depend on
either, and a pitch shift doesn't change it, so width is the one range fact
that can be compared across a singer's songs without guessing.

The measure
-----------
Width is the distance between the duration-weighted 5th and 95th
percentiles of the sung notes. #223 used 10th-90th, the most stable "comfort
zone" in the research on #215. Here the question is different: whether the
singer can *reach* the song, which is decided by its high and low points.
A chorus peak or a low verse line can be well under 10% of the sung time, so
10th-90th hides exactly the notes that matter. The true extremes go too far
the other way: a single ad-lib or mis-entered note sets them. 5th-95th keeps
held highs and lows and drops the stragglers. Weighting by duration means a
held note counts for more than a passing one.

USDB files sometimes have a few notes entered an octave off, which would
inflate the outer percentiles. Any note more than an octave from the
song's median is moved by whole octaves towards it before measuring. A real
vocal line rarely goes that far from its middle, so this costs at most a
little width on the widest songs. A semitone or two of error is fine for
this meter.
"""

from __future__ import annotations

import difflib
import logging
import math
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence, Tuple

from .usdb import UsdbAuthError, UsdbClient, UsdbError, UsdbSong

if TYPE_CHECKING:
    from .config_manager import ConfigManager
    from .database import Database
    from .history import HistoryManager

# A note file with fewer notes than this is a stub or a broken upload.
MIN_NOTES = 30

# Outer percentiles of the duration-weighted notes (see module docstring).
LOW_PERCENTILE = 0.05
HIGH_PERCENTILE = 0.95

# Notes further than this from the median (semitones) are folded towards it.
FOLD_DISTANCE = 12

# Show the singer's past songs only when there are at least this many with a
# known width; with fewer, a "spread" of dots says nothing.
MIN_PAST_SONGS = 3

# How many of the singer's most recent distinct songs to place on the meter.
MAX_PAST_SONGS = 15

# A play counts as "sung" once it got this far. Low enough to keep songs a
# singer gave up on part-way (they are part of the picture), high enough to
# drop tracks skipped in the first verse.
MIN_SUNG_COMPLETION_PERCENTAGE = 50.0

# How long "USDB doesn't have this song" is trusted before asking again.
NOT_FOUND_RETRY_SECONDS = 30 * 24 * 3600

# After a network or login failure, leave USDB alone for this long. Guests
# just see no meter in the meantime.
FAILURE_BACKOFF_SECONDS = 300.0

# Words in a USDB title that mark a different arrangement of the song.
_VARIANT_WORDS = re.compile(r"\b(live|acoustic|remix|duet|version|unplugged|demo|edit)\b", re.I)


@dataclass(frozen=True)
class VocalSpan:
    """Duration-weighted 5th-95th percentile of a song's sung notes (semitones)."""

    low: float
    high: float

    @property
    def width(self) -> float:
        return self.high - self.low


# ---------------------------------------------------------------------------
# Note files and width maths
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
            notes.append((pitch, max(duration, 1)))
    return notes


def _weighted_percentile(ordered: Sequence[Tuple[float, int]], total: int, p: float) -> float:
    """Pitch at fraction ``p`` of the total duration; ``ordered`` sorted by pitch."""
    running = 0
    for pitch, duration in ordered:
        running += duration
        if running >= p * total:
            return float(pitch)
    return float(ordered[-1][0])


def _fold(pitch: float, centre: float) -> float:
    """Move ``pitch`` by whole octaves until it is within FOLD_DISTANCE of ``centre``."""
    while pitch - centre > FOLD_DISTANCE:
        pitch -= 12
    while centre - pitch > FOLD_DISTANCE:
        pitch += 12
    return pitch


def vocal_span(notes: Sequence[Tuple[int, int]]) -> Optional[VocalSpan]:
    """Width-measuring span of a song's notes, or None if there are too few notes."""
    if len(notes) < MIN_NOTES:
        return None
    total = sum(d for _, d in notes)
    median = _weighted_percentile(sorted(notes), total, 0.5)
    folded = sorted((_fold(p, median), d) for p, d in notes)
    return VocalSpan(
        low=_weighted_percentile(folded, total, LOW_PERCENTILE),
        high=_weighted_percentile(folded, total, HIGH_PERCENTILE),
    )


def width_caption(semitones: float) -> str:
    """
    Plain description of a width, to the nearest half octave.

    No note names or interval names: "octave" is the one musical word most
    people know.
    """
    if semitones < 9:
        return "This song spans less than an octave"
    halves = math.floor(semitones / 6 + 0.5)
    whole, half = divmod(halves, 2)
    if halves == 2:
        amount = "an octave"
    else:
        number = f"{whole}½" if half else str(whole)
        amount = f"{number} octaves"
    return f"This song spans about {amount}"


# ---------------------------------------------------------------------------
# Working out which song a video is
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


def song_key(artist: str, title: str) -> str:
    """Cache key for a song, independent of which karaoke video it came from."""
    return f"{normalize(artist)}|{normalize(title)}"


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


# Words that make a title segment karaoke packaging rather than song or artist.
_PACKAGING = re.compile(
    r"\b(karaoke|instrumental|lyrics?|version|backing track|sing ?a?long|official|"
    r"video|hd|hq|with|without|vocals?|no|lead|karafun|sing king|zoom)\b",
    re.I,
)
_TRAILING_PACKAGING = re.compile(r"\s+(karaoke|instrumental|lyrics)(\s+version)?\s*$", re.I)
_STYLE_OF = re.compile(
    r"[(\[]\s*(?:in the style of|made famous by|as made famous by|originally by)\s+([^)\]]+)[)\]]",
    re.I,
)
_SEPARATORS = re.compile(r"\s+[-–—~]\s+|\s*[|•]\s*")


def title_candidates(title: str, channel: Optional[str] = None) -> List[Tuple[str, str]]:
    """
    Possible (artist, song) readings of a karaoke video title, most likely first.

    Karaoke channels put artist and song either way round ("Adele - Someone
    Like You (Karaoke Version)", "Someone Like You - Adele | Karaoke Version
    | KaraFun"), so both orders come back; the caller tries them against
    USDB, whose strict match (:func:`pick_match`) decides. Replaces #223's
    LLM call, which cost seconds (and a cold start of over ten) before the
    first answer.
    """
    if not title:
        return []
    style = _STYLE_OF.search(title)
    without_brackets = re.sub(r"\(.*?\)|\[.*?\]|\{.*?\}", " ", title)
    channel_norm = normalize(channel or "")
    segments = []
    for part in _SEPARATORS.split(without_brackets):
        part = _TRAILING_PACKAGING.sub("", part).strip(' -–—|•~:"')
        if not part:
            continue
        if not re.search(r"[^\W\d_]", _PACKAGING.sub(" ", part)):
            continue
        if channel_norm and normalize(part) == channel_norm:
            continue
        segments.append(part)
    if style and segments:
        return [(style.group(1).strip(), segments[0])]
    if len(segments) < 2:
        return []
    a, b = segments[0], segments[1]
    return [(a, b), (b, a)]


# ---------------------------------------------------------------------------
# Service: video -> USDB -> cache -> meter
# ---------------------------------------------------------------------------


class SongWidthService:
    """
    Looks up songs' widths and builds the add-song meter.

    Answers (including "not found") are cached per song in the
    ``song_range_cache`` table, keyed by normalised artist/title rather than
    by video, since many karaoke videos are the same song and a singer's past
    songs are known by artist/title.

    The chosen song is looked up while the request waits (the dialog asks
    after it has opened, so that never delays it). The singer's past songs
    come from the cache only; any not yet known are looked up in the
    background, one at a time, so they appear on a later visit.
    """

    def __init__(
        self,
        database: "Database",
        config_manager: "ConfigManager",
        history_manager: "HistoryManager",
        client_factory: Optional[Callable[[str, str], UsdbClient]] = None,
        now: Optional[Callable[[], float]] = None,
        submit: Optional[Callable[[Callable[[], None]], None]] = None,
    ):
        """
        Args:
            database: For the width cache and cached artist/title extraction.
            config_manager: Holds the USDB credentials.
            history_manager: The singer's past performances.
            client_factory: Builds a USDB client from (username, password);
                injectable so tests don't touch the network.
            now: Wall clock, injectable for tests.
            submit: Runs a background lookup; injectable so tests can run
                it inline. Defaults to a single worker thread.
        """
        self.logger = logging.getLogger(__name__)
        self.database = database
        self.config_manager = config_manager
        self.history_manager = history_manager
        self._client_factory = client_factory or UsdbClient
        self._now = now or time.time
        self._submit = submit or self._submit_to_worker
        self._executor: Optional[ThreadPoolExecutor] = None
        self._lock = threading.Lock()
        self._client: Optional[UsdbClient] = None
        self._client_credentials: Optional[Tuple[str, str]] = None
        self._backoff_until = 0.0
        # One lock per song so two lookups of the same song don't both hit
        # USDB; the second waits and then reads the cache.
        self._song_locks: Dict[str, threading.Lock] = {}
        self._pending_background: set = set()

    # -- configuration ------------------------------------------------------

    def _credentials(self) -> Optional[Tuple[str, str]]:
        username = (self.config_manager.get("usdb_username") or "").strip()
        password = self.config_manager.get("usdb_password") or ""
        if not username or not password:
            return None
        return username, password

    def is_configured(self) -> bool:
        return self._credentials() is not None

    # -- the meter ----------------------------------------------------------

    def meter_for_video(
        self, user_id: Optional[str], video_id: str, title: str, channel: Optional[str] = None
    ) -> Optional[dict]:
        """
        Data for the add-song meter, or None if there is nothing to show.

        Nothing to show covers: USDB not configured, artist/title unknown,
        song not in USDB or not clearly matched, USDB unreachable. In every
        one of those cases the guest sees what they saw before this feature.
        """
        if not self.is_configured():
            return None
        candidates = self._candidates_for_video(video_id, title, channel)
        found = self._span_for_candidates(candidates, look_up=True)
        if found is None:
            return None
        key, span = found
        past = self.past_widths(user_id, exclude_key=key) if user_id else []
        return {
            "width": round(span.width, 1),
            "caption": width_caption(span.width),
            "past": past if len(past) >= MIN_PAST_SONGS else [],
        }

    def past_widths(self, user_id: str, exclude_key: Optional[str] = None) -> List[float]:
        """
        Widths of the singer's recent distinct songs that are already known.

        Songs not yet looked up are queued for a background lookup.
        """
        widths = []
        seen = set()
        for record in self.history_manager.get_user_history(user_id, limit=50):
            completion = (record.performance or {}).get("completion_percentage") or 0
            if completion < MIN_SUNG_COMPLETION_PERCENTAGE:
                continue
            meta = record.metadata
            if meta.artist and meta.song_name:
                candidates = [(meta.artist, meta.song_name)]
            else:
                candidates = title_candidates(meta.title, meta.channel)
            keys = [song_key(a, t) for a, t in candidates]
            if not keys or any(k in seen for k in keys):
                continue
            seen.update(keys)
            if exclude_key in keys:
                continue
            if len(seen) > MAX_PAST_SONGS * 2:
                break
            found = self._span_for_candidates(candidates, look_up=False)
            if found is not None:
                widths.append(round(found[1].width, 1))
            elif not self._all_known_missing(keys):
                self._look_up_later(candidates)
        return widths[:MAX_PAST_SONGS]

    # -- candidates and lookups --------------------------------------------

    def _candidates_for_video(
        self, video_id: str, title: str, channel: Optional[str]
    ) -> List[Tuple[str, str]]:
        """(artist, song) readings: an earlier LLM extraction first, then the title."""
        candidates = []
        conn = self.database.get_connection()
        try:
            row = conn.execute(
                "SELECT artist, song_name FROM song_metadata_cache WHERE video_id = ?",
                (video_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None and row["artist"] and row["song_name"]:
            candidates.append((row["artist"], row["song_name"]))
        for c in title_candidates(title, channel):
            if song_key(*c) not in {song_key(*x) for x in candidates}:
                candidates.append(c)
        return candidates

    def _span_for_candidates(
        self, candidates: Sequence[Tuple[str, str]], look_up: bool
    ) -> Optional[Tuple[str, VocalSpan]]:
        """First candidate with a known span: from the cache, then (optionally) USDB."""
        for artist, title in candidates:
            cached = self._get_cached(song_key(artist, title))
            if cached is not None and cached[1] is not None:
                return song_key(artist, title), cached[1]
        if not look_up:
            return None
        for artist, title in candidates:
            span = self.span_for_song(artist, title)
            if span is not None:
                return song_key(artist, title), span
        return None

    def _all_known_missing(self, keys: Sequence[str]) -> bool:
        return all((c := self._get_cached(k)) is not None and c[1] is None for k in keys)

    def _look_up_later(self, candidates: Sequence[Tuple[str, str]]) -> None:
        marker = tuple(song_key(a, t) for a, t in candidates)
        with self._lock:
            if marker in self._pending_background:
                return
            self._pending_background.add(marker)

        def run() -> None:
            try:
                for artist, title in candidates:
                    if self.span_for_song(artist, title) is not None:
                        break
            except Exception as e:  # never let a background lookup kill the worker
                self.logger.warning("Background USDB lookup failed: %s", e)
            finally:
                with self._lock:
                    self._pending_background.discard(marker)

        self._submit(run)

    def _submit_to_worker(self, fn: Callable[[], None]) -> None:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="song-width")
            executor = self._executor
        executor.submit(fn)

    def span_for_song(self, artist: str, title: str) -> Optional[VocalSpan]:
        """Span from the cache, looking it up in USDB if needed."""
        key = song_key(artist, title)
        if key.startswith("|") or key.endswith("|"):
            return None
        with self._lock:
            song_lock = self._song_locks.setdefault(key, threading.Lock())
        with song_lock:
            cached = self._get_cached(key)
            if cached is not None:
                return cached[1]
            return self._look_up(key, artist, title)

    def _look_up(self, key: str, artist: str, title: str) -> Optional[VocalSpan]:
        if self._now() < self._backoff_until:
            return None
        client = self._get_client()
        if client is None:
            return None
        try:
            song = pick_match(client.search(artist, title), artist, title)
            span = None
            if song is not None:
                notes = parse_ultrastar_notes(client.get_notes(song.song_id))
                span = vocal_span(notes) if notes is not None else None
        except UsdbAuthError:
            self.logger.warning("USDB login failed; check the USDB username/password in settings")
            self._backoff_until = self._now() + FAILURE_BACKOFF_SECONDS
            return None
        except UsdbError as e:
            self.logger.warning("USDB lookup failed for '%s' by '%s': %s", title, artist, e)
            self._backoff_until = self._now() + FAILURE_BACKOFF_SECONDS
            return None

        self._store(key, artist, title, song.song_id if song else None, span)
        if span is None:
            self.logger.debug(
                "No usable USDB notes for '%s' by '%s'%s",
                title,
                artist,
                "" if song is None else f" (USDB song {song.song_id} unusable)",
            )
        else:
            self.logger.info(
                "USDB width for '%s' by '%s': song %s, %.0f semitones",
                title,
                artist,
                song.song_id if song else None,
                span.width,
            )
        return span

    def _get_client(self) -> Optional[UsdbClient]:
        creds = self._credentials()
        if creds is None:
            return None
        with self._lock:
            if self._client is None or self._client_credentials != creds:
                self._client = self._client_factory(*creds)
                self._client_credentials = creds
            return self._client

    # -- cache --------------------------------------------------------------

    def _get_cached(self, key: str) -> Optional[Tuple[bool, Optional[VocalSpan]]]:
        """(found, span) if there is a cache entry still worth trusting, else None."""
        conn = self.database.get_connection()
        try:
            row = conn.execute(
                """
                SELECT span_low, span_high, CAST(strftime('%s', looked_up_at) AS INTEGER) AS ts
                FROM song_range_cache WHERE song_key = ?
                """,
                (key,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        if row["span_low"] is not None and row["span_high"] is not None:
            return True, VocalSpan(low=row["span_low"], high=row["span_high"])
        if row["ts"] is not None and self._now() - row["ts"] < NOT_FOUND_RETRY_SECONDS:
            return False, None
        return None

    def _store(
        self,
        key: str,
        artist: str,
        title: str,
        usdb_song_id: Optional[int],
        span: Optional[VocalSpan],
    ) -> None:
        conn = self.database.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO song_range_cache
                    (song_key, artist, song_name, usdb_song_id, span_low, span_high, looked_up_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime(?, 'unixepoch'))
                ON CONFLICT(song_key) DO UPDATE SET
                    artist = excluded.artist,
                    song_name = excluded.song_name,
                    usdb_song_id = excluded.usdb_song_id,
                    span_low = excluded.span_low,
                    span_high = excluded.span_high,
                    looked_up_at = excluded.looked_up_at
                """,
                (
                    key,
                    artist,
                    title,
                    usdb_song_id,
                    span.low if span else None,
                    span.high if span else None,
                    int(self._now()),
                ),
            )
            conn.commit()
        finally:
            conn.close()
