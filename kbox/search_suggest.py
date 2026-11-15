"""
Search-box autocomplete for kbox.

Design, measurements and open questions:
docs/development/search-autocomplete.md

Wraps Google's undocumented query-suggestion endpoint — the one that powers
YouTube's own search box — to offer completions while a guest types.

Why this and not the YouTube Data API: the Data API has no suggestion
endpoint at all, and its `search.list` method is capped at 100 calls per day
by default (its own quota bucket, separate from the 10,000-unit pool), which
a single party can exhaust.  Calling it per keystroke is not an option.  The
suggestion endpoint is uncounted, fast (~65ms), and its results are already
biased towards karaoke: YouTube's completions for "bohemian rha" include
"bohemian rhapsody karaoke", because that is what people actually search for.

The tradeoff is that the endpoint is undocumented and unsupported.  Google
can change or block it without notice, and no published rate limit exists.
Everything here is therefore built to fail quietly: an endpoint that is
unreachable, slow, or answering with something unrecognizable returns no
suggestions, which leaves the search box behaving exactly as it did before
this feature existed.  Autocomplete is a convenience, never a dependency.

That guard is deliberately narrow, covering the network call and parsing the
reply and nothing else.  A defect in this module is not an endpoint failure
and is not caught: swallowing it would hide the bug and spend the circuit
breaker on ourselves.

Requests are proxied through the server rather than made from the browser:
the endpoint sends no CORS headers, and this keeps guests' partial queries
off their own network connections.  That does mean every guest's typing
leaves from one IP, though the volume is small: ten guests adding four songs
each, at roughly six requests per query once debounced, is a few hundred
requests spread over an evening.  The cache and breaker below are cheap
insurance against pathological repeats, not load-shedding.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from .video_library import karaoke_search_query

if TYPE_CHECKING:
    from .config_manager import ConfigManager

# Google's suggestion endpoint.  client=firefox returns a plain JSON array;
# client=youtube returns the same data wrapped in a JSONP callback, which
# would need stripping.  ds=yt scopes suggestions to YouTube.
# Completions containing one of these are not what a karaoke search wants; see
# SearchSuggester._drop_off_target.
OFF_TARGET_PHRASES = (
    "lyrics",
    "lyric",
    "remix",
    "cover",
    "covers",
    "line dance",
    "dance",
    "tutorial",
    "chords",
    "reaction",
    "meaning",
    "ringtone",
    "official video",
    "music video",
)

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"

# Shorter than any human would wait for a dropdown.  A slow suggestion is
# worse than no suggestion: by the time it arrives the query has moved on.
REQUEST_TIMEOUT_SECONDS = 2.0

# How long a prefix's suggestions stay fresh.  This is not here to deduplicate
# across guests - at five to ten people, song choices barely overlap, and two
# of them typing the same full query in five minutes is rare.  What it absorbs
# is one guest backtracking: deleting a few characters and retyping them,
# correcting a typo, or searching the same thing twice.  Those prefixes repeat
# immediately, and suggestions for them don't change within a party.
CACHE_TTL_SECONDS = 300.0

# Bounded so a long party can't grow the cache without limit.  Entries are
# small (a prefix and a handful of strings).
CACHE_MAX_ENTRIES = 500

# Consecutive upstream failures before the breaker opens.  Low on purpose:
# if the endpoint has started refusing us, backing off immediately is both
# politer and faster than letting every keystroke time out.
FAILURE_THRESHOLD = 3

# How long the breaker stays open before a single request is allowed through
# to test the water.
BREAKER_COOLDOWN_SECONDS = 120.0


class SearchSuggester:
    """
    Fetches query completions, with caching and a circuit breaker.

    Thread-safe: FastAPI serves requests from a thread pool, and a party is
    several people typing at once.  The lock covers the cache and the breaker
    counters; concurrent callers asking for the same uncached prefix simply
    both fetch it, which costs one extra request and is not worth preventing.
    """

    def __init__(
        self,
        config_manager: "ConfigManager",
        now: Optional[Callable[[], float]] = None,
        fetch: Optional[Callable[[str], str]] = None,
    ):
        """
        Initialize the suggester.

        Args:
            config_manager: For the enable/disable toggle.
            now: Monotonic clock, injectable so tests can control expiry
                without sleeping.
            fetch: Performs the HTTP GET and returns the response body,
                injectable so tests don't touch the network.
        """
        self.logger = logging.getLogger(__name__)
        self.config_manager = config_manager
        self._now = now or time.monotonic
        self._fetch = fetch or self._http_get

        self._lock = threading.Lock()
        # prefix -> (expires_at, suggestions)
        self._cache: Dict[str, Tuple[float, List[str]]] = {}

        self._consecutive_failures = 0
        self._breaker_open_until = 0.0

    # =====================================================================
    # Public API
    # =====================================================================

    def is_enabled(self) -> bool:
        """True when the operator hasn't switched autocomplete off."""
        return self.config_manager.get_bool("search_suggestions_enabled", True)

    def suggest(self, query: str, max_results: int = 8) -> List[str]:
        """
        Get completions for a partial query.

        A call to Google that doesn't get through — unreachable, refused,
        timed out — yields an empty list, and the search box falls back to
        its pre-autocomplete behavior. Nothing else is swallowed: a reply we
        can't parse, or a bug in this module, propagates.

        Args:
            query: What the guest has typed so far.
            max_results: Maximum completions to return.

        Returns:
            Suggestion strings, deduplicated by the search they'd produce.
            Empty if autocomplete is off, the query is too short, the call
            failed, or the breaker is backed off.

        Raises:
            ValueError: if the endpoint's reply can't be parsed.
        """
        if not self.is_enabled():
            return []

        key = query.strip().lower()
        if not key:
            return []

        cached = self._get_cached(key)
        if cached is not None:
            return cached[:max_results]

        if self._breaker_is_open():
            self.logger.debug("Suggestion breaker open, skipping fetch for %r", key)
            return []

        suggestions = self._fetch_and_parse(key)
        if suggestions is None:
            return []

        # That None was the only swallowed failure in this module: the call
        # to Google did not get through.  Everything from here raises, as
        # does a reply _parse could not read.
        suggestions = self._drop_off_target(key, suggestions)
        suggestions = self._deduplicate(suggestions)
        self._store(key, suggestions)
        self._record_success()
        return suggestions[:max_results]

    # =====================================================================
    # Fetching
    # =====================================================================

    def _fetch_and_parse(self, key: str) -> Optional[List[str]]:
        """
        Ask the endpoint for one prefix.

        Returns:
            The suggestion strings, or None if the call to Google failed. An
            empty list is a real answer — the endpoint had nothing — and is
            cached as one.

        Raises:
            ValueError: if the reply cannot be parsed. Not swallowed; see the
                comment below.

        Transport failures are logged at warning rather than debug because a
        guest sees nothing at all when this happens: no dropdown is
        indistinguishable from having nothing to suggest, so the log is the
        only signal that the endpoint has gone away or started refusing us.
        It cannot flood: the breaker opens after FAILURE_THRESHOLD of these
        and stays shut for the cooldown.
        """
        try:
            raw = self._fetch(key)
        except (OSError, http.client.HTTPException) as e:
            # OSError covers the whole urllib family worth catching here:
            # URLError, HTTPError, socket timeouts, connection resets, DNS.
            self.logger.warning("Suggestion fetch failed for %r: %s", key, e)
            self._record_failure()
            return None

        # Parsing is deliberately NOT guarded. A reply we cannot read means
        # the undocumented format has changed under us — the feature is
        # broken, not merely unavailable, and the two want different
        # responses. Backing off and returning an empty list would leave it
        # dead and quiet; letting ValueError out puts a traceback naming the
        # offending body in the log, 500s the route, and the search box
        # already treats that as "no suggestions" for the guest.
        return self._parse(raw)

    def _http_get(self, query: str) -> str:
        """Perform the suggestion request and return the response body."""
        params = urllib.parse.urlencode(
            {
                "client": "firefox",
                "ds": "yt",
                "q": query,
            }
        )
        url = f"{SUGGEST_URL}?{params}"

        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")

    @staticmethod
    def _parse(raw: str) -> List[str]:
        """
        Pull the suggestion strings out of the response.

        The body is a JSON array shaped [query, [suggestions...], ...].

        Raises:
            ValueError: if the body isn't JSON, isn't that shape, or holds
                entries we can't read. The endpoint's format is not a
                contract, so a change here is expected breakage rather than a
                bug — but it is breakage, and it must be loud. Returning an
                empty list for any of these would be indistinguishable from
                "no suggestions for that prefix", which is how a feature dies
                silently.
        """
        data = json.loads(raw)

        if not isinstance(data, list) or len(data) < 2:
            raise ValueError(
                f"expected a JSON array of at least 2 elements, "
                f"got {type(data).__name__} {raw[:120]!r}"
            )

        suggestions = data[1]
        if not isinstance(suggestions, list):
            raise ValueError(
                f"expected suggestions at index 1, got {type(suggestions).__name__} {raw[:120]!r}"
            )

        usable = [s for s in suggestions if isinstance(s, str) and s.strip()]

        # One odd entry in an otherwise good list is not a format change, so
        # it is dropped quietly. Every entry being unreadable is a format
        # change wearing the costume of an empty result — the exact failure
        # that would otherwise look like "nothing to suggest" forever.
        if suggestions and not usable:
            raise ValueError(
                f"none of the {len(suggestions)} entries were usable strings: {raw[:120]!r}"
            )

        return usable

    @staticmethod
    def _drop_off_target(query: str, suggestions: List[str]) -> List[str]:
        """
        Drop completions that steer away from a karaoke track.

        "karaoke" is appended to every search, so "<song> lyrics", "<song>
        remix" or "<song> line dance" would just run the plain song search and
        lead to a lyric video, a remix or a dance tutorial rather than a
        singalong.  A modifier the guest typed themselves is left alone, so
        that someone who really does want "<song> acoustic karaoke" can still
        get completions.
        """
        typed = set(re.findall(r"[a-z0-9']+", query.lower()))
        kept = []
        for suggestion in suggestions:
            text = " ".join(re.findall(r"[a-z0-9']+", suggestion.lower()))
            padded = f" {text} "
            if any(
                f" {phrase} " in padded and not set(phrase.split()) <= typed
                for phrase in OFF_TARGET_PHRASES
            ):
                continue
            kept.append(suggestion)
        return kept

    @staticmethod
    def _deduplicate(suggestions: List[str]) -> List[str]:
        """
        Drop suggestions that would run the same search.

        The backend appends "karaoke" to every query, so "bohemian rhapsody"
        and "bohemian rhapsody karaoke" — both of which YouTube offers for
        "bohemian rha" — are the same search.  Showing both would waste the
        top of a dropdown that is mostly thumbed without reading.

        The first of each colliding group wins, preserving the endpoint's
        popularity ordering.
        """
        seen = set()
        unique = []
        for suggestion in suggestions:
            fingerprint = karaoke_search_query(suggestion).lower()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append(suggestion)
        return unique

    # =====================================================================
    # Cache
    # =====================================================================

    def _get_cached(self, key: str) -> Optional[List[str]]:
        """Return cached suggestions, or None if absent or stale."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, suggestions = entry
            if expires_at <= self._now():
                del self._cache[key]
                return None
            return list(suggestions)

    def _store(self, key: str, suggestions: List[str]) -> None:
        """Cache a prefix's suggestions, evicting if the cache is full."""
        with self._lock:
            if len(self._cache) >= CACHE_MAX_ENTRIES and key not in self._cache:
                self._evict_locked()
            self._cache[key] = (self._now() + CACHE_TTL_SECONDS, list(suggestions))

    def _evict_locked(self) -> None:
        """
        Make room in the cache.  Caller must hold the lock.

        Expired entries go first; if none are expired, the oldest insertion
        is dropped (dicts preserve insertion order).
        """
        now = self._now()
        expired = [k for k, (expires_at, _) in self._cache.items() if expires_at <= now]
        if expired:
            for k in expired:
                del self._cache[k]
            return

        oldest = next(iter(self._cache))
        del self._cache[oldest]

    # =====================================================================
    # Circuit breaker
    # =====================================================================

    def _breaker_is_open(self) -> bool:
        """True while we're backing off after repeated failures."""
        with self._lock:
            return self._now() < self._breaker_open_until

    def _record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._breaker_open_until = 0.0

    def _record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= FAILURE_THRESHOLD:
                self._breaker_open_until = self._now() + BREAKER_COOLDOWN_SECONDS
                # At least as loud as the individual failures above: giving up
                # on suggestions entirely for a while is the bigger event.
                self.logger.warning(
                    "Search suggestions unavailable after %d consecutive failures; "
                    "backing off for %.0fs",
                    self._consecutive_failures,
                    BREAKER_COOLDOWN_SECONDS,
                )
                self._consecutive_failures = 0
