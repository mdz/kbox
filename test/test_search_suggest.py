"""
Tests for search-box autocomplete.

The suggestion endpoint is undocumented and can change or vanish without
notice, so these tests fake the HTTP layer entirely: what matters is that
kbox behaves sanely around it, not that Google's response shape is what it
was today.  The clock is injected for the same reason timing is never slept
on here - expiry is exercised by moving a fake clock, not by waiting.
"""

import json

import pytest

from kbox.search_suggest import (
    BREAKER_COOLDOWN_SECONDS,
    CACHE_MAX_ENTRIES,
    CACHE_TTL_SECONDS,
    FAILURE_THRESHOLD,
    SearchSuggester,
)


class FakeClock:
    """A monotonic clock that only moves when a test says so."""

    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeConfig:
    """Minimal stand-in for ConfigManager."""

    def __init__(self, enabled=True):
        self.enabled = enabled

    def get_bool(self, key, default=False):
        assert key == "search_suggestions_enabled"
        return self.enabled


def suggest_response(query, suggestions):
    """Build a response body shaped like the real endpoint's."""
    return json.dumps([query, suggestions, [], {}])


class RecordingFetcher:
    """Fake HTTP fetcher that records calls and replays canned responses."""

    def __init__(self, body=None, error=None):
        self.body = body
        self.error = error
        self.calls = []

    def __call__(self, query):
        self.calls.append(query)
        if self.error is not None:
            raise self.error
        if callable(self.body):
            return self.body(query)
        return self.body


@pytest.fixture
def clock():
    return FakeClock()


def make_suggester(fetcher, clock, enabled=True):
    return SearchSuggester(FakeConfig(enabled=enabled), now=clock, fetch=fetcher)


class TestParsing:
    """Turning the endpoint's response into a list of strings."""

    def test_extracts_suggestions(self, clock):
        fetcher = RecordingFetcher(
            suggest_response("bohemian rha", ["bohemian rhapsody", "bohemian rhapsody piano"])
        )
        suggester = make_suggester(fetcher, clock)

        assert suggester.suggest("bohemian rha") == [
            "bohemian rhapsody",
            "bohemian rhapsody piano",
        ]

    def test_malformed_json_yields_nothing(self, clock):
        suggester = make_suggester(RecordingFetcher("not json at all"), clock)
        assert suggester.suggest("bohemian rha") == []

    def test_unexpected_shape_yields_nothing(self, clock):
        """A response that isn't [query, [suggestions]] is not an error."""
        suggester = make_suggester(RecordingFetcher(json.dumps({"suggestions": []})), clock)
        assert suggester.suggest("bohemian rha") == []

    def test_non_string_entries_are_dropped(self, clock):
        body = json.dumps(["q", ["real suggestion", 42, None, "  ", "another"], [], {}])
        suggester = make_suggester(RecordingFetcher(body), clock)

        assert suggester.suggest("q") == ["real suggestion", "another"]

    def test_max_results_caps_the_list(self, clock):
        body = suggest_response("q", [f"suggestion {i}" for i in range(20)])
        suggester = make_suggester(RecordingFetcher(body), clock)

        assert len(suggester.suggest("q", max_results=5)) == 5


class TestDeduplication:
    """Suggestions that would run the same search collapse to one row."""

    def test_karaoke_variant_is_dropped(self, clock):
        """The endpoint offers both forms; appending "karaoke" merges them."""
        fetcher = RecordingFetcher(
            suggest_response(
                "bohemian rha",
                ["bohemian rhapsody", "bohemian rhapsody karaoke", "bohemian rhapsody piano"],
            )
        )
        suggester = make_suggester(fetcher, clock)

        assert suggester.suggest("bohemian rha") == [
            "bohemian rhapsody",
            "bohemian rhapsody piano",
        ]

    def test_first_of_a_colliding_group_wins(self, clock):
        """Ordering is the endpoint's popularity ranking - preserve it."""
        fetcher = RecordingFetcher(
            suggest_response("x", ["song karaoke", "song", "other"]),
        )
        suggester = make_suggester(fetcher, clock)

        assert suggester.suggest("x") == ["song karaoke", "other"]

    def test_case_differences_collapse(self, clock):
        fetcher = RecordingFetcher(suggest_response("x", ["Song Name", "song name", "other"]))
        suggester = make_suggester(fetcher, clock)

        assert suggester.suggest("x") == ["Song Name", "other"]


