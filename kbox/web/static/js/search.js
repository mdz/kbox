/**
 * Search and add song functions for kbox web UI.
 */

import {
    userName, userId, currentVideoToAdd, setCurrentVideoToAdd,
    queueDepthSeconds, queueDepthCount, currentQueue,
    currentSelectionTarget, setCurrentSelectionTarget
} from './state.js';
import { renderSongSettings } from './song-settings.js';
import { loadQueue, updateReplaceTargetBanner } from './queue.js';
import { renderFavoriteStar, bindFavoriteStar } from './favorites.js';

// Note: window.fetch is patched in auth.js to retry once on a 401/403 to
// /api/* if the session's identity got clobbered — no special handling
// needed here.

// Render a list of tappable search-result rows into a container.
// Used for search results and suggestions - selecting a row always goes
// through showAddSongModal, which adds or replaces depending on whether
// a replace target is currently set (see state.js: currentSelectionTarget).
function renderResultsList(container, videos) {
    container.innerHTML = '';
    videos.forEach(video => {
        const div = document.createElement('div');
        div.className = 'search-result';
        div.tabIndex = 0;
        div.setAttribute('role', 'button');
        div.setAttribute('aria-label', `Select ${video.title} by ${video.channel}`);
        div.innerHTML = `
            <img src="${video.thumbnail}" alt="${video.title}" />
            <div class="search-result-info">
                <div class="search-result-title">${video.title}</div>
                <div class="search-result-channel">${video.channel}</div>
            </div>
            ${renderFavoriteStar(video)}
        `;
        div.onclick = () => showAddSongModal(video);
        div.onkeydown = (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                showAddSongModal(video);
            }
        };
        bindFavoriteStar(div.querySelector('.favorite-star'), video);
        container.appendChild(div);
    });
}

// Get AI-powered song suggestions
export async function getSuggestions() {
    if (!userName) {
        alert('Please enter your name first');
        document.getElementById('name-modal').classList.remove('hidden');
        return;
    }

    const resultsDiv = document.getElementById('search-results');
    const suggestButton = document.getElementById('suggest-button');

    // Show loading state
    resultsDiv.innerHTML = '<div class="suggestions-loading">✨ Finding songs for you...</div>';
    suggestButton.disabled = true;
    suggestButton.textContent = '✨ Thinking...';

    try {
        const response = await fetch(`/api/suggestions?user_id=${encodeURIComponent(userId)}`);

        if (!response.ok) {
            const error = await response.json();
            resultsDiv.innerHTML = `<div class="suggestions-error">${error.detail || 'Could not get suggestions'}</div>`;
            return;
        }

        const data = await response.json();

        if (!data.results || data.results.length === 0) {
            resultsDiv.innerHTML = '<div class="suggestions-empty">No suggestions found. Try searching for a song!</div>';
            return;
        }

        // Display results (same format as search results)
        resultsDiv.innerHTML = '<div class="suggestions-header">✨ Suggested for you</div>';
        const listDiv = document.createElement('div');
        resultsDiv.appendChild(listDiv);
        renderResultsList(listDiv, data.results);
    } catch (e) {
        console.error('Error getting suggestions:', e);
        resultsDiv.innerHTML = '<div class="suggestions-error">Error getting suggestions. Try again later.</div>';
    } finally {
        suggestButton.disabled = false;
        suggestButton.textContent = '✨ Suggest for me';
    }
}

// Guard against duplicate search calls (e.g. touchend + form submit)
let _searchInFlight = false;

// Search for videos
export async function search() {
    if (_searchInFlight) return;

    const query = document.getElementById('search-input').value;

    if (!query) {
        alert('Please enter a search query');
        return;
    }

    if (!userName) {
        alert('Please enter your name first');
        document.getElementById('name-modal').classList.remove('hidden');
        return;
    }

    const resultsDiv = document.getElementById('search-results');
    resultsDiv.innerHTML = 'Searching...';

    _searchInFlight = true;
    try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        const data = await response.json();

        renderResultsList(resultsDiv, data.results);
    } catch (e) {
        resultsDiv.innerHTML = '<div class="error">Error searching</div>';
    } finally {
        _searchInFlight = false;
    }
}

