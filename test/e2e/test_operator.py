"""
Operator authentication: PIN modal, unlock, wrong-PIN error, and playback controls.

After a successful PIN entry the server reloads the page. On reload,
checkOperatorStatus() fetches the session and sets isOperator=true; the next
loadQueue() tick makes the playback-controls section visible.

NOTE: the operator-auth button label does NOT update after reload (it is set
once on page load before the async status check returns). Assertions use the
playback-controls section, which is rendered by loadQueue() and reliably
reflects operator state.

Playback control tests verify the full button → JS → fetch → API endpoint path
without requiring a song to be playing. The mock PlaybackController returns
True for all control methods, so skip/seek/play all return HTTP 200. State
boundary: these tests confirm the control plane wiring; actual audio/video
output is not tested here (GStreamer/IFrame boundary, per project policy).
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def _unlock_and_reveal_buttons(mobile_page, init_user, operator_unlock):
    """Navigate, unlock operator, reveal the playback button panel."""
    init_user()
    operator_unlock()
    # Wait for the operator panel to be visible (rendered by loadQueue tick)
    expect(mobile_page.locator("#playback-controls-section")).to_be_visible()
    # The buttons panel starts hidden; clicking the lock button reveals it
    mobile_page.locator("#playback-lock-button").click()
    expect(mobile_page.locator("#playback-buttons-section")).to_be_visible()


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


def test_skip_button_calls_api(mobile_page, live_app, init_user, operator_unlock):
    """Skip button issues POST /api/playback/skip and receives HTTP 200."""
    _unlock_and_reveal_buttons(mobile_page, init_user, operator_unlock)
    with mobile_page.expect_response(
        lambda r: "/api/playback/skip" in r.url and r.request.method == "POST"
    ) as resp_info:
        mobile_page.locator("button:has-text('Skip')").click()
    assert resp_info.value.status == 200


def test_seek_forward_button_calls_api(mobile_page, live_app, init_user, operator_unlock):
    """Seek-forward (+10s) button issues POST /api/playback/seek and receives HTTP 200."""
    _unlock_and_reveal_buttons(mobile_page, init_user, operator_unlock)
    with mobile_page.expect_response(
        lambda r: "/api/playback/seek" in r.url and r.request.method == "POST"
    ) as resp_info:
        mobile_page.locator("button:has-text('+10s')").click()
    assert resp_info.value.status == 200


def test_play_pause_button_calls_play_when_idle(mobile_page, live_app, init_user, operator_unlock):
    """Play/Pause toggle issues POST /api/playback/play when the mock is in idle state.

    The mock PlaybackController reports state='idle' and play() returns False
    (no song queued), so the server returns 400. The test dismisses the resulting
    alert and asserts the correct endpoint was called — verifying button → JS →
    fetch wiring independently of playback outcome.
    """
    _unlock_and_reveal_buttons(mobile_page, init_user, operator_unlock)
    # Dismiss any alert() that JS may raise on a non-ok response
    mobile_page.on("dialog", lambda d: d.dismiss())
    with mobile_page.expect_response(
        lambda r: "/api/playback/play" in r.url and r.request.method == "POST"
    ) as resp_info:
        mobile_page.locator("#play-pause-toggle").click()
    # Endpoint was reached (mock play() returns False → 400, but the call was made)
    assert resp_info.value.status in (200, 400)
