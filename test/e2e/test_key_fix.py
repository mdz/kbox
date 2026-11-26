"""
The singer's "Too high? / Too low?" key fix on the Now Playing card.

The e2e app mocks the playback controller, so nothing is ever playing. The
browser is the seam: the playback field of /api/status is stubbed to
report a song, and /api/playback/pitch is stubbed to record what the page
sends. Whether the server lets a singer change their own song is covered in
test_api.py.
"""

import json

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def _current_user_id(page):
    return page.evaluate(
        """async () => {
            const appSrc = document.querySelector('script[type="module"]').src;
            const { userId } = await import(new URL('./state.js', appSrc).href);
            return userId;
        }"""
    )


def _stub_status(page, user_id, position, pitch=0):
    song = {
        "id": 1,
        "user_id": user_id,
        "user_name": "Someone",
        "video_id": "youtube:test123",
        "title": "Test Song",
        "duration_seconds": 180,
        "pitch_semitones": pitch,
    }
    playback = {"state": "playing", "current_song": song, "position_seconds": position}

    def handle(route):
        # The real response, with only what playback is doing swapped in
        response = route.fetch()
        data = response.json()
        data["playback"] = playback
        route.fulfill(response=response, json=data)

    page.route("**/api/status", handle)


def _record_pitch_posts(page):
    sent = []

    def handle(route):
        payload = json.loads(route.request.post_data)
        sent.append(payload["semitones"])
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"status": "updated", "pitch": payload["semitones"]}),
        )

    page.route("**/api/playback/pitch", handle)
    return sent


def test_singer_lowers_the_key_by_a_whole_step(mobile_page, init_user):
    init_user("Alice")
    _stub_status(mobile_page, _current_user_id(mobile_page), position=20)
    sent = _record_pitch_posts(mobile_page)

    key_fix = mobile_page.locator("#key-fix")
    expect(mobile_page.locator("#key-fix-lower")).to_be_visible()
    expect(key_fix.locator(".key-fix-status")).to_contain_text("Original key")

    mobile_page.locator("#key-fix-lower").click()
    expect(key_fix.locator(".key-fix-status")).to_contain_text("2 steps lower")
    mobile_page.locator("#key-fix-lower").click()
    expect(key_fix.locator(".key-fix-status")).to_contain_text("4 steps lower")
    assert sent == [-2, -4]

    mobile_page.locator("#key-fix-reset").click()
    expect(key_fix.locator(".key-fix-status")).to_contain_text("Original key")
    assert sent == [-2, -4, 0]


def test_key_fix_is_ready_late_in_the_song(mobile_page, init_user):
    """A singer looks at their phone only when something is wrong, and that
    can be at any point in the song: the full control is there throughout."""
    init_user("Alice")
    _stub_status(mobile_page, _current_user_id(mobile_page), position=170, pitch=-2)

    expect(mobile_page.locator("#key-fix-lower")).to_be_visible()
    expect(mobile_page.locator("#key-fix-higher")).to_be_visible()
    expect(mobile_page.locator("#key-fix .key-fix-status")).to_contain_text("2 steps lower")


def test_key_fix_only_for_the_singer(mobile_page, init_user):
    init_user("Alice")
    _stub_status(mobile_page, "someone-else", position=20)

    expect(mobile_page.locator("#now-playing-content")).to_contain_text("Test Song")
    expect(mobile_page.locator("#key-fix")).to_have_count(0)
