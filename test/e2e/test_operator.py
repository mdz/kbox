"""
Operator authentication: PIN modal, unlock, wrong-PIN error.

After a successful PIN entry the server reloads the page. On reload,
checkOperatorStatus() fetches the session and sets isOperator=true; the next
loadQueue() tick makes the playback-controls section visible.

NOTE: the operator-auth button label does NOT update after reload (it is set
once on page load before the async status check returns). Assertions use the
playback-controls section, which is rendered by loadQueue() and reliably
reflects operator state.
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_operator_button_shows_pin_modal(mobile_page, live_app, init_user):
    """Clicking the operator key opens the PIN entry modal."""
    init_user()
    mobile_page.locator("#operator-auth-button").click()
    expect(mobile_page.locator("#operator-pin-modal")).to_be_visible()


def test_wrong_pin_shows_error(mobile_page, live_app, init_user):
    """Submitting an incorrect PIN shows an error inside the modal."""
    init_user()
    mobile_page.locator("#operator-auth-button").click()
    mobile_page.wait_for_selector("#operator-pin-modal", state="visible")
    mobile_page.fill("#operator-pin-input", "9999")
    mobile_page.locator("button:has-text('Authenticate')").click()
    expect(mobile_page.locator("#operator-pin-message")).to_contain_text("Invalid PIN")
    # Modal stays open
    expect(mobile_page.locator("#operator-pin-modal")).to_be_visible()


def test_correct_pin_reveals_playback_controls(mobile_page, live_app, init_user, operator_unlock):
    """Correct PIN causes a page reload; after reload, playback controls are visible."""
    init_user()
    operator_unlock()
    # loadQueue() runs after reload and renderNowPlaying() shows the controls for operators
    expect(mobile_page.locator("#playback-controls-section")).to_be_visible()
