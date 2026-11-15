"""
Queue UI: adding songs and duplicate-entry warnings.

Covers the highest-traffic guest path (search → add) and the regression
lock for the "already queued / already played" warning (commit d2803ac).

NOTE: for non-operators, queue items show the song title only on the
guest's own rows (marked "You"); other guests' titles stay hidden. Assertions
use the user name ("Alice") which is always present.
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
    """Adding the same song a second time shows an inline warning in the modal."""
    init_user("Alice")

    # Add the song once
    _search_and_open_add_modal(mobile_page)
    mobile_page.locator("button:has-text('Add to Queue')").click()
    mobile_page.wait_for_selector("#add-song-modal", state="hidden")
    mobile_page.wait_for_selector(".queue-item")

    # Try to add it again
    _search_and_open_add_modal(mobile_page)

    # The warning is shown inline in the modal and the button becomes
    # "Add anyway"; no native dialog is involved.
    expect(
        mobile_page.locator("#add-song-modal .add-song-warning", has_text="already in the queue")
    ).to_have_count(1)
    expect(mobile_page.locator("#add-song-modal-confirm")).to_have_text("Add anyway")

    # Cancelling leaves the song count at 1
    mobile_page.locator("#add-song-modal button:has-text('Cancel')").click()
    expect(mobile_page.locator(".queue-item")).to_have_count(1)


def test_second_song_nudge(mobile_page, init_user):
    """Adding a second, different song while one is still unplayed nudges (soft, inline, never blocking)."""
    init_user("Alice")

    # Add a first song
    _search_and_open_add_modal(mobile_page)
    mobile_page.locator("button:has-text('Add to Queue')").click()
    mobile_page.wait_for_selector("#add-song-modal", state="hidden")
    mobile_page.wait_for_selector(".queue-item")

    # Add a different song - should nudge since the first is still unplayed
    _search_and_open_add_modal(mobile_page, query="second song")
    expect(mobile_page.locator("#add-song-modal .add-song-warning")).to_contain_text(
        "already have a song queued"
    )
    mobile_page.locator("#add-song-modal-confirm").click()
    mobile_page.wait_for_selector("#add-song-modal", state="hidden")

    # User confirmed, so both songs are queued
    expect(mobile_page.locator(".queue-item")).to_have_count(2)


def test_user_name_is_rendered_as_text_not_html(mobile_page, init_user):
    """A display name containing markup shows literally, not as elements."""
    init_user("<b>x</b>")
    _search_and_open_add_modal(mobile_page)
    mobile_page.locator("button:has-text('Add to Queue')").click()
    mobile_page.wait_for_selector("#add-song-modal", state="hidden")
    item = mobile_page.locator(".queue-item")
    expect(item).to_have_count(1)
    expect(item.locator(".queue-item-user")).to_contain_text("<b>x</b>")
    expect(item.locator(".queue-item-user b")).to_have_count(0)
