/**
 * "Need a minute? Let Bob go first" for the singer's own song.
 *
 * A singer takes their phone out mid-song only when something is wrong. If
 * it's the key, the key fix above handles it; for anything else (wrong song,
 * wrong version, a bad karaoke track, or just not ready) they need to say "I
 * can't go on with this right now". So this is one button, with no reasons to
 * pick from, naming who goes on instead. With nobody after them, it's simply
 * "Stop my song".
 *
 * Two taps, like Remove in the queue editor: the first arms the button, a
 * second within a few seconds stops the song. Nothing changes for the room
 * until that second tap.
 *
 * POST /api/playback/stop-song names the song the page saw playing, so a tap
 * that lands after the song ended can't stop the next singer. Stopping never
 * brings the song back by itself; another-go.js then offers the singer
 * another go.
 */

import { escapeHtml } from './utils.js';
import { showToast } from './toast.js';
import { offerAnotherGo } from './another-go.js';

export const STOP_CONFIRM_MS = 4000;

// Pure: the button's two lines for who (if anyone) goes on next.
export function stopSongLabel(nextSingerName, armed) {
    if (armed) return { ask: '', act: 'Tap again: stops your song' };
    if (!nextSingerName) return { ask: '', act: 'Stop my song' };
    return { ask: 'Need a minute?', act: `Let ${nextSingerName} go first` };
}

let state = { song: null, nextName: null, armed: false, pending: false, timer: null };

function paint() {
    const btn = document.getElementById('stop-song-button');
    if (!btn) return;
    const { ask, act } = stopSongLabel(state.nextName, state.armed);
    btn.innerHTML = (ask ? `<span class="stop-song-ask">${escapeHtml(ask)}</span>` : '') +
        `<span class="stop-song-act">${escapeHtml(act)}</span>`;
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
// song: {id, label}; nextSingerName: who goes on if they stop, or null.
export function renderStopSong(container, song, nextSingerName) {
    if (!state.song || state.song.id !== song.id) {
        clearTimeout(state.timer);
        state = { song, nextName: nextSingerName, armed: false, pending: false, timer: null };
    }
    state.nextName = nextSingerName;
    const root = document.createElement('div');
    root.className = 'stop-song';
    root.innerHTML = `
        <button type="button" id="stop-song-button" class="stop-song-button"
                onclick="stopSongTap()"></button>
    `;
    container.appendChild(root);
    paint();
}

// Refresh the next singer's name from a poll without rebuilding the button.
export function updateStopSong(nextSingerName) {
    if (state.nextName === nextSingerName) return;
    state.nextName = nextSingerName;
    paint();
}

export async function stopSongTap() {
    if (state.pending || !state.song) return;
    if (!state.armed) {
        state.armed = true;
        paint();
        state.timer = setTimeout(disarm, STOP_CONFIRM_MS);
        return;
    }
    disarm();
    state.pending = true;
    paint();
    const song = state.song;
    try {
        const response = await fetch('/api/playback/stop-song', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: song.id }),
        });
        if (response.ok) {
            const result = await response.json().catch(() => ({}));
            if (result.status === 'stopped') offerAnotherGo(song);
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
