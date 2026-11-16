/**
 * "Stop my song" for the singer's own song.
 *
 * A singer takes their phone out mid-song only when something is wrong. If
 * it's the key, the key fix above handles it; if it's anything else (wrong
 * song, wrong version, a bad karaoke track) they just need to say "I can't
 * go on with this". So this is one button, with no reasons to pick from.
 *
 * Two taps, like Remove in the queue editor: the first arms the button, a
 * second within a few seconds stops the song. Nothing changes on screen for
 * the room until that second tap.
 *
 * POST /api/playback/stop-song names the song the page saw playing, so a tap
 * that lands after the song ended can't stop the next singer.
 */

import { showToast } from './toast.js';

export const STOP_CONFIRM_MS = 4000;

let state = { itemId: null, armed: false, pending: false, timer: null };

function button() {
    return document.getElementById('stop-song-button');
}

function paint() {
    const btn = button();
    if (!btn) return;
    btn.textContent = state.armed ? 'Tap again to stop' : 'Stop my song';
    btn.classList.toggle('armed', state.armed);
    btn.disabled = state.pending;
}

function disarm() {
    clearTimeout(state.timer);
    state.timer = null;
    state.armed = false;
    paint();
}

// Build the control for the current song, below the key fix.
export function renderStopSong(container, itemId) {
    if (state.itemId !== itemId) {
        clearTimeout(state.timer);
        state = { itemId, armed: false, pending: false, timer: null };
    }
    const root = document.createElement('div');
    root.className = 'stop-song';
    root.innerHTML = `
        <button type="button" id="stop-song-button" class="stop-song-button"
                onclick="stopSongTap()">Stop my song</button>
    `;
    container.appendChild(root);
    paint();
}

export async function stopSongTap() {
    if (state.pending || state.itemId === null) return;
    if (!state.armed) {
        state.armed = true;
        paint();
        state.timer = setTimeout(disarm, STOP_CONFIRM_MS);
        return;
    }
    disarm();
    state.pending = true;
    paint();
    try {
        const response = await fetch('/api/playback/stop-song', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: state.itemId }),
        });
        if (response.ok) {
            showToast('Song stopped');
        } else {
            const error = await response.json().catch(() => ({}));
            const detail = typeof error.detail === 'string' ? error.detail : 'try again';
            showToast('Could not stop the song: ' + detail, { error: true });
        }
    } catch (e) {
        showToast('Could not stop the song', { error: true });
    } finally {
        state.pending = false;
        paint();
    }
}
