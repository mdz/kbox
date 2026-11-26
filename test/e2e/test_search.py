"""
Search: results render from the mocked VideoLibrary.

The mock returns a single result ("Test Song" / "youtube:test123"). This test
verifies the full path: search input → API → JS rendering → DOM.
"""

import sqlite3

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_search_results_render(mobile_page, init_user):
    """Searching shows results from the mocked video source."""
    init_user("Alice")
    mobile_page.fill("#search-input", "bohemian rhapsody")
    mobile_page.locator("#search-button").click()
    expect(mobile_page.locator(".search-result-title")).to_have_text("Test Song")


def test_search_result_opens_add_modal(mobile_page, init_user):
    """Tapping a search result opens the Add to Queue modal."""
    init_user("Alice")
    mobile_page.fill("#search-input", "test")
    mobile_page.locator("#search-button").click()
    mobile_page.wait_for_selector(".search-result")
    mobile_page.locator(".search-result").click()
    expect(mobile_page.locator("#add-song-modal")).to_be_visible()


def test_clear_button_empties_the_box(mobile_page, init_user):
    """The clear button appears with text, empties the box and keeps focus."""
    init_user("Alice")
    clear = mobile_page.locator("#search-clear")
    expect(clear).to_be_hidden()

    mobile_page.fill("#search-input", "bohemian rhapsody")
    expect(clear).to_be_visible()

    clear.click()
    expect(mobile_page.locator("#search-input")).to_have_value("")
    expect(mobile_page.locator("#search-input")).to_be_focused()
    expect(clear).to_be_hidden()


def _record_performance(db_path, video_id):
    """Insert a history row straight into the live app's DB (same tmp_path)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO playback_history (video_id, user_id, user_name, "
            "song_metadata_json, settings_json, performance_json) "
            "VALUES (?, 'someone', 'Someone', '{\"title\": \"Test Song\"}', '{}', "
            "'{\"completion_percentage\": 100.0}')",
            (video_id,),
        )
        conn.commit()
    finally:
        conn.close()


def test_performed_track_is_labelled(mobile_page, init_user, tmp_path):
    """A result performed here before gets the badge and the modal note (#216)."""
    _record_performance(tmp_path / "test.db", "youtube:test123")
    init_user("Alice")
    mobile_page.fill("#search-input", "test")
    mobile_page.locator("#search-button").click()

    expect(mobile_page.locator(".search-result-tested")).to_be_visible()
    expect(mobile_page.locator(".search-result")).to_have_attribute(
        "aria-label", "Select Test Song (3:00, Tested track)"
    )
    mobile_page.locator(".search-result").click()
    expect(mobile_page.locator("#add-song-modal")).to_contain_text(
        "This version has worked well here before"
    )


def test_untried_track_is_not_labelled(mobile_page, init_user):
    """With no history, results carry no label."""
    init_user("Alice")
    mobile_page.fill("#search-input", "test")
    mobile_page.locator("#search-button").click()
    mobile_page.wait_for_selector(".search-result")
    expect(mobile_page.locator(".search-result-tested")).to_have_count(0)
