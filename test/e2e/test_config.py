"""
Config screen: load and round-trip save.

Config requires operator auth. After unlock the page reloads; we wait for
the playback-controls section (the reliable signal that isOperator=true is in
effect) before touching the config button.
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def _open_config(page):
    """Wait for operator state to be active, then open the config screen."""
    # playback-controls-section visibility is set by loadQueue+renderNowPlaying
    # and is the earliest reliable signal that isOperator=true is in effect.
    expect(page.locator("#playback-controls-section")).to_be_visible()
    page.locator("#config-toggle-button").click()
    expect(page.locator("#config-screen")).to_be_visible()
    # Wait for the form to finish loading (initial text replaced by real fields)
    expect(page.locator("#config-form-container")).not_to_contain_text("Loading configuration...")


def test_config_screen_loads(mobile_page, init_user, operator_unlock):
    """Config screen opens for operators and renders the form."""
    init_user()
    operator_unlock()
    _open_config(mobile_page)
    # At least one config group should be present
    expect(mobile_page.locator(".config-group")).not_to_have_count(0)


def test_config_round_trip(mobile_page, init_user, operator_unlock):
    """Changing a config value, saving, and reopening shows the new value."""
    init_user()
    operator_unlock()
    _open_config(mobile_page)

    # Use "Party Theme" (suggestion_theme) — a plain text field, safe to modify.
    theme_input = mobile_page.locator("#config-suggestion_theme")
    theme_input.fill("80s Night")

    mobile_page.locator("button:has-text('Save Configuration')").click()
    expect(mobile_page.locator("#config-message")).to_contain_text("saved successfully")

    # Close and reopen config to verify persistence.
    # Use the config toggle button — avoids ambiguity with "Close" buttons
    # in the help/history modals that are also in the DOM but hidden.
    mobile_page.locator("#config-toggle-button").click()
    expect(mobile_page.locator("#config-screen")).to_be_hidden()
    mobile_page.locator("#config-toggle-button").click()
    expect(mobile_page.locator("#config-screen")).to_be_visible()
    expect(mobile_page.locator("#config-form-container")).not_to_contain_text(
        "Loading configuration..."
    )

    expect(mobile_page.locator("#config-suggestion_theme")).to_have_value("80s Night")