class TestCaching:
    """Repeat prefixes shouldn't each cost an upstream request."""

    def test_second_call_is_served_from_cache(self, clock):
        fetcher = RecordingFetcher(suggest_response("abc", ["abc song"]))
        suggester = make_suggester(fetcher, clock)

        assert suggester.suggest("abc") == ["abc song"]
        assert suggester.suggest("abc") == ["abc song"]
        assert len(fetcher.calls) == 1

    def test_cache_key_ignores_case_and_padding(self, clock):
        fetcher = RecordingFetcher(suggest_response("abc", ["abc song"]))
        suggester = make_suggester(fetcher, clock)

        suggester.suggest("abc")
        suggester.suggest("  ABC  ")
        assert len(fetcher.calls) == 1

    def test_cache_expires(self, clock):
        fetcher = RecordingFetcher(suggest_response("abc", ["abc song"]))
        suggester = make_suggester(fetcher, clock)

        suggester.suggest("abc")
        clock.advance(CACHE_TTL_SECONDS + 1)
        suggester.suggest("abc")

        assert len(fetcher.calls) == 2

    def test_cache_is_bounded(self, clock):
        fetcher = RecordingFetcher(lambda q: suggest_response(q, [f"{q} song"]))
        suggester = make_suggester(fetcher, clock)

        for i in range(CACHE_MAX_ENTRIES + 50):
            suggester.suggest(f"query{i}")

        assert len(suggester._cache) <= CACHE_MAX_ENTRIES

    def test_failures_are_not_cached(self, clock):
        """A transient failure shouldn't poison a prefix for five minutes."""
        fetcher = RecordingFetcher(error=OSError("connection reset"))
        suggester = make_suggester(fetcher, clock)

        suggester.suggest("abc")
        suggester.suggest("abc")

        assert len(fetcher.calls) == 2


class TestFailureHandling:
    """Every failure mode degrades to "no suggestions"."""

    @pytest.mark.parametrize(
        "error",
        [
            OSError("connection refused"),
            TimeoutError("timed out"),
            ValueError("garbage"),
        ],
    )
    def test_errors_are_swallowed(self, clock, error):
        suggester = make_suggester(RecordingFetcher(error=error), clock)
        assert suggester.suggest("abc") == []

    def test_breaker_opens_after_repeated_failures(self, clock):
        fetcher = RecordingFetcher(error=OSError("down"))
        suggester = make_suggester(fetcher, clock)

        for _ in range(FAILURE_THRESHOLD):
            suggester.suggest("abc")
        assert len(fetcher.calls) == FAILURE_THRESHOLD

        # Further typing shouldn't reach the network at all.
        suggester.suggest("abcd")
        suggester.suggest("abcde")
        assert len(fetcher.calls) == FAILURE_THRESHOLD

    def test_breaker_closes_after_cooldown(self, clock):
        fetcher = RecordingFetcher(error=OSError("down"))
        suggester = make_suggester(fetcher, clock)

        for _ in range(FAILURE_THRESHOLD):
            suggester.suggest("abc")

        clock.advance(BREAKER_COOLDOWN_SECONDS + 1)
        suggester.suggest("abcd")

        assert len(fetcher.calls) == FAILURE_THRESHOLD + 1

    def test_success_resets_the_failure_count(self, clock):
        """Occasional failures shouldn't accumulate into an open breaker."""
        responses = {"good": suggest_response("good", ["good song"])}

        def body(query):
            if query in responses:
                return responses[query]
            raise OSError("down")

        suggester = make_suggester(RecordingFetcher(body), clock)

        for i in range(FAILURE_THRESHOLD * 3):
            suggester.suggest(f"bad{i}")
            suggester.suggest("good")
            # Expire the cached success so it's re-fetched next time round.
            clock.advance(CACHE_TTL_SECONDS + 1)

        # The breaker never opened, so the bad queries all reached the fetcher.
        assert suggester.suggest("good") == ["good song"]


class TestGating:
    """When not to ask at all."""

    def test_disabled_by_config(self, clock):
        fetcher = RecordingFetcher(suggest_response("abc", ["abc song"]))
        suggester = make_suggester(fetcher, clock, enabled=False)

        assert suggester.suggest("abc") == []
        assert fetcher.calls == []

    def test_blank_query(self, clock):
        fetcher = RecordingFetcher(suggest_response("", []))
        suggester = make_suggester(fetcher, clock)

        assert suggester.suggest("   ") == []
        assert fetcher.calls == []

    def test_query_is_normalized_before_fetching(self, clock):
        fetcher = RecordingFetcher(suggest_response("abc", ["abc song"]))
        suggester = make_suggester(fetcher, clock)

        suggester.suggest("  ABC  ")
        assert fetcher.calls == ["abc"]
