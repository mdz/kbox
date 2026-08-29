"""
Identity flow: first-run name capture, and name-keyed recognition on return.

Covers: name modal appears on first visit, dismisses after entry, does not
reappear on reload (name survives in localStorage + session). Also covers
the recognition flow from ldocs/GUEST_IDENTITY_CONTINUITY.md /
ldocs/GUEST_IDENTITY_TECHNICAL_DESIGN.md: a guest typing a name that matches
an existing identity (e.g. after localStorage was lost) sees a recognition
list instead of silently getting a brand-new, disconnected identity.
"""

import httpx
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
    # Wait for the search form — a concrete signal the page has rendered its
    # initial state. networkidle is unreliable because the 1s /api/queue poll
    # prevents the browser from ever reaching truly idle.
    mobile_page.wait_for_selector("#search-form", state="visible")
    expect(mobile_page.locator("#name-modal")).to_be_hidden()


def test_new_name_never_shows_recognition_list(mobile_page, live_app):
    """A genuinely new name goes straight through — nothing changes from today."""
    mobile_page.goto(live_app)
    mobile_page.wait_for_selector("#name-modal", state="visible")
    mobile_page.fill("#name-modal-input", "BrandNewGuest")
    mobile_page.locator("button:has-text('Continue')").click()
    mobile_page.wait_for_selector("#name-modal", state="hidden")
    expect(mobile_page.locator("#recognition-modal")).to_be_hidden()


def _second_visit_types_name(mobile_page, live_app, name):
    """Open a fresh, isolated browser context against the same server — as if
    a guest is on a different device (or lost localStorage) — and type `name`
    into the name modal. Returns the new page with the recognition step
    (if any) not yet resolved."""
    browser = mobile_page.context.browser
    context = browser.new_context()
    page = context.new_page()
    page.goto(live_app)
    page.wait_for_selector("#name-modal", state="visible")
    page.fill("#name-modal-input", name)
    page.locator("button:has-text('Continue')").click()
    return context, page


def test_returning_guest_sees_recognition_list(mobile_page, live_app, init_user):
    """A typed name matching an existing identity shows a recognition list
    instead of silently creating a disconnected new one."""
    init_user("Vlad")

    context, page = _second_visit_types_name(mobile_page, live_app, "Vlad")
    try:
        expect(page.locator("#recognition-modal")).to_be_visible()
        candidates = page.locator(".recognition-candidate")
        expect(candidates).to_have_count(1)
        expect(candidates.first).to_contain_text("Vlad")
    finally:
        context.close()


def test_choosing_a_candidate_reuses_the_existing_identity(mobile_page, live_app, init_user):
    """Tapping a recognized candidate claims the same identity, not a new one."""
    init_user("Vlad")

    context, page = _second_visit_types_name(mobile_page, live_app, "Vlad")
    try:
        page.wait_for_selector("#recognition-modal", state="visible")
        page.locator(".recognition-candidate").first.click()
        page.wait_for_selector("#recognition-modal", state="hidden")

        response = httpx.get(f"{live_app}/api/users/lookup", params={"name": "Vlad"})
        assert len(response.json()["candidates"]) == 1  # still just one Vlad
    finally:
        context.close()


def test_choosing_im_new_creates_a_separate_identity(mobile_page, live_app, init_user):
    """'None of these — I'm new' on a collision adds a distinct ghost
    identity rather than erroring or merging — an accepted cost, per
    ldocs/GUEST_IDENTITY_CONTINUITY.md."""
    init_user("Vlad")

    context, page = _second_visit_types_name(mobile_page, live_app, "Vlad")
    try:
        page.wait_for_selector("#recognition-modal", state="visible")
        page.locator("#recognition-new-button").click()
        page.wait_for_selector("#recognition-modal", state="hidden")

        response = httpx.get(f"{live_app}/api/users/lookup", params={"name": "Vlad"})
        assert len(response.json()["candidates"]) == 2
    finally:
        context.close()
