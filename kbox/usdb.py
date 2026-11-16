"""
Minimal client for USDB (usdb.animux.de), the UltraStar note-file database.

USDB has no API. Login, search and note download are HTML forms, and every
page except the front page needs a (free) account. This module does only
what the song-range cues need: search by artist/title and fetch one note
file. The form fields and page shapes follow ``bohning/usdb_syncer``'s
``usdb_scraper.py``.

Traffic is deliberately low: one request at a time, at least
``MIN_REQUEST_INTERVAL_SECONDS`` apart, and callers cache every answer (see
:mod:`kbox.song_range`), so a song is looked up at most once.
"""

from __future__ import annotations

import html
import http.client
import http.cookiejar
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional

BASE_URL = "https://usdb.animux.de/index.php"

REQUEST_TIMEOUT_SECONDS = 10.0

# USDB is a volunteer-run site. A lookup is at most three requests (login,
# search, note file), so a one-second gap costs a guest a couple of seconds
# on a song nobody has looked up before, and nothing afterwards.
MIN_REQUEST_INTERVAL_SECONDS = 1.0

USER_AGENT = "kbox (home karaoke; song range lookup)"

# Shown on every members-only page when the session has lapsed.
NOT_LOGGED_IN_MARKER = "You are not logged in"
LOGIN_FAILED_MARKER = "Login or Password invalid"

_ROW_START = re.compile(r'<tr class="list_tr\d"\s+data-songid="(\d+)"')
_CELL = re.compile(r'<td onclick="show_detail\(\d+\)"[^>]*>(.*?)</td>', re.S)
_TAG = re.compile(r"<[^>]+>")
_TEXTAREA = re.compile(r"<textarea[^>]*>(.*?)</textarea>", re.S)

# Columns of a search-result row, after the cover image cell.
_ARTIST_COL, _TITLE_COL, _VIEWS_COL = 1, 2, 10


class UsdbError(Exception):
    """USDB could not be reached, refused us, or sent a page we can't read."""


class UsdbAuthError(UsdbError):
    """The configured USDB username/password were rejected."""


@dataclass(frozen=True)
class UsdbSong:
    """One row of a USDB search result."""

    song_id: int
    artist: str
    title: str
    views: int


def parse_search_results(page: str) -> List[UsdbSong]:
    """Extract the song rows from a USDB search-result page."""
    songs = []
    starts = list(_ROW_START.finditer(page))
    for i, start in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(page)
        cells = [_TAG.sub("", c).strip() for c in _CELL.findall(page, start.end(), end)]
        if len(cells) <= _TITLE_COL:
            continue
        views_text = cells[_VIEWS_COL] if len(cells) > _VIEWS_COL else ""
        songs.append(
            UsdbSong(
                song_id=int(start.group(1)),
                artist=html.unescape(cells[_ARTIST_COL]),
                title=html.unescape(cells[_TITLE_COL]),
                views=int(views_text) if views_text.isdigit() else 0,
            )
        )
    return songs


def parse_notes_page(page: str) -> str:
    """Extract the UltraStar note file from a USDB ``gettxt`` page."""
    match = _TEXTAREA.search(page)
    if not match:
        raise UsdbError("note file not found on page")
    return html.unescape(match.group(1))


def search_keyword(text: str) -> str:
    """
    The single word to search USDB for.

    USDB's search mishandles apostrophes ("Don't", "Livin'"), so search on
    the longest word that contains none and match the rest locally.
    """
    words = re.findall(r"[^\W_]+", text)
    return max(words, key=len) if words else ""


class UsdbClient:
    """
    Logged-in session with USDB.

    Thread-safe: requests are serialised by a lock, which also enforces the
    minimum gap between them.
    """

    def __init__(
        self,
        username: str,
        password: str,
        post: Optional[Callable[[dict, dict], str]] = None,
        now: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ):
        """
        Args:
            username: USDB account name.
            password: USDB account password.
            post: Performs a form POST (query params, form data) and returns
                the page; injectable so tests don't touch the network.
            now: Monotonic clock, injectable for tests.
            sleep: Used to space requests out, injectable for tests.
        """
        self.logger = logging.getLogger(__name__)
        self._username = username
        self._password = password
        self._now = now or time.monotonic
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()
        self._last_request_at: Optional[float] = None
        self._logged_in = False
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self._opener.addheaders = [("User-Agent", USER_AGENT)]
        self._post = post or self._http_post

    def search(self, artist: str, title: str) -> List[UsdbSong]:
        """
        Search on one keyword each of artist and title, most-viewed first.

        The results are candidates only; the caller decides which (if any)
        is really the song.
        """
        form = {
            "interpret": search_keyword(artist),
            "title": search_keyword(title),
            "limit": "100",
            "details": "1",
            "order": "views",
            "ud": "desc",
            "start": "0",
        }
        return parse_search_results(self._members_page({"link": "list"}, form))

    def get_notes(self, song_id: int) -> str:
        """Download one song's UltraStar note file."""
        page = self._members_page({"link": "gettxt", "id": str(song_id)}, {"wd": "1"})
        return parse_notes_page(page)

    def _members_page(self, params: dict, form: dict) -> str:
        """Fetch a members-only page, logging in first or again as needed."""
        with self._lock:
            if not self._logged_in:
                self._login()
            page = self._request(params, form)
            if NOT_LOGGED_IN_MARKER in page:
                self.logger.debug("USDB session lapsed; logging in again")
                self._login()
                page = self._request(params, form)
                if NOT_LOGGED_IN_MARKER in page:
                    raise UsdbError("still not logged in after logging in")
            return page

    def _login(self) -> None:
        page = self._request({}, {"user": self._username, "pass": self._password, "login": "Login"})
        if LOGIN_FAILED_MARKER in page:
            self._logged_in = False
            raise UsdbAuthError("USDB rejected the configured username/password")
        self._logged_in = True
        self.logger.debug("Logged in to USDB")

    def _request(self, params: dict, form: dict) -> str:
        """One throttled POST. Caller holds the lock."""
        if self._last_request_at is not None:
            wait = MIN_REQUEST_INTERVAL_SECONDS - (self._now() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        try:
            return self._post(params, form)
        except (OSError, http.client.HTTPException) as e:
            # The exception text never carries the form data, so it's safe
            # to log; the credentials only ever travel in the POST body.
            raise UsdbError(f"request failed: {e}") from e
        finally:
            self._last_request_at = self._now()

    def _http_post(self, params: dict, form: dict) -> str:
        url = BASE_URL + ("?" + urllib.parse.urlencode(params) if params else "")
        data = urllib.parse.urlencode(form).encode()
        with self._opener.open(url, data, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", errors="replace")
