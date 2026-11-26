/**
 * Delivering alerts to this phone.
 *
 * The server addresses alerts to people -- a guest, or whoever is operating
 * (see kbox/alerts.py) -- and hands each viewer theirs in the /api/status
 * poll. This module is the phone end of that: the first time it sees an
 * alert id it buzzes and shows the message.
 *
 * Buzzing uses the Vibration API, so it only happens where the browser has
 * one (Android, not iOS) and only while the page is open and the phone
 * unlocked. An alert stays active until its problem is fixed, so a phone
 * that was locked buzzes as soon as the page polls again. Delivery that
 * reaches a locked phone (Web Push) would be another channel on the server;
 * nothing here would need to change for it.
 */

import { showToast } from './toast.js';

const SEEN_KEY = 'kbox-seen-alert-ids';
const MAX_SEEN = 50;
const ALERT_TOAST_MS = 8000;

// Long-short-long, distinct from the turn bar's "you're next" double pulse.
export const ALERT_VIBRATION = [400, 150, 400, 150, 400];

// What tapping an alert does, by kind (see KIND_* in kbox/alerts.py). Through
// window, like the page's onclick handlers, to keep this module free of the
// queue and search modules.
const TAP_ACTIONS = {
    download_failed: alert => window.fixFailedSong(alert.queue_item_id),
};

let seenInMemory = null;

function loadSeen() {
    if (seenInMemory) return seenInMemory;
    seenInMemory = [];
    try {
        const stored = JSON.parse(localStorage.getItem(SEEN_KEY) || '[]');
        if (Array.isArray(stored)) seenInMemory = stored;
    } catch (e) {
        // Private mode or blocked storage: remember for this page load only.
    }
    return seenInMemory;
}

function saveSeen(seen) {
    seenInMemory = seen.slice(-MAX_SEEN);
    try {
        localStorage.setItem(SEEN_KEY, JSON.stringify(seenInMemory));
    } catch (e) {
        // Ignore: the in-memory copy still stops repeat buzzes on this page.
    }
}

/** Vibrate the phone, where the browser supports it. Never throws. */
export function buzz(pattern) {
    if (typeof navigator.vibrate !== 'function') return;
    try { navigator.vibrate(pattern); } catch (e) { /* ignore */ }
}

/**
 * Pure: which alerts haven't been seen yet.
 * @param {Array<{id: string}>} alerts
 * @param {string[]} seenIds
 */
export function unseenAlerts(alerts, seenIds) {
    const seen = new Set(seenIds);
    return (alerts || []).filter(alert => alert && alert.id && !seen.has(alert.id));
}

/**
 * Buzz and show each alert this phone hasn't seen before.
 * @param {Array<{id: string, kind: string, message: string}>} alerts
 */
export function handleAlerts(alerts) {
    const seen = loadSeen();
    const fresh = unseenAlerts(alerts, seen);
    if (fresh.length === 0) return;

    buzz(ALERT_VIBRATION);
    // One toast at a time: the newest is the one worth reading now; the
    // persistent places (turn bar, queue list) carry the rest.
    const latest = fresh[fresh.length - 1];
    const action = TAP_ACTIONS[latest.kind];
    showToast(latest.message, {
        error: true,
        durationMs: ALERT_TOAST_MS,
        onTap: action ? () => action(latest) : null,
    });
    saveSeen(seen.concat(fresh.map(alert => alert.id)));
}
