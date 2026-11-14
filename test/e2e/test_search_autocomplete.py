"""
Search autocomplete: the dropdown, keyboard navigation, and graceful failure.

Google's suggestion endpoint is replaced at the lowest seam — the `fetch`
callable inside the real SearchSuggester, via the `suggest_fetcher` fixture.
Everything above it runs for real: the route, the minimum-length gate,
caching, karaoke normalization, deduplication, JSON encoding, and the
browser-side parsing of all of it. A change to the response shape anywhere in
that chain fails these tests, which is the point — intercepting
/api/search/suggest in the browser instead would mock kbox's own API and
leave the entire server path untested.

The browser IS the right seam for conditions the server cannot produce: a
request that never arrives, or one that fails after the server decided to
answer. Those two tests use page.route deliberately.
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

SUGGESTIONS = [
    "bohemian rhapsody",
    "bohemian rhapsody piano",
    "bohemian rhapsody lyrics",
]


def type_query(page, text="bohemian"):
    """Type into the search box so `input` events fire per character."""
    page.locator("#search-input").click()
    page.locator("#search-input").type(text, delay=10)


def count_suggest_requests(page):
    """Observe (without intercepting) requests to the suggestion endpoint."""
    seen = []
    page.on(
        "request",
        lambda r: seen.append(r.url) if "/api/search/suggest" in r.url else None,
    )
    return seen


def test_suggestions_appear_while_typing(mobile_page, init_user, suggest_fetcher):
    """Typing past the minimum length opens the dropdown."""
    suggest_fetcher.suggestions = SUGGESTIONS
    init_user("Alice")

    type_query(mobile_page)

    expect(mobile_page.locator("#search-suggestions")).to_be_visible()
    expect(mobile_page.locator(".search-suggestion")).to_have_count(len(SUGGESTIONS))
    expect(mobile_page.locator(".search-suggestion").first).to_have_text("bohemian rhapsody")


def test_short_queries_are_never_requested(mobile_page, init_user, suggest_fetcher):
    """Below the minimum the client must not ask at all.

    Asserting on an absent dropdown would prove nothing here: an empty answer
    hides the dropdown too, so such a test passes with the gate deleted.
    """
    suggest_fetcher.suggestions = SUGGESTIONS
    init_user("Alice")
    requests = count_suggest_requests(mobile_page)

    type_query(mobile_page, "boh")
    mobile_page.wait_for_timeout(800)

    assert requests == []
    assert suggest_fetcher.calls == []
    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()


def test_karaoke_duplicates_collapse_to_one_row(mobile_page, init_user, suggest_fetcher):
    """End-to-end check that server-side dedupe reaches the screen.

    Google really does offer both of these for the same prefix, and both run
    the identical search once "karaoke" is appended.
    """
    suggest_fetcher.suggestions = [
        "bohemian rhapsody",
        "bohemian rhapsody karaoke",
        "bohemian rhapsody piano",
    ]
    init_user("Alice")

    type_query(mobile_page)

    expect(mobile_page.locator(".search-suggestion")).to_have_count(2)
    expect(mobile_page.locator(".search-suggestion").nth(1)).to_have_text("bohemian rhapsody piano")


def test_repeat_prefixes_are_served_from_cache(mobile_page, init_user, suggest_fetcher):
    """Backtracking over characters already typed shouldn't re-ask Google."""
    suggest_fetcher.suggestions = SUGGESTIONS
    init_user("Alice")

    type_query(mobile_page)
    expect(mobile_page.locator("#search-suggestions")).to_be_visible()
    before = len(suggest_fetcher.calls)
    assert before > 0

    # Retype the identical query; every prefix is already cached.
    mobile_page.fill("#search-input", "")
    type_query(mobile_page)
    expect(mobile_page.locator("#search-suggestions")).to_be_visible()

    assert suggest_fetcher.calls[before:] == []


def test_tapping_a_suggestion_searches_for_it(mobile_page, init_user, suggest_fetcher):
    """The dropdown offers queries, so choosing one runs a search."""
    suggest_fetcher.suggestions = SUGGESTIONS
    init_user("Alice")

    type_query(mobile_page)
    mobile_page.locator(".search-suggestion").nth(1).click()

    expect(mobile_page.locator("#search-input")).to_have_value("bohemian rhapsody piano")
    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()
    # The mocked video library answers every query with the same result.
    expect(mobile_page.locator(".search-result-title")).to_have_text("Test Song")


