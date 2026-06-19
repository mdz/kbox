"""
Phase 1 smoke test: browser can reach the live server and the UI renders.

Proves the full stack (Playwright → uvicorn → FastAPI → Jinja2 templates)
works on both Chromium (mobile Chrome) and WebKit (mobile Safari).
"""

import pytest

pytestmark = pytest.mark.e2e


def test_index_renders(mobile_page, live_app):
    """The main page loads and the kbox header is visible."""
    mobile_page.goto(live_app)
    mobile_page.wait_for_selector("h1")
    assert "kbox" in mobile_page.locator("h1").inner_text()
