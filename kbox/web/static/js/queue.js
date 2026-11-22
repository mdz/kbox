/**
 * Queue management functions for kbox web UI.
 */

import {
    isOperator, userId,
    currentQueue, setCurrentQueue,
    currentQueueItemToEdit, setCurrentQueueItemToEdit,
    setQueueDepthSeconds, setQueueDepthCount,
    currentSelectionTarget, setCurrentSelectionTarget
} from './state.js';
import { renderSongSettings } from './song-settings.js';
import { escapeHtml } from './utils.js';
import { updatePitchDisplay, updatePitchButtons } from './pitch.js';
import { showToast } from './toast.js';
import { updatePlayPauseButton, renderNowPlaying, renderUpNext } from './playback.js';

// Check if playback controls are open
export function arePlaybackControlsOpen() {
    const screen = document.getElementById('operator-controls-screen');
    return screen && !screen.classList.contains('hidden');
}

// Show edit queue item modal
export function showEditQueueItemModal(item) {
    // Check if user can edit this song
    const canEdit = isOperator || (userId && item.user_id === userId);

    // Other guests' songs are a surprise: never reveal their title.
    if (!canEdit) return;

    setCurrentQueueItemToEdit(item);
    lastSavedPitch = { itemId: item.id, value: item.pitch_semitones || 0 };
    pendingPitch = null;
    resetRemoveConfirm();

    // Use reusable song settings component
    const additionalControls = !canEdit ? '<div style="color: #e74c3c; font-size: 0.9em; margin-top: 8px;">⚠️ You can only edit songs you added</div>' : '';

    // Format display title: prefer extracted artist/song
    const hasExtracted = item.artist && item.song_name;
    const displayTitle = hasExtracted ? `${item.song_name} by ${item.artist}` : item.title;

    renderSongSettings('edit-queue-item-content', {
        title: displayTitle,
        original_title: hasExtracted ? item.title : null,  // Show original as secondary if we have extracted
        thumbnail_url: item.thumbnail_url,
        user_id: item.user_id,
        user_name: item.user_name,
        duration_seconds: item.duration_seconds,
        content_status: item.content_status,
        pitch_semitones: item.pitch_semitones || 0
    }, {
        context: 'edit-queue-item',
        live: false,
        showStatus: true,
        showThumbnail: true,
        showUser: true,
        additionalControls: additionalControls
    });

    // Enable/disable buttons based on permissions
    if (!canEdit) {
        const minusBtn = document.getElementById('edit-queue-item-pitch-minus');
        const plusBtn = document.getElementById('edit-queue-item-pitch-plus');
        if (minusBtn) minusBtn.disabled = true;
        if (plusBtn) plusBtn.disabled = true;
    }

    // Save button is in the modal footer, handled separately

    // Show/hide options based on ownership and operator status
    const ownerOptions = document.getElementById('edit-queue-item-owner-options');
    const operatorOptions = document.getElementById('edit-queue-item-operator-options');
    const isOwnSong = userId && item.user_id === userId;

    // Owner options visible to song owner OR operator (superset principle)
    ownerOptions.style.display = (isOwnSong || isOperator) ? 'block' : 'none';

    // Operator-only options visible only to operators
    operatorOptions.style.display = isOperator ? 'block' : 'none';

    // Enable/disable "Jump to Song" button based on download status
    if (isOperator) {
        const jumpToButton = document.getElementById('jump-to-button');
        if (jumpToButton) {
            if (item.content_status === 'ready') {
                jumpToButton.disabled = false;
                jumpToButton.style.opacity = '1';
            } else {
                jumpToButton.disabled = true;
                jumpToButton.style.opacity = '0.5';
                jumpToButton.title = 'Song must be ready to play';
            }
        }
    }

    // Show modal
    const modal = document.getElementById('edit-queue-item-modal');
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
}

