"""
Phase 3: pure-JS helper coverage via page.evaluate().

Each function is imported as an ES module from the live server and exercised
with an input table inside the browser. No Node toolchain needed — the browser
is the JS runtime.

Boundary: these tests verify the pure logic of the helpers, not DOM layout or
API calls. They run on both engines (Chromium / WebKit) via mobile_page.
"""

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# getIntervalName (pitch.js)
# ---------------------------------------------------------------------------

_INTERVAL_CASES = [
    # (semitones, expected_name)
    (-12, "Octave down"),
    (-11, "Major Seventh down"),
    (-10, "Minor Seventh down"),
    (-9, "Major Sixth down"),
    (-8, "Minor Sixth down"),
    (-7, "Perfect Fifth down"),
    (-6, "Tritone down"),
    (-5, "Perfect Fourth down"),
    (-4, "Major Third down"),
    (-3, "Minor Third down"),
    (-2, "Major Second down"),
    (-1, "Minor Second down"),
    (0, "Unison"),
    (1, "Minor Second up"),
    (2, "Major Second up"),
    (3, "Minor Third up"),
    (4, "Major Third up"),
    (5, "Perfect Fourth up"),
    (6, "Tritone up"),
    (7, "Perfect Fifth up"),
    (8, "Minor Sixth up"),
    (9, "Major Sixth up"),
    (10, "Minor Seventh up"),
    (11, "Major Seventh up"),
    (12, "Octave up"),
    # out-of-range: fallback to "{n} semitones [up|down]"
    (13, "13 semitones up"),
    (-13, "13 semitones down"),
]


def test_interval_names(mobile_page, live_app):
    """getIntervalName covers all 25 named intervals plus the numeric fallback."""
    mobile_page.goto(live_app)

    inputs = [s for s, _ in _INTERVAL_CASES]
    expected = [name for _, name in _INTERVAL_CASES]

    results = mobile_page.evaluate(
        """async (inputs) => {
            const { getIntervalName } = await import('/static/js/pitch.js');
            return inputs.map(getIntervalName);
        }""",
        inputs,
    )

    assert results == expected


# ---------------------------------------------------------------------------
# formatSliderValue (config.js)
# ---------------------------------------------------------------------------

_SLIDER_CASES = [
    # (value, format, expected)
    (0.5, "percent", "50%"),
    (0.0, "percent", "0%"),
    (1.0, "percent", "100%"),
    (0.756, "percent", "76%"),  # rounding: Math.round(75.6) = 76
    (75, "percent_int", "75%"),
    (75.6, "percent_int", "76%"),  # rounding: Math.round(75.6) = 76
    (30, "seconds", "30s"),
    (0, "seconds", "0s"),
    (0, "minutes", "Off"),  # zero → "Off"
    (5, "minutes", "5 min"),
    (1, "minutes", "1 min"),
    (42, "unknown", "42"),  # default: String(value)
    (3.14, "", "3.14"),  # default: String(value)
]


def test_format_slider_value(mobile_page, live_app):
    """formatSliderValue covers all format branches and the default fallback."""
    mobile_page.goto(live_app)

    cases = [{"value": v, "format": f} for v, f, _ in _SLIDER_CASES]
    expected = [e for _, _, e in _SLIDER_CASES]

    results = mobile_page.evaluate(
        """async (cases) => {
            const { formatSliderValue } = await import('/static/js/config.js');
            return cases.map(({value, format}) => formatSliderValue(value, format));
        }""",
        cases,
    )

    assert results == expected


# ---------------------------------------------------------------------------
# escapeHtml (utils.js) — XSS safety check
# ---------------------------------------------------------------------------


def test_escape_html_inert(mobile_page, live_app):
    """escapeHtml neutralises XSS payloads before innerHTML insertion.

    Imports the real function from the live server, passes a <script>-laden
    string, inserts the escaped output as innerHTML into a new element, and
    asserts the script tag never executed.
    """
    mobile_page.goto(live_app)

    result = mobile_page.evaluate(
        """async () => {
            const { escapeHtml } = await import('/static/js/utils.js');
            const payload = '<script>window.__xss_triggered = true;<\\/script>';
            const escaped = escapeHtml(payload);

            // Insert as innerHTML — this is the risky operation escapeHtml guards
            const div = document.createElement('div');
            div.innerHTML = escaped;
            document.body.appendChild(div);

            return {
                escaped: escaped,
                xss_triggered: !!window.__xss_triggered,
                visible_text: div.textContent,
            };
        }"""
    )

    assert not result["xss_triggered"], "script tag in escapeHtml output must not execute"
    assert "<script>" not in result["escaped"], (
        "< must be HTML-encoded; raw <script> must not appear in escaped output"
    )
    # The text content should be the raw payload (visible as literal text, not code)
    assert "<script>" in result["visible_text"]