def test_arrow_keys_select_and_enter_accepts(mobile_page, init_user, suggest_fetcher):
    """Keyboard navigation, for anyone on a real keyboard."""
    suggest_fetcher.suggestions = SUGGESTIONS
    init_user("Alice")

    type_query(mobile_page)
    # Suggestions are debounced, so they aren't in the DOM the instant typing
    # stops — arrow keys before then would find an empty list.
    expect(mobile_page.locator("#search-suggestions")).to_be_visible()

    mobile_page.keyboard.press("ArrowDown")
    mobile_page.keyboard.press("ArrowDown")

    expect(mobile_page.locator(".search-suggestion").nth(1)).to_have_attribute(
        "aria-selected", "true"
    )

    mobile_page.keyboard.press("Enter")
    expect(mobile_page.locator("#search-input")).to_have_value("bohemian rhapsody piano")
    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()


def test_escape_closes_the_dropdown(mobile_page, init_user, suggest_fetcher):
    """Escape dismisses suggestions without disturbing what was typed."""
    suggest_fetcher.suggestions = SUGGESTIONS
    init_user("Alice")

    type_query(mobile_page)
    expect(mobile_page.locator("#search-suggestions")).to_be_visible()

    mobile_page.keyboard.press("Escape")

    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()
    expect(mobile_page.locator("#search-input")).to_have_value("bohemian")


def test_enter_without_a_selection_searches_what_was_typed(mobile_page, init_user, suggest_fetcher):
    """The dropdown must not hijack Enter from someone ignoring it."""
    suggest_fetcher.suggestions = SUGGESTIONS
    init_user("Alice")

    type_query(mobile_page)
    mobile_page.keyboard.press("Enter")

    expect(mobile_page.locator("#search-input")).to_have_value("bohemian")
    expect(mobile_page.locator(".search-result-title")).to_have_text("Test Song")


def test_search_button_stays_reachable_under_the_dropdown(mobile_page, init_user, suggest_fetcher):
    """A guest who ignores the suggestions must still be able to tap Search.

    The Search button sits full-width directly below the input, so a dropdown
    anchored to the input covers it — which is exactly what happened, and what
    broke the queue tests before the list was re-anchored to the form.
    """
    suggest_fetcher.suggestions = SUGGESTIONS
    init_user("Alice")

    type_query(mobile_page)
    expect(mobile_page.locator("#search-suggestions")).to_be_visible()

    # Would time out on "element is not stable / visible" if the dropdown
    # were painted over the button.
    mobile_page.locator("#search-button").click(timeout=5000)

    expect(mobile_page.locator("#search-input")).to_have_value("bohemian")
    expect(mobile_page.locator(".search-result-title")).to_have_text("Test Song")


def test_empty_suggestions_leave_no_dropdown(mobile_page, init_user):
    """Nothing to suggest is the ordinary case, not an error state.

    The fetcher offers nothing by default, so this is the fixture as-is.
    """
    init_user("Alice")

    type_query(mobile_page)
    mobile_page.wait_for_timeout(800)

    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()


def test_clearing_the_box_closes_the_dropdown(mobile_page, init_user, suggest_fetcher):
    """Deleting back below the minimum length dismisses suggestions."""
    suggest_fetcher.suggestions = SUGGESTIONS
    init_user("Alice")

    type_query(mobile_page)
    expect(mobile_page.locator("#search-suggestions")).to_be_visible()

    mobile_page.fill("#search-input", "")
    mobile_page.locator("#search-input").type("b", delay=10)

    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()


# ---------------------------------------------------------------------------
# Conditions the server cannot produce, so the browser is the right seam.
# ---------------------------------------------------------------------------


def test_a_request_that_never_arrives_leaves_search_working(mobile_page, init_user):
    """A guest's phone losing its connection to kbox mid-request.

    There is no server-side equivalent: the request never reaches it.
    """
    init_user("Alice")
    mobile_page.route("**/api/search/suggest*", lambda route: route.abort())

    type_query(mobile_page)
    mobile_page.wait_for_timeout(800)
    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()

    mobile_page.locator("#search-button").click()
    expect(mobile_page.locator(".search-result-title")).to_have_text("Test Song")


def test_a_server_error_leaves_search_working(mobile_page, init_user):
    """A 500 is how a reply kbox can no longer parse reaches the browser.

    The guest should see an absent dropdown, not a broken page, and must
    still be able to search.
    """
    init_user("Alice")
    mobile_page.route(
        "**/api/search/suggest*",
        lambda route: route.fulfill(status=500, content_type="text/plain", body="boom"),
    )

    type_query(mobile_page)
    mobile_page.wait_for_timeout(800)
    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()

    mobile_page.locator("#search-button").click()
    expect(mobile_page.locator(".search-result-title")).to_have_text("Test Song")
