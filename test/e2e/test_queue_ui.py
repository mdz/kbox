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


def _search_and_open_add_modal(page):
    """Search for the mock result and open the Add to Queue modal."""
    page.fill("#search-input", "test song")
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
