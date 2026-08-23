"""
Queue UI: adding songs and duplicate-entry warnings.

Covers the highest-traffic guest path (search → add) and the regression
lock for the "already queued / already played" warning (commit d2803ac).

NOTE: queue items only show the singer's name when the user is not an
operator. The song title is visible to operators only. Assertions use
the user name ("Alice") which is always present.
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def _search_and_open_add_modal(page, query="test song"):
    """Search for the mock result and open the Add to Queue modal."""
    page.fill("#search-input", query)
    page.locator("#search-button").click()
    page.wait_for_selector(".search-result")
    page.locator(".search-result").click()
    page.wait_for_selector("#add-song-modal", state="visible")


def test_add_song_appears_in_queue(mobile_page, init_user):
    """Adding a song through the UI causes it to appear in the queue list."""
    init_user("Alice")
    _search_and_open_add_modal(mobile_page)
    mobile_page.locator("button:has-text('Add to Queue')").click()
    mobile_page.wait_for_selector("#add-song-modal", state="hidden")
    expect(mobile_page.locator(".queue-item")).to_have_count(1)
    expect(mobile_page.locator(".queue-item")).to_contain_text("Alice")


def test_already_queued_warning(mobile_page, init_user):
    """Adding the same song a second time shows a confirmation dialog."""
    init_user("Alice")

    # Add the song once
    _search_and_open_add_modal(mobile_page)
    mobile_page.locator("button:has-text('Add to Queue')").click()
    mobile_page.wait_for_selector("#add-song-modal", state="hidden")
    mobile_page.wait_for_selector(".queue-item")

    # Try to add it again
    _search_and_open_add_modal(mobile_page)

    # Click "Add to Queue" — JS checks currentQueue and calls confirm().
    # Register handler before click; confirm() fires synchronously (no await
    # precedes it in confirmAddToQueue), so messages is populated by the time
    # click() returns.
    messages = []
    mobile_page.on("dialog", lambda d: (messages.append(d.message), d.dismiss()))
    mobile_page.locator("button:has-text('Add to Queue')").click()
    assert messages, "expected a confirm() dialog for the duplicate song"
    assert "already in the queue" in messages[0]

    # Song count stays at 1 (user dismissed the confirmation)
    expect(mobile_page.locator(".queue-item")).to_have_count(1)


def test_second_song_nudge(mobile_page, init_user):
    """Adding a second, different song while one is still unplayed nudges (soft, dismissible)."""
    init_user("Alice")

    # Add a first song
    _search_and_open_add_modal(mobile_page)
    mobile_page.locator("button:has-text('Add to Queue')").click()
    mobile_page.wait_for_selector("#add-song-modal", state="hidden")
    mobile_page.wait_for_selector(".queue-item")

    # Add a different song - should nudge since the first is still unplayed
    _search_and_open_add_modal(mobile_page, query="second song")
    messages = []
    mobile_page.on("dialog", lambda d: (messages.append(d.message), d.accept()))
    mobile_page.locator("button:has-text('Add to Queue')").click()
    mobile_page.wait_for_selector("#add-song-modal", state="hidden")

    assert messages, "expected a confirm() dialog for the second-song nudge"
    assert "already have a song queued" in messages[0]

    # User confirmed, so both songs are queued
    expect(mobile_page.locator(".queue-item")).to_have_count(2)
