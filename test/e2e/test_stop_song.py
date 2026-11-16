"""
The singer's own-song view, "Let Bob go first", and the offer of another go.

The e2e app mocks the playback controller, so nothing is ever playing. The
browser is the seam: /api/playback/status and /api/queue are stubbed to say
who is singing and who is next, and the stop-song / another-go endpoints are
stubbed to record what the page sends. What the server does with them is
covered in test_api.py and test_integration.py.
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


def _song(item_id, user_id, user_name, title):
    return {
        "id": item_id,
        "user_id": user_id,
        "user_name": user_name,
        "video_id": f"youtube:{item_id}",
        "title": title,
        "duration_seconds": 180,
        "pitch_semitones": 0,
    }


class _Party:
    """Stubbed playback status and queue, changeable mid-test."""

    def __init__(self, page):
        self.current = None
        self.next_song = None
        self.posts = []
        page.route("**/api/playback/status", self._status)
        page.route("**/api/queue", self._queue)
        page.route("**/api/playback/stop-song", self._record("stopped"))
        page.route("**/api/playback/another-go", self._record("queued"))

    def _status(self, route):
        body = {"state": "playing", "current_song": self.current, "position_seconds": 30}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def _queue(self, route):
        if route.request.method != "GET":
            route.continue_()
            return
        body = {
            "queue": [],
            "current_song_id": self.current and self.current["id"],
            "next_song": self.next_song,
            "my_next_turn": None,
            "queue_depth_seconds": 0,
            "queue_depth_count": 0,
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def _record(self, status):
        def handle(route):
            self.posts.append((route.request.url.rsplit("/", 1)[-1], route.request.post_data_json))
            route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"status": status})
            )

        return handle


@pytest.fixture
def party(mobile_page, init_user):
    init_user("Alice")
    me = _current_user_id(mobile_page)
    party = _Party(mobile_page)
    party.me = me
    party.current = _song(7, me, "Alice", "Test Song")
    party.next_song = _song(8, "bob-id", "Bob", "Bob's Song")
    return party


def test_singer_sees_only_their_song(mobile_page, party):
    button = mobile_page.locator("#stop-song-button")
    expect(button).to_contain_text("Need a minute?")
    expect(button).to_contain_text("Let Bob go first")
    expect(mobile_page.locator("#key-fix-lower")).to_be_visible()
    expect(mobile_page.locator("#search-input")).to_be_hidden()
    expect(mobile_page.locator("#queue-list")).to_be_hidden()


def test_with_nobody_next_it_says_stop(mobile_page, party):
    party.next_song = None
    expect(mobile_page.locator("#stop-song-button")).to_have_text("Stop my song")


def test_stop_takes_two_taps_then_offers_another_go(mobile_page, party):
    button = mobile_page.locator("#stop-song-button")
    expect(button).to_contain_text("Let Bob go first")

    button.click()
    expect(button).to_have_text("Tap again: stops your song")
    assert party.posts == []

    # The server moves on: Bob is singing now.
    party.current, party.next_song = party.next_song, None
    button.click()
    expect(mobile_page.locator("#another-go")).to_contain_text("Want another go after Bob?")
    assert party.posts == [("stop-song", {"item_id": 7})]

    mobile_page.locator("#another-go-same").click()
    expect(mobile_page.locator("#toast")).to_contain_text("You're up after Bob")
    assert party.posts[-1] == ("another-go", {"item_id": 7})
    expect(mobile_page.locator("#another-go")).to_be_hidden()


def _stop_with_bob_on(mobile_page, party):
    button = mobile_page.locator("#stop-song-button")
    expect(button).to_contain_text("Let Bob go first")
    button.click()
    party.current, party.next_song = party.next_song, None
    button.click()
    expect(mobile_page.locator("#another-go")).to_be_visible()


def test_no_thanks_ends_the_turn_quietly(mobile_page, party):
    _stop_with_bob_on(mobile_page, party)

    mobile_page.locator("#another-go-dismiss").click()

    expect(mobile_page.locator("#another-go")).to_be_hidden()
    assert [url for url, _ in party.posts] == ["stop-song"]


def test_different_song_opens_search_for_the_next_go(mobile_page, party):
    _stop_with_bob_on(mobile_page, party)

    mobile_page.locator("#another-go-different").click()

    expect(mobile_page.locator("#replace-target-banner")).to_have_text(
        "Pick a song for your next go"
    )
    expect(mobile_page.locator("#search-input")).to_be_focused()


def test_offer_lapses_when_their_song_is_on_again(mobile_page, party):
    _stop_with_bob_on(mobile_page, party)

    party.current = _song(7, party.me, "Alice", "Test Song")

    expect(mobile_page.locator("#stop-song-button")).to_be_visible()
    party.current = party.next_song = None
    expect(mobile_page.locator("#now-playing-content")).to_contain_text("Nothing playing")
    expect(mobile_page.locator("#another-go")).to_be_hidden()


def test_other_guests_song_shows_the_usual_page(mobile_page, party):
    party.current = _song(9, "someone-else", "Carol", "Test Song")

    expect(mobile_page.locator("#now-playing-content")).to_contain_text("Test Song")
    expect(mobile_page.locator("#stop-song-button")).to_have_count(0)
    expect(mobile_page.locator("#search-input")).to_be_visible()
    expect(mobile_page.locator("body")).not_to_have_class("singing")
