"""
Search: results render from the mocked VideoLibrary.

The mock returns a single result ("Test Song" / "youtube:test123"). This test
verifies the full path: search input → API → JS rendering → DOM.
"""

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
