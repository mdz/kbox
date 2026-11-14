"""
Search autocomplete: the dropdown, keyboard navigation, and graceful failure.

/api/search/suggest is intercepted at the browser so these tests never touch
Google's suggestion endpoint — it's undocumented, rate-limited by unknown
rules, and a test suite that depends on it would fail for reasons that have
nothing to do with kbox.
"""

import json

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

SUGGESTIONS = [
    "bohemian rhapsody",
    "bohemian rhapsody piano",
    "bohemian rhapsody lyrics",
]


def stub_suggestions(page, suggestions=SUGGESTIONS, status=200):
    """Intercept the suggest endpoint and answer with canned data."""

    def handler(route):
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps({"suggestions": suggestions}),
        )

    page.route("**/api/search/suggest*", handler)


def type_query(page, text="bohemian"):
    """Type into the search box so `input` events fire per character."""
    page.locator("#search-input").click()
    page.locator("#search-input").type(text, delay=10)


def test_suggestions_appear_while_typing(mobile_page, init_user):
    """Typing past the minimum length opens the dropdown."""
    init_user("Alice")
    stub_suggestions(mobile_page)

    type_query(mobile_page)

    expect(mobile_page.locator("#search-suggestions")).to_be_visible()
    expect(mobile_page.locator(".search-suggestion")).to_have_count(len(SUGGESTIONS))
    expect(mobile_page.locator(".search-suggestion").first).to_have_text("bohemian rhapsody")


def test_short_queries_do_not_open_the_dropdown(mobile_page, init_user):
    """One or two characters match everything — not worth a dropdown."""
    init_user("Alice")
    stub_suggestions(mobile_page)

    type_query(mobile_page, "bo")

    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()


def test_tapping_a_suggestion_searches_for_it(mobile_page, init_user):
    """The dropdown offers queries, so choosing one runs a search."""
    init_user("Alice")
    stub_suggestions(mobile_page)

    type_query(mobile_page)
    mobile_page.locator(".search-suggestion").nth(1).click()

    expect(mobile_page.locator("#search-input")).to_have_value("bohemian rhapsody piano")
    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()
    # The mocked video library answers every query with the same result.
    expect(mobile_page.locator(".search-result-title")).to_have_text("Test Song")


def test_arrow_keys_select_and_enter_accepts(mobile_page, init_user):
    """Keyboard navigation, for anyone on a real keyboard."""
    init_user("Alice")
    stub_suggestions(mobile_page)

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


def test_escape_closes_the_dropdown(mobile_page, init_user):
    """Escape dismisses suggestions without disturbing what was typed."""
    init_user("Alice")
    stub_suggestions(mobile_page)

    type_query(mobile_page)
    expect(mobile_page.locator("#search-suggestions")).to_be_visible()

    mobile_page.keyboard.press("Escape")

    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()
    expect(mobile_page.locator("#search-input")).to_have_value("bohemian")


def test_enter_without_a_selection_searches_what_was_typed(mobile_page, init_user):
    """The dropdown must not hijack Enter from someone ignoring it."""
    init_user("Alice")
    stub_suggestions(mobile_page)

    type_query(mobile_page)
    mobile_page.keyboard.press("Enter")

    expect(mobile_page.locator("#search-input")).to_have_value("bohemian")
    expect(mobile_page.locator(".search-result-title")).to_have_text("Test Song")


def test_empty_suggestions_leave_no_dropdown(mobile_page, init_user):
    """Nothing to suggest is the ordinary case, not an error state."""
    init_user("Alice")
    stub_suggestions(mobile_page, suggestions=[])

    type_query(mobile_page)

    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()


def test_failed_suggest_request_leaves_search_working(mobile_page, init_user):
    """A broken upstream degrades to the pre-autocomplete search box."""
    init_user("Alice")
    mobile_page.route("**/api/search/suggest*", lambda route: route.abort())

    type_query(mobile_page)
    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()

    mobile_page.locator("#search-button").click()
    expect(mobile_page.locator(".search-result-title")).to_have_text("Test Song")


def test_search_button_stays_reachable_under_the_dropdown(mobile_page, init_user):
    """A guest who ignores the suggestions must still be able to tap Search.

    The Search button sits full-width directly below the input, so a dropdown
    anchored to the input covers it — which is exactly what happened, and what
    broke the queue tests before the list was re-anchored to the form.
    """
    init_user("Alice")
    stub_suggestions(mobile_page)

    type_query(mobile_page)
    expect(mobile_page.locator("#search-suggestions")).to_be_visible()

    # Would time out on "element is not stable / visible" if the dropdown
    # were painted over the button.
    mobile_page.locator("#search-button").click(timeout=5000)

    expect(mobile_page.locator("#search-input")).to_have_value("bohemian")
    expect(mobile_page.locator(".search-result-title")).to_have_text("Test Song")


def test_clearing_the_box_closes_the_dropdown(mobile_page, init_user):
    """Deleting back below the minimum length dismisses suggestions."""
    init_user("Alice")
    stub_suggestions(mobile_page)

    type_query(mobile_page)
    expect(mobile_page.locator("#search-suggestions")).to_be_visible()

    mobile_page.fill("#search-input", "")
    mobile_page.locator("#search-input").type("b", delay=10)

    expect(mobile_page.locator("#search-suggestions")).to_be_hidden()
