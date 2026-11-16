/**
 * "End my turn" for the singer's own song, when something's wrong (wrong
 * song, wrong version, bad track, not ready). Two taps, like Remove in the
 * queue editor. The request names the song the page saw playing, so a late
 * tap can't end the next singer's turn.
 */

import { showToast } from './toast.js';

const CONFIRM_MS = 4000;

let itemId = null;
let armed = false;
let timer = null;

function setArmed(value) {
    armed = value;
    clearTimeout(timer);
    if (armed) timer = setTimeout(() => setArmed(false), CONFIRM_MS);
    const btn = document.getElementById('end-turn-button');
    if (!btn) return;
    btn.textContent = armed ? 'Tap again: ends your turn' : 'End my turn';
    btn.classList.toggle('armed', armed);
}

export function renderEndTurn(container, songId) {
    if (songId !== itemId) {
        itemId = songId;
        armed = false;
    }
    const root = document.createElement('div');
    root.className = 'end-turn';
    root.innerHTML = '<button type="button" id="end-turn-button" class="end-turn-button" onclick="endTurnTap()"></button>';
    container.appendChild(root);
    setArmed(armed);
}

export async function endTurnTap() {
    if (!armed) {
        setArmed(true);
        return;
    }
    setArmed(false);
    try {
        const response = await fetch('/api/playback/end-turn', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: itemId }),
        });
        if (!response.ok) throw new Error(response.status);
        showToast('Turn ended. Want another go? Ask the host.');
    } catch (e) {
        showToast('Could not end your turn', { error: true });
    }
}
