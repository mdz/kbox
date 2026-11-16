/**
 * "Want another go after Bob?" after a singer stopped their own song.
 *
 * Stopping never brings the song back by itself: a singer who bailed out,
 * maybe embarrassed, shouldn't be surprised by the same track coming round
 * again. Instead their phone offers another go, after whoever is on now:
 *
 * - Same song: puts the stopped song back (POST /api/playback/another-go).
 * - Different song: opens search; whatever they pick replaces the stopped
 *   song, which then goes back the same way (see confirmAddToQueue).
 * - No thanks, or ignoring it: their turn is over.
 *
 * The offer lasts until it's used or dismissed, or one of this guest's songs
 * starts playing. It's remembered across a reload in localStorage, which is
 * only a convenience: the server decides whether the song may come back.
 */

import { escapeHtml } from './utils.js';
import { showToast } from './toast.js';
import { userId, userName, setCurrentSelectionTarget } from './state.js';
import { updateReplaceTargetBanner } from './queue.js';

const STORAGE_KEY = 'kbox.anotherGo';

let offer = load();
let renderedName;  // undefined until first render; null means "nobody named"

function load() {
    try {
        const value = JSON.parse(localStorage.getItem(STORAGE_KEY));
        return value && Number.isInteger(value.id) ? value : null;
    } catch (e) {
        return null;
    }
}

function save(value) {
    offer = value;
    try {
        if (value) localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
        else localStorage.removeItem(STORAGE_KEY);
    } catch (e) {
        // Private mode etc.: the offer just won't survive a reload.
    }
}

function clearOffer() {
    save(null);
    renderedName = undefined;
    const card = document.getElementById('another-go');
    if (card) {
        card.hidden = true;
        card.innerHTML = '';
    }
}

// Pure: who the singer would go after, matching the server's rule: whoever
// is singing now, else whoever is up next; nobody if that's this guest.
export function anotherGoAfter(statusData, nextSong, myUserId) {
    const current = statusData && statusData.current_song;
    if (current) return current.user_id === myUserId ? null : current.user_name;
    if (nextSong && nextSong.user_id !== myUserId) return nextSong.user_name;
    return null;
}

// Called by stop-song.js once the song has stopped. song: {id, label}
export function offerAnotherGo(song) {
    save({ id: song.id, label: song.label });
    renderedName = undefined;
}

// Called on every queue/status poll.
export function renderAnotherGo(statusData, nextSong) {
    const card = document.getElementById('another-go');
    if (!card) return;
    if (!offer) {
        card.hidden = true;
        return;
    }
    const current = statusData && statusData.current_song;
    if (current && current.user_id === userId) {
        // One of their songs is on: the offer is spent (or moot).
        clearOffer();
        return;
    }

    const name = anotherGoAfter(statusData, nextSong, userId);
    card.hidden = false;
    if (renderedName === name && card.firstChild) return;  // keep the buttons
    renderedName = name;
    const question = name ? `Want another go after ${name}?` : 'Want another go?';
    card.innerHTML = `
        <div class="another-go-question">${escapeHtml(question)}</div>
        <div class="another-go-buttons">
            <button type="button" id="another-go-same" onclick="anotherGoSame()">Same song</button>
            <button type="button" id="another-go-different" onclick="anotherGoDifferent()">Different song</button>
        </div>
        <button type="button" id="another-go-dismiss" class="another-go-dismiss"
                onclick="anotherGoDismiss()">No thanks</button>
    `;
}

// Put the stopped song (already replaced, for a different song) back in.
// Returns true if it's back in the queue.
export async function requestAnotherGo(itemId, title) {
    const after = renderedName;
    try {
        const response = await fetch('/api/playback/another-go', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: itemId }),
        });
        if (response.ok) {
            clearOffer();
            const what = title ? ` with ${title}` : '';
            showToast(after ? `You're up after ${after}${what}` : `You're up next${what}`);
            return true;
        }
        const error = await response.json().catch(() => ({}));
        if (response.status === 409) clearOffer();
        const detail = typeof error.detail === 'string' ? error.detail : 'try again';
        showToast('Could not give you another go: ' + detail, { error: true });
    } catch (e) {
        showToast('Could not give you another go', { error: true });
    }
    return false;
}

export function anotherGoSame() {
    if (!offer) return;
    return requestAnotherGo(offer.id, null);
}

export function anotherGoDifferent() {
    if (!offer) return;
    setCurrentSelectionTarget({
        itemId: offer.id,
        label: offer.label,
        userName,
        anotherGo: true,
    });
    updateReplaceTargetBanner();
    const searchInput = document.getElementById('search-input');
    if (searchInput) {
        searchInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
        searchInput.focus();
    }
}

export function anotherGoDismiss() {
    clearOffer();
}
