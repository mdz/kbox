"""
Identity flow: first-run name capture.

Covers: name modal appears on first visit, dismisses after entry, does not
reappear on reload (name survives in localStorage + session).
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_first_visit_shows_name_modal(mobile_page, live_app):
    """Visiting for the first time prompts for a name."""
    mobile_page.goto(live_app)
    expect(mobile_page.locator("#name-modal")).to_be_visible()


def test_enter_name_dismisses_modal(mobile_page, init_user):
    """Entering a name and clicking Continue hides the modal."""
    init_user("Alice")
    expect(mobile_page.locator("#name-modal")).to_be_hidden()


def test_name_persists_on_reload(mobile_page, init_user):
    """After name is saved, reloading the page does not show the modal again."""
    init_user("Alice")
    mobile_page.reload()
    mobile_page.wait_for_load_state("networkidle")
    expect(mobile_page.locator("#name-modal")).to_be_hidden()