// Show add song confirmation modal
export async function showAddSongModal(video) {
    if (!userName) {
        alert('Please enter your name first');
        document.getElementById('name-modal').classList.remove('hidden');
        return;
    }

    setCurrentVideoToAdd(video);

    const titleEl = document.getElementById('add-song-modal-title');
    const confirmBtn = document.getElementById('add-song-modal-confirm');

    let additionalControls;
    let savedPitch = 0;

    if (currentSelectionTarget) {
        // Replacing an existing queue item - no pitch preset lookup or queue
        // depth needed, just make it unmistakable what's being replaced.
        titleEl.textContent = 'Replace Song';
        confirmBtn.textContent = 'Replace';
        additionalControls = `<div style="color: #aaa; font-size: 0.9em; margin-top: 8px; padding: 8px 12px; background: rgba(255, 255, 255, 0.05); border-radius: 6px; text-align: center;">Replacing “${currentSelectionTarget.label}” for ${currentSelectionTarget.userName}</div>`;
    } else {
        titleEl.textContent = 'Add to Queue';
        confirmBtn.textContent = 'Add to Queue';

        // Fetch saved settings for this video (pitch preset, etc.) - identity
        // comes from the session, not a query param.
        try {
            const settingsResponse = await fetch(`/api/queue/settings/${encodeURIComponent(video.id)}`);
            if (settingsResponse.ok) {
                const settingsData = await settingsResponse.json();
                savedPitch = settingsData.settings?.pitch_semitones || 0;
            }
        } catch (e) {
            // If fetch fails, just use default 0
            console.debug('Could not fetch saved settings:', e);
        }

        // Build queue depth display from backend-computed values
        if (queueDepthCount === 0) {
            additionalControls = '<div style="color: #4aff6e; font-size: 0.9em; margin-top: 8px; padding: 8px 12px; background: rgba(74, 255, 110, 0.08); border-radius: 6px; text-align: center;">Queue is empty — your song will play next!</div>';
        } else {
            const minutes = Math.round(queueDepthSeconds / 60);
            const timeStr = minutes < 1 ? 'less than a minute' : minutes === 1 ? '~1 minute' : `~${minutes} minutes`;
            const songStr = queueDepthCount === 1 ? '1 song' : `${queueDepthCount} songs`;
            additionalControls = `<div style="color: #aaa; font-size: 0.9em; margin-top: 8px; padding: 8px 12px; background: rgba(255, 255, 255, 0.05); border-radius: 6px; text-align: center;">&#x23F3; ${songStr} ahead (${timeStr})</div>`;
        }
    }

    // Use reusable song settings component with saved pitch.
    // Pitch is left as-is when replacing (it isn't part of what's being
    // swapped), so the pitch control is hidden in that case.
    renderSongSettings('add-song-modal-content', {
        title: video.title,
        thumbnail_url: video.thumbnail,
        channel: video.channel,
        duration_seconds: video.duration_seconds,
        pitch_semitones: savedPitch
    }, {
        context: 'add-song',
        live: false,
        showStatus: false,
        showThumbnail: true,
        showUser: true,
        showPitchControls: !currentSelectionTarget,
        additionalControls: additionalControls
    });

    // Show modal
    const modal = document.getElementById('add-song-modal');
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
}

// Cancel adding/replacing song
export function cancelAddToQueue() {
    const modal = document.getElementById('add-song-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
    setCurrentVideoToAdd(null);
    setCurrentSelectionTarget(null);
    updateReplaceTargetBanner();
}

// Confirm and add (or replace) a song in the queue.
// Same guardrails and submit path either way - only the endpoint/payload differ.
export async function confirmAddToQueue() {
    if (!currentVideoToAdd) return;

    const replacing = currentSelectionTarget;

    // Warn if song was already added tonight (queued or already played)
    const duplicate = currentQueue?.find(item => item.video_id === currentVideoToAdd.id);
    if (duplicate) {
        const msg = duplicate.is_played
            ? `"${currentVideoToAdd.title}" was already performed tonight. Add it again?`
            : `"${currentVideoToAdd.title}" is already in the queue. Add it again?`;
        if (!confirm(msg)) {
            return;
        }
    }

    // Warn if song exceeds the long song threshold
    const warningMinutes = window.kboxConfig?.longSongWarningMinutes || 0;
    const durationSeconds = currentVideoToAdd.duration_seconds || 0;
    if (warningMinutes > 0 && durationSeconds > warningMinutes * 60) {
        if (!confirm(`This song is ${Math.floor(durationSeconds / 60)}:${String(Math.round(durationSeconds) % 60).padStart(2, '0')} long. Are you sure you want to add it?`)) {
            return;
        }
    }

    // Soft etiquette nudge: give everyone a turn. Doesn't apply when
    // replacing an existing entry in place (see #88) — that's correcting
    // a mistake, not queueing a second turn.
    if (!replacing && window.kboxConfig?.duplicateSingerNudgeEnabled) {
        const alreadyQueued = currentQueue?.some(item => item.user_id === userId && !item.is_played);
        if (alreadyQueued && !confirm('You already have a song queued. Add another anyway?')) {
            return;
        }
    }

    let url, body;
    if (replacing) {
        url = `/api/queue/${replacing.itemId}/replace`;
        body = {
            video_id: currentVideoToAdd.id,
            title: currentVideoToAdd.title,
            duration_seconds: currentVideoToAdd.duration_seconds,
            thumbnail_url: currentVideoToAdd.thumbnail
        };
    } else {
        const pitchInput = document.getElementById('add-song-pitch-input');
        if (!pitchInput) {
            alert('Pitch control not initialized');
            return;
        }
        url = '/api/queue';
        body = {
            user_id: userId,
            video_id: currentVideoToAdd.id,
            title: currentVideoToAdd.title,
            duration_seconds: currentVideoToAdd.duration_seconds,
            thumbnail_url: currentVideoToAdd.thumbnail,
            pitch_semitones: parseInt(pitchInput.value) || 0
        };
    }

    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });

        if (response.ok) {
            const modal = document.getElementById('add-song-modal');
            modal.classList.add('hidden');
            modal.style.display = 'none';
            document.getElementById('search-input').value = '';
            document.getElementById('search-results').innerHTML = '';
            loadQueue();
            setCurrentVideoToAdd(null);
            setCurrentSelectionTarget(null);
            updateReplaceTargetBanner();
        } else {
            // Try to get error detail from response
            let errorMessage = replacing ? 'Error replacing song' : 'Error adding song to queue';
            try {
                const errorData = await response.json();
                if (errorData.detail) {
                    errorMessage = errorData.detail;
                }
            } catch (parseError) {
                // Ignore parse errors, use default message
            }
            alert(errorMessage);
        }
    } catch (e) {
        alert(replacing ? 'Error replacing song' : 'Error adding song to queue');
    }
}

// Set up search input handlers
export function setupSearchHandlers() {
    // Add Enter key support for search input
    document.getElementById('search-input').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            search();
        }
    });

    // iOS Safari fix: add touchend handler for search button
    // This helps when the keyboard is open and click events don't fire properly
    document.getElementById('search-button').addEventListener('touchend', function(e) {
        e.preventDefault();
        search();
    });
}
