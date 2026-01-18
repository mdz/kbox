/**
 * Queue management functions for kbox web UI.
 */

import {
    isOperator, userId,
    currentQueue, setCurrentQueue,
    currentQueueItemToEdit, setCurrentQueueItemToEdit
} from './state.js';
import { renderSongSettings } from './song-settings.js';
import { updatePlayPauseButton, renderNowPlaying, renderUpNext } from './playback.js';

// Check if playback controls are open
export function arePlaybackControlsOpen() {
    const screen = document.getElementById('operator-controls-screen');
    return screen && !screen.classList.contains('hidden');
}

// Show edit queue item modal
export function showEditQueueItemModal(item) {
    setCurrentQueueItemToEdit(item);

    // Check if user can edit this song
    const canEdit = isOperator || (userId && item.user_id === userId);

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
        download_status: item.download_status,
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

    // Show/hide operator options based on operator status
    const operatorOptions = document.getElementById('edit-queue-item-operator-options');
    const userOptions = document.getElementById('edit-queue-item-user-options');
    const isOwnSong = userId && item.user_id === userId;

    if (isOperator) {
        operatorOptions.style.display = 'block';
        userOptions.style.display = 'none'; // Operators use the full operator controls

        // Enable/disable "Jump to Song" button based on download status
        const jumpToButton = document.getElementById('jump-to-button');
        if (jumpToButton) {
            if (item.download_status === 'ready') {
                jumpToButton.disabled = false;
                jumpToButton.style.opacity = '1';
            } else {
                jumpToButton.disabled = true;
                jumpToButton.style.opacity = '0.5';
                jumpToButton.title = 'Song must be ready to play';
            }
        }
    } else {
        operatorOptions.style.display = 'none';
        // Show user options (remove own song) only for their own songs
        userOptions.style.display = isOwnSong ? 'block' : 'none';
    }

    // Show modal
    const modal = document.getElementById('edit-queue-item-modal');
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
}

// Cancel editing queue item
export function cancelEditQueueItem() {
    const modal = document.getElementById('edit-queue-item-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
    setCurrentQueueItemToEdit(null);
}

// Save queue item pitch
export async function saveQueueItemPitch() {
    if (!currentQueueItemToEdit) return;

    // Check permissions
    const canEdit = isOperator || (userId && currentQueueItemToEdit.user_id === userId);
    if (!canEdit) {
        alert('You can only edit songs you added');
        return;
    }

    const pitchInput = document.getElementById('edit-queue-item-pitch-input');
    const pitchSemitones = parseInt(pitchInput.value) || 0;

    try {
        const response = await fetch(`/api/queue/${currentQueueItemToEdit.id}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                pitch_semitones: pitchSemitones,
                user_id: userId
            })
        });

        if (response.ok) {
            cancelEditQueueItem();
            loadQueue();
        } else {
            const error = await response.json();
            alert('Error: ' + error.detail);
        }
    } catch (e) {
        alert('Error updating pitch');
    }
}

// Jump to song (operator only) - immediately start playing the song at its current queue position
export async function jumpToQueueItem() {
    if (!currentQueueItemToEdit) return;

    if (currentQueueItemToEdit.download_status !== 'ready') {
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

// Remove from queue (operator only)
export async function removeQueueItem() {
    if (!currentQueueItemToEdit) return;

    // Use extracted song name if available, otherwise title
    const displayName = (currentQueueItemToEdit.artist && currentQueueItemToEdit.song_name)
        ? `${currentQueueItemToEdit.song_name} by ${currentQueueItemToEdit.artist}`
        : currentQueueItemToEdit.title;
    if (!confirm(`Remove "${displayName}" from queue?`)) return;

    try {
        const response = await fetch(`/api/queue/${currentQueueItemToEdit.id}`, {method: 'DELETE'});
        if (response.ok) {
            cancelEditQueueItem();
            loadQueue();
        } else {
            const error = await response.json();
            alert('Error: ' + error.detail);
        }
    } catch (e) {
        alert('Error removing song');
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

// Remove own song from queue - for regular users
export async function removeOwnQueueItem() {
    if (!currentQueueItemToEdit) return;

    // Use extracted song name if available, otherwise title
    const displayName = (currentQueueItemToEdit.artist && currentQueueItemToEdit.song_name)
        ? `${currentQueueItemToEdit.song_name} by ${currentQueueItemToEdit.artist}`
        : currentQueueItemToEdit.title;
    if (!confirm(`Remove "${displayName}" from queue?`)) return;

    try {
        const response = await fetch(`/api/queue/${currentQueueItemToEdit.id}?user_id=${encodeURIComponent(userId)}`, {method: 'DELETE'});
        if (response.ok) {
            cancelEditQueueItem();
            loadQueue();
        } else {
            const error = await response.json();
            alert('Error: ' + error.detail);
        }
    } catch (e) {
        alert('Error removing song');
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

// Load queue
export async function loadQueue() {
    try {
        // Get queue and current playback status
        const [queueResponse, statusResponse] = await Promise.all([
            fetch('/api/queue'),
            fetch('/api/playback/status')
        ]);

        const queueData = await queueResponse.json();
        const statusData = await statusResponse.json();
        setCurrentQueue(queueData.queue);

        // Update play/pause toggle button
        updatePlayPauseButton(statusData.state);

        // Render Now Playing section
        renderNowPlaying(statusData);

        // Render Up Next section (backend provides next_song - single source of truth)
        renderUpNext(statusData, queueData.next_song);

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

        currentQueue.forEach(item => {
            const div = document.createElement('div');
            const isCurrent = item.is_current;
            const isPlayed = item.is_played && !isCurrent;

            // Build class list based on state
            let classes = ['queue-item'];
            if (isCurrent) classes.push('playing');
            if (isPlayed) classes.push('played');
            div.className = classes.join(' ');

            const statusClass = `status-${item.download_status}`;
            const duration = item.duration_seconds || 0;
            const durationStr = `${Math.floor(duration / 60)}:${(duration % 60).toString().padStart(2, '0')}`;

            // Format song display: use extracted artist/song if available, otherwise title
            const hasExtracted = item.artist && item.song_name;
            const primaryDisplay = hasExtracted
                ? `<strong>${item.song_name}</strong> <span style="color: #888;">by ${item.artist}</span>`
                : item.title;
            const secondaryDisplay = hasExtracted
                ? `<div class="queue-item-original" style="color: #666; font-size: 0.8em; margin-top: 2px;">${item.title}</div>`
                : '';

            div.innerHTML = `
                <div class="queue-item-info">
                    <div class="queue-item-user">${item.user_name} <span style="color: #666; font-size: 0.85em;">(${durationStr})</span></div>
                    ${isOperator ? `<div class="queue-item-title">${primaryDisplay}</div>${secondaryDisplay}` : ''}
                </div>
                <span class="queue-item-status ${statusClass}">${item.download_status}</span>
            `;

            // Make all queue items clickable to edit
            div.onclick = () => showEditQueueItemModal(item);

            queueDiv.appendChild(div);
        });
    } catch (e) {
        console.error('Error loading queue:', e);
    }
}
