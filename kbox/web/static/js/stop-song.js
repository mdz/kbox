/**
 * "Stop my song" for the singer's own song, when something's wrong (wrong
 * song, wrong version, bad track, not ready). Two taps, like Remove in the
 * queue editor. The request names the song the page saw playing, so a late
 * tap can't stop the next singer.
 */

import { showToast } from './toast.js';

const STOP_CONFIRM_MS = 4000;

let itemId = null;
let armed = false;
let timer = null;

function setArmed(value) {
    armed = value;
    clearTimeout(timer);
    if (armed) timer = setTimeout(() => setArmed(false), STOP_CONFIRM_MS);
    const btn = document.getElementById('stop-song-button');
    if (!btn) return;
    btn.textContent = armed ? 'Tap again: stops your song' : 'Stop my song';
    btn.classList.toggle('armed', armed);
}

export function renderStopSong(container, songId) {
    if (songId !== itemId) {
        itemId = songId;
        armed = false;
    }
    const root = document.createElement('div');
    root.className = 'stop-song';
    root.innerHTML = '<button type="button" id="stop-song-button" class="stop-song-button" onclick="stopSongTap()"></button>';
    container.appendChild(root);
    setArmed(armed);
}

export async function stopSongTap() {
    if (!armed) {
        setArmed(true);
        return;
    }
    setArmed(false);
    try {
        const response = await fetch('/api/playback/stop-song', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: itemId }),
        });
        if (!response.ok) throw new Error(response.status);
        showToast('Song stopped. Want another go? Ask the host.');
    } catch (e) {
        showToast('Could not stop the song', { error: true });
    }
}