// Cancel editing queue item
export function cancelEditQueueItem() {
    // Flush before clearing currentQueueItemToEdit; the PATCH uses the
    // captured item id so it still lands.
    if (pendingPitch) flushPitch();
    resetRemoveConfirm();
    const modal = document.getElementById('edit-queue-item-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
    setCurrentQueueItemToEdit(null);
}

// Pitch edits apply immediately (like the now-playing control and like
// Replace/Remove), so the dialog has no Save step. Taps are debounced into one
// PATCH; closing the dialog flushes any pending change.
const PITCH_DEBOUNCE_MS = 400;
let pitchTimer = null;
let pendingPitch = null;      // {itemId, value} not yet sent
let lastSavedPitch = null;    // {itemId, value} last value the server has

export function queueEditPitchChanged() {
    if (!currentQueueItemToEdit) return;
    const input = document.getElementById('edit-queue-item-pitch-input');
    if (!input) return;
    pendingPitch = { itemId: currentQueueItemToEdit.id, value: parseInt(input.value) || 0 };
    clearTimeout(pitchTimer);
    pitchTimer = setTimeout(flushPitch, PITCH_DEBOUNCE_MS);
}

async function flushPitch() {
    clearTimeout(pitchTimer);
    pitchTimer = null;
    const pending = pendingPitch;
    pendingPitch = null;
    if (!pending) return;
    if (lastSavedPitch && lastSavedPitch.itemId === pending.itemId && lastSavedPitch.value === pending.value) return;

    try {
        const response = await fetch(`/api/queue/${pending.itemId}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ pitch_semitones: pending.value, user_id: userId })
        });
        if (response.ok) {
            lastSavedPitch = pending;
            loadQueue();
        } else {
            const error = await response.json().catch(() => ({}));
            showToast('Could not change pitch: ' + (error.detail || 'unknown error'), {error: true});
            revertPitchDisplay(pending.itemId);
        }
    } catch (e) {
        showToast('Could not change pitch', {error: true});
        revertPitchDisplay(pending.itemId);
    }
}

// Put the dialog back to the last value the server accepted, if still open.
function revertPitchDisplay(itemId) {
    if (!currentQueueItemToEdit || currentQueueItemToEdit.id !== itemId) return;
    const value = (lastSavedPitch && lastSavedPitch.itemId === itemId)
        ? lastSavedPitch.value
        : (currentQueueItemToEdit.pitch_semitones || 0);
    const input = document.getElementById('edit-queue-item-pitch-input');
    if (input) input.value = value;
    updatePitchDisplay('edit-queue-item-pitch-display', value);
    updatePitchButtons('edit-queue-item', value);
}

// Jump to song (operator only) - immediately start playing the song at its current queue position
export async function jumpToQueueItem() {
    if (!currentQueueItemToEdit) return;

    if (currentQueueItemToEdit.content_status !== 'ready') {
        alert('Song must be ready (downloaded) before it can be played');
        return;
    }

    try {
        const response = await fetch(`/api/playback/jump/${currentQueueItemToEdit.id}`, {method: 'POST'});
        if (response.ok) {
            cancelEditQueueItem();
            loadQueue();
        } else {
            const error = await response.json();
            alert('Error: ' + error.detail);
        }
    } catch (e) {
        alert('Error playing song');
    }
}

// Play next (operator only)
export async function playNextQueueItem() {
    if (!currentQueueItemToEdit) return;

    try {
        const response = await fetch(`/api/queue/${currentQueueItemToEdit.id}/play-next`, {method: 'POST'});
        if (response.ok) {
            cancelEditQueueItem();
            loadQueue();
        } else {
            const error = await response.json();
            alert('Error: ' + error.detail);
        }
    } catch (e) {
        alert('Error moving song to play next');
    }
}

// Move to end (operator only)
export async function moveToEndQueueItem() {
    if (!currentQueueItemToEdit) return;

    try {
        const response = await fetch(`/api/queue/${currentQueueItemToEdit.id}/move-to-end`, {method: 'POST'});
        if (response.ok) {
            cancelEditQueueItem();
            loadQueue();
        } else {
            const error = await response.json();
            alert('Error: ' + error.detail);
        }
    } catch (e) {
        alert('Error moving song to end');
    }
}

// Replace song (works for song owner or operator) - sends the operator/user
// to the search box to pick a replacement; the next result they select
// replaces this item instead of adding a new one.
export function replaceQueueItem() {
    if (!currentQueueItemToEdit) return;

    const item = currentQueueItemToEdit;
    const label = (item.artist && item.song_name)
        ? `${item.song_name} by ${item.artist}`
        : item.title;

    setCurrentSelectionTarget({ itemId: item.id, label, userName: item.user_name });
    updateReplaceTargetBanner();
    cancelEditQueueItem();

    const searchInput = document.getElementById('search-input');
    if (searchInput) {
        searchInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
        searchInput.focus();
    }
}

// Keep the "Replacing: X" banner above the search box in sync with
// currentSelectionTarget. Called whenever that state changes (set on
// replaceQueueItem, cleared on confirm/cancel of the add-song modal) so the
// gap between tapping "Replace Song" and picking a result is never silent.
export function updateReplaceTargetBanner() {
    const banner = document.getElementById('replace-target-banner');
    if (!banner) return;

    if (currentSelectionTarget) {
        banner.textContent = `Replacing "${currentSelectionTarget.label}" — search for a new song`;
        banner.classList.remove('hidden');
    } else {
        banner.classList.add('hidden');
    }
}

const REMOVE_CONFIRM_MS = 4000;
let removeArmed = false;
let removeTimer = null;

function resetRemoveConfirm() {
    removeArmed = false;
    clearTimeout(removeTimer);
    removeTimer = null;
    const btn = document.getElementById('remove-queue-item-button');
    if (btn) btn.textContent = 'Remove from Queue';
}

// Remove from queue (works for song owner or operator)
export async function removeQueueItem() {
    if (!currentQueueItemToEdit) return;

    // Two-step confirm instead of a native confirm(): first tap arms the
    // button, a second tap within a few seconds removes.
    const btn = document.getElementById('remove-queue-item-button');
    if (!removeArmed) {
        removeArmed = true;
        if (btn) btn.textContent = 'Tap again to remove';
        removeTimer = setTimeout(resetRemoveConfirm, REMOVE_CONFIRM_MS);
        return;
    }
    resetRemoveConfirm();

    try {
        // Always pass user_id - server allows if operator OR if user owns the song
        const response = await fetch(`/api/queue/${currentQueueItemToEdit.id}?user_id=${encodeURIComponent(userId)}`, {method: 'DELETE'});
        if (response.ok) {
            cancelEditQueueItem();
            loadQueue();
        } else {
            const error = await response.json();
            showToast('Could not remove song: ' + (error.detail || 'unknown error'), {error: true});
        }
    } catch (e) {
        showToast('Could not remove song', {error: true});
    }
}

// Move up in queue - operator only
export async function moveUpQueueItem() {
    if (!currentQueueItemToEdit) return;

    try {
        const response = await fetch(`/api/queue/${currentQueueItemToEdit.id}/move-up`, {method: 'POST'});
        if (response.ok) {
            cancelEditQueueItem();
            loadQueue();
        } else {
            const error = await response.json();
            alert('Error: ' + error.detail);
        }
    } catch (e) {
        alert('Error moving song up');
    }
}

// Move down in queue - operator only
export async function moveDownQueueItem() {
    if (!currentQueueItemToEdit) return;

    try {
        const response = await fetch(`/api/queue/${currentQueueItemToEdit.id}/move-down`, {method: 'POST'});
        if (response.ok) {
            cancelEditQueueItem();
            loadQueue();
        } else {
            const error = await response.json();
            alert('Error: ' + error.detail);
        }
    } catch (e) {
        alert('Error moving song down');
    }
}


// Clear queue
export async function clearQueue() {
    if (!isOperator) {
        alert('Operator authentication required');
        return;
    }

    if (!confirm('Clear entire queue?')) return;

    try {
        const response = await fetch('/api/queue/clear', {method: 'POST'});
        if (response.ok) {
            loadQueue();
        } else {
            alert('Error clearing queue');
        }
    } catch (e) {
        alert('Error clearing queue');
    }
}

// Set once a 401 is seen, so loadQueue stops making requests instead of
// retrying forever on every 1-second poll tick.
let authExpired = false;

function handleAuthExpired() {
    authExpired = true;
    const queueDiv = document.getElementById('queue-list');
    if (queueDiv) {
        queueDiv.innerHTML = `
            <div style="text-align: center; color: #e74c3c; padding: 20px;">
                <p>⚠️ Not authenticated.</p>
                <p>Scan the QR code on the TV to (re)join, or <a href="/" style="color: #4a9eff;">reload the page</a>.</p>
            </div>`;
    }
}

// Guest-friendly labels for content_status values (see QueueManager.STATUS_*)
const STATUS_LABELS = {
    pending: 'Getting ready…',
    preparing: 'Getting ready…',
    ready: 'Ready',
    error: 'Problem',
};

// At most one queue refresh in flight per page. Every phone polls each
// second, and each request holds a server worker thread until it answers;
// if the server is slow, overlapping polls would pile up there.
let queueRefreshInFlight = false;
// Set when loadQueue() was asked for while a refresh was in flight.
let queueRefreshWanted = false;
// Give up on a refresh after this long. Polls wait for the one in flight,
// so without a limit one hung request (a phone changing networks) would
// stop that phone's updates until the browser itself gave up.
const QUEUE_REFRESH_TIMEOUT_MS = 10000;

// The once-a-second poll: skip the tick if the last one hasn't answered.
export function pollQueue() {
    if (!queueRefreshInFlight) loadQueue();
}

// Refresh the queue now, e.g. after an action changed it. If a refresh is
// already in flight, its answer may predate the change, so run one more
// as soon as it finishes instead of overlapping it.
export async function loadQueue() {
    if (queueRefreshInFlight) {
        queueRefreshWanted = true;
        return;
    }
    queueRefreshInFlight = true;
    try {
        do {
            queueRefreshWanted = false;
            const abort = new AbortController();
            const timer = setTimeout(() => abort.abort(), QUEUE_REFRESH_TIMEOUT_MS);
            try {
                await refreshQueue(abort.signal);
            } finally {
                clearTimeout(timer);
            }
        } while (queueRefreshWanted);
    } finally {
        queueRefreshInFlight = false;
    }
}

async function refreshQueue(signal) {
    if (authExpired) return;
    try {
        // Get queue and current playback status
        const [queueResponse, statusResponse] = await Promise.all([
            fetch('/api/queue', { signal }),
            fetch('/api/playback/status', { signal })
        ]);

        if (queueResponse.status === 401 || statusResponse.status === 401) {
            handleAuthExpired();
            return;
        }

        const queueData = await queueResponse.json();
        const statusData = await statusResponse.json();
        setCurrentQueue(queueData.queue);
        setQueueDepthSeconds(queueData.queue_depth_seconds || 0);
        setQueueDepthCount(queueData.queue_depth_count || 0);

        // Update play/pause toggle button
        updatePlayPauseButton(statusData.state);

        // Render Now Playing section
        renderNowPlaying(statusData);

        // Render Up Next section (backend provides next_song and my_next_turn - single source of truth)
        renderUpNext(statusData, queueData.next_song, queueData.my_next_turn);

        // Render Clear Queue button (operator only)
        const clearQueueContainer = document.getElementById('clear-queue-button-container');
        if (clearQueueContainer) {
            if (isOperator && currentQueue.length > 0) {
                clearQueueContainer.innerHTML = '<button onclick="clearQueue()" class="danger" style="padding: 8px 16px;">Clear Queue</button>';
            } else {
                clearQueueContainer.innerHTML = '';
            }
        }

        // Render queue
        const queueDiv = document.getElementById('queue-list');

        if (currentQueue.length === 0) {
            queueDiv.innerHTML = '<div style="text-align: center; color: #666; padding: 20px;">Queue is empty. Use Search to add songs!</div>';
            return;
        }

        queueDiv.innerHTML = '';

        let position = 0;
        currentQueue.forEach(item => {
            const div = document.createElement('div');
            const isCurrent = item.is_current;
            const isPlayed = item.is_played && !isCurrent;
            const isMine = !!userId && item.user_id === userId;
            const canSeeTitle = isOperator || isMine;
            const canEdit = canSeeTitle;
            const isUpcoming = !isCurrent && !isPlayed;
            if (isUpcoming) position += 1;

            // Build class list based on state
            let classes = ['queue-item'];
            if (isCurrent) classes.push('playing');
            if (isPlayed) classes.push('played');
            if (isMine) classes.push('mine');
            if (!canEdit) classes.push('readonly');
            div.className = classes.join(' ');

            const statusClass = `status-${escapeHtml(item.content_status)}`;
            const statusLabel = escapeHtml(STATUS_LABELS[item.content_status] || item.content_status);
            const duration = item.duration_seconds || 0;
            const durationStr = `${Math.floor(duration / 60)}:${(duration % 60).toString().padStart(2, '0')}`;

            // Format song display: use extracted artist/song if available, otherwise title
            const hasExtracted = item.artist && item.song_name;
            const primaryDisplay = hasExtracted
                ? `<strong>${escapeHtml(item.song_name)}</strong> <span style="color: #888;">by ${escapeHtml(item.artist)}</span>`
                : escapeHtml(item.title);
            // The original YouTube title is extra detail for operators only
            const secondaryDisplay = hasExtracted && isOperator
                ? `<div class="queue-item-original" style="color: #666; font-size: 0.8em; margin-top: 2px;">${escapeHtml(item.title)}</div>`
                : '';

            const positionBadge = isUpcoming ? `<span class="queue-item-position">${position}</span>` : '';
            const youBadge = isMine ? '<span class="queue-item-you">You</span>' : '';

            div.innerHTML = `
                ${positionBadge}
                <div class="queue-item-info">
                    <div class="queue-item-user">${escapeHtml(item.user_name)}${youBadge} <span style="color: #666; font-size: 0.85em;">(${durationStr})</span></div>
                    ${canSeeTitle ? `<div class="queue-item-title">${primaryDisplay}</div>${secondaryDisplay}` : ''}
                </div>
                <span class="queue-item-status ${statusClass}">${statusLabel}</span>
            `;

            // Only the owner and operators can open the edit modal
            if (canEdit) div.onclick = () => showEditQueueItemModal(item);

            queueDiv.appendChild(div);
        });
    } catch (e) {
        console.error('Error loading queue:', e);
    }
}
