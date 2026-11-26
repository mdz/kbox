"""
The singer's own-song view: only their song, with "End my turn".

The e2e app mocks the playback controller, so nothing is ever playing. The
browser is the seam: the playback field of /api/status is stubbed to
report a song, and /api/playback/end-turn is stubbed to record what the
page sends. Whether the server lets a singer end their own turn is covered
in test_api.py.
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


def _stub_status(page, user_id, item_id=7):
    song = {
        "id": item_id,
        "user_id": user_id,
        "user_name": "Someone",
        "video_id": "youtube:test123",
        "title": "Test Song",
        "duration_seconds": 180,
        "pitch_semitones": 0,
    }
    playback = {"state": "playing", "current_song": song, "position_seconds": 30}

    def handle(route):
        # The real response, with only what playback is doing swapped in
        response = route.fetch()
        data = response.json()
        data["playback"] = playback
        route.fulfill(response=response, json=data)

    page.route("**/api/status", handle)


def _record_end_turn_posts(page):
    sent = []

    def handle(route):
        sent.append(json.loads(route.request.post_data))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"status": "ended"}),
        )

    page.route("**/api/playback/end-turn", handle)
    return sent


def test_singer_sees_only_their_song(mobile_page, init_user):
    init_user("Alice")
    _stub_status(mobile_page, _current_user_id(mobile_page))

    expect(mobile_page.locator("#end-turn-button")).to_be_visible()
    expect(mobile_page.locator("#key-fix-lower")).to_be_visible()
    expect(mobile_page.locator("#search-input")).to_be_hidden()
    expect(mobile_page.locator("#queue-list")).to_be_hidden()


def test_ending_a_turn_takes_two_taps(mobile_page, init_user):
    init_user("Alice")
    _stub_status(mobile_page, _current_user_id(mobile_page), item_id=7)
    sent = _record_end_turn_posts(mobile_page)
    button = mobile_page.locator("#end-turn-button")

    button.click()
    expect(button).to_have_text("Tap again: ends your turn")
    assert sent == []

    button.click()
    expect(mobile_page.locator("#toast")).to_contain_text(
        "Turn ended. Want another go? Ask the host."
    )
    assert sent == [{"item_id": 7}]
    expect(button).to_have_text("End my turn")


def test_other_guests_song_shows_the_usual_page(mobile_page, init_user):
    init_user("Alice")
    _stub_status(mobile_page, "someone-else")

    expect(mobile_page.locator("#now-playing-content")).to_contain_text("Test Song")
    expect(mobile_page.locator("#end-turn-button")).to_have_count(0)
    expect(mobile_page.locator("#search-input")).to_be_visible()
    expect(mobile_page.locator("#queue-list")).to_be_attached()
    expect(mobile_page.locator("body")).not_to_have_class("singing")
