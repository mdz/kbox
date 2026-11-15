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
import { showToast } from './toast.js';
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

    // Whatever triggered the search (button, Enter, accepted suggestion),
    // the dropdown has served its purpose.
    closeSuggestions();

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
        // The guest gets a message either way; without this the cause was
        // discarded entirely.
        console.error('Search failed:', e);
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
// Work out where a just-added/replaced queue item sits and roughly when it
// will play, from the server's queue + playback status (the add/replace
// responses don't include this). Returns null if it can't be determined.
async function describeQueuePosition(itemId) {
    try {
        const [queueResponse, statusResponse] = await Promise.all([
            fetch('/api/queue'),
            fetch('/api/playback/status')
        ]);
        if (!queueResponse.ok || !statusResponse.ok) return null;
        const queueData = await queueResponse.json();
        const status = await statusResponse.json();

        const upcoming = queueData.queue.filter(item => !item.is_played && !item.is_current);
        const index = upcoming.findIndex(item => item.id === itemId);
        if (index === -1) return null;

        const current = status.current_song;
        let seconds = current
            ? Math.max(0, (current.duration_seconds || 0) - (status.position_seconds || 0))
            : 0;
        for (let i = 0; i < index; i++) {
            seconds += upcoming[i].duration_seconds || 0;
        }
        return {position: index + 1, minutes: Math.round(seconds / 60)};
    } catch (e) {
        return null;
    }
}

function positionSuffix(info) {
    if (!info) return '';
    if (info.position === 1) return ' — you\'re up next';
    const wait = info.minutes < 1 ? 'less than a minute' : `about ${info.minutes} min`;
    return ` — #${info.position} in the queue, ${wait}`;
}

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
            showToast('Pitch control not initialized', {error: true});
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
            const title = currentVideoToAdd.title;
            const addedId = replacing ? replacing.itemId : (await response.json()).id;
            loadQueue();
            setCurrentVideoToAdd(null);
            setCurrentSelectionTarget(null);
            updateReplaceTargetBanner();
            const verb = replacing ? 'Replaced with' : 'Added';
            showToast(`✓ ${verb} ${title}`);
            const info = await describeQueuePosition(addedId);
            if (info) showToast(`✓ ${verb} ${title}${positionSuffix(info)}`);
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
            showToast(errorMessage, {error: true});
        }
    } catch (e) {
        showToast(replacing ? 'Error replacing song' : 'Error adding song to queue', {error: true});
    }
}

// ===========================================================================
// Search box autocomplete
//
// Suggestions come from /api/search/suggest, which proxies YouTube's own
// query completions.  They are query strings, not videos: choosing one fills
// the box and runs a normal search.
//
// The whole feature is best-effort.  Any failure - disabled by the operator,
// upstream down, request aborted - results in no dropdown, which is exactly
// how the search box behaved before autocomplete existed.
// ===========================================================================

// A debounce alone behaves as a cliff, not a dial: it fires on every
// keystroke when the gap between keys exceeds it, and not once until typing
// stops when the gap is shorter. Measured by typing "bohemian rhapsody" at
// several cadences with a 350ms debounce and no cap:
//
//     120ms/key -> 1 request, dropdown after 2.3s   (i.e. only at the end)
//     250ms/key -> 1 request, dropdown after 4.4s
//     350ms/key -> 15 requests, dropdown after 1.1s
//
// Phone thumb-typing lands at 200-350ms between keys, straddling that edge,
// so the feature would behave completely differently for two guests typing
// at slightly different speeds — and for the faster one it would do nothing
// until they had already finished typing.
//
// So: debounce for the quiet period after typing stops, plus a maximum wait
// that forces a request during continuous typing. Responsiveness then comes
// from the cap and no longer depends on how fast the guest types.
const SUGGEST_DEBOUNCE_MS = 350;

// Longest a guest can keep typing without seeing the list refresh. Low
// enough to feel live, high enough to cost a few requests per query rather
// than one per keystroke.
const SUGGEST_MAX_WAIT_MS = 600;

// Matches MIN_SUGGEST_QUERY_LENGTH on the server, which enforces it and
// documents how the number was arrived at.
const SUGGEST_MIN_CHARS = 4;

let _suggestTimer = null;
let _suggestAbort = null;
// When the last request actually went out, for the maximum-wait cap.
let _lastSuggestFetchAt = 0;
let _suggestions = [];
let _activeSuggestion = -1;

function suggestionsEnabled() {
    return window.kboxConfig?.searchSuggestionsEnabled !== false;
}

function suggestionListEl() {
    return document.getElementById('search-suggestions');
}

// Hide the dropdown and forget its state.  Safe to call at any time.
export function closeSuggestions() {
    const list = suggestionListEl();
    if (!list) return;

    if (_suggestTimer) {
        clearTimeout(_suggestTimer);
        _suggestTimer = null;
    }
    if (_suggestAbort) {
        _suggestAbort.abort();
        _suggestAbort = null;
    }

    list.innerHTML = '';
    list.classList.add('hidden');
    _suggestions = [];
    _activeSuggestion = -1;
    // Starting a fresh query should get its first list immediately rather
    // than waiting out a cap left over from the previous one.
    _lastSuggestFetchAt = 0;

    const input = document.getElementById('search-input');
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
}

// Render one row, splitting it into the text already typed and the part
// being offered.  Built from text nodes rather than innerHTML: these strings
// come from an external service and land next to a tap target.
function renderSuggestionRow(suggestion, index, typed) {
    const row = document.createElement('li');
    row.className = 'search-suggestion';
    row.id = `search-suggestion-${index}`;
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', 'false');

    const matchesTyped = suggestion.toLowerCase().startsWith(typed.toLowerCase());
    if (matchesTyped) {
        row.appendChild(document.createTextNode(suggestion.slice(0, typed.length)));
        const completion = document.createElement('span');
        completion.className = 'search-suggestion-completion';
        completion.textContent = suggestion.slice(typed.length);
        row.appendChild(completion);
    } else {
        // Not a prefix extension (the endpoint corrects spelling, so this
        // happens) - emphasize the whole thing rather than a wrong split.
        const completion = document.createElement('span');
        completion.className = 'search-suggestion-completion';
        completion.textContent = suggestion;
        row.appendChild(completion);
    }

    // mousedown, not click: the input's blur handler closes the dropdown,
    // and blur beats click. preventDefault also keeps focus in the box so
    // the mobile keyboard doesn't flicker shut.
    row.addEventListener('mousedown', (e) => {
        e.preventDefault();
        acceptSuggestion(index);
    });

    return row;
}

function renderSuggestions(suggestions, typed) {
    const list = suggestionListEl();
    const input = document.getElementById('search-input');

    _suggestions = suggestions;
    _activeSuggestion = -1;
    list.innerHTML = '';

    if (!suggestions.length) {
        closeSuggestions();
        return;
    }

    suggestions.forEach((suggestion, index) => {
        list.appendChild(renderSuggestionRow(suggestion, index, typed));
    });

    list.classList.remove('hidden');
    input.setAttribute('aria-expanded', 'true');
    input.removeAttribute('aria-activedescendant');
}

// Move the highlight. Wraps around, and stepping past either end returns to
// the raw typed text so there's always a way back out of the list.
function moveActiveSuggestion(delta) {
    if (!_suggestions.length) return;

    const list = suggestionListEl();
    const input = document.getElementById('search-input');
    const rows = list.querySelectorAll('.search-suggestion');

    if (_activeSuggestion >= 0 && rows[_activeSuggestion]) {
        rows[_activeSuggestion].setAttribute('aria-selected', 'false');
    }

    _activeSuggestion += delta;
    if (_activeSuggestion < -1) {
        _activeSuggestion = _suggestions.length - 1;
    } else if (_activeSuggestion >= _suggestions.length) {
        _activeSuggestion = -1;
    }

    if (_activeSuggestion === -1) {
        input.removeAttribute('aria-activedescendant');
        return;
    }

    const active = rows[_activeSuggestion];
    active.setAttribute('aria-selected', 'true');
    active.scrollIntoView({ block: 'nearest' });
    input.setAttribute('aria-activedescendant', active.id);
}

// Put a suggestion in the box and search for it.
function acceptSuggestion(index) {
    const suggestion = _suggestions[index];
    if (suggestion === undefined) return;

    const input = document.getElementById('search-input');
    input.value = suggestion;
    closeSuggestions();
    search();
}

async function fetchSuggestions(query) {
    _lastSuggestFetchAt = Date.now();

    // Abort the previous request so a slow early response can't land after
    // a faster later one and repopulate the list with stale text.
    if (_suggestAbort) {
        _suggestAbort.abort();
    }
    const controller = new AbortController();
    _suggestAbort = controller;

    try {
        const response = await fetch(
            `/api/search/suggest?q=${encodeURIComponent(query)}`,
            { signal: controller.signal }
        );
        if (!response.ok) {
            // The server answers 200 with an empty list for every expected
            // case - disabled, too short, Google unreachable. A non-ok here
            // means something it could not handle, such as a reply it could
            // no longer parse, so say so rather than looking like "nothing
            // to suggest".
            console.error(`Suggestions unavailable: HTTP ${response.status}`);
            closeSuggestions();
            return;
        }

        const data = await response.json();
        const input = document.getElementById('search-input');

        // The box may have moved on while this was in flight.
        if (input.value.trim() !== query) return;

        if (!Array.isArray(data.suggestions)) {
            console.error('Suggestions response had no suggestions array:', data);
            closeSuggestions();
            return;
        }

        renderSuggestions(data.suggestions, query);
    } catch (e) {
        if (e.name !== 'AbortError') {
            // Not debug: a guest sees an absent dropdown either way, so the
            // console is the only place this is visible at all.
            console.error('Suggestion fetch failed:', e);
            closeSuggestions();
        }
    } finally {
        if (_suggestAbort === controller) {
            _suggestAbort = null;
        }
    }
}

function onSearchInput() {
    if (!suggestionsEnabled()) return;

    const query = document.getElementById('search-input').value.trim();

    if (_suggestTimer) clearTimeout(_suggestTimer);

    if (query.length < SUGGEST_MIN_CHARS) {
        closeSuggestions();
        return;
    }

    // Still typing, but it's been long enough since the last list — refresh
    // now instead of waiting for a pause that a steady typist never takes.
    if (Date.now() - _lastSuggestFetchAt >= SUGGEST_MAX_WAIT_MS) {
        fetchSuggestions(query);
        return;
    }

    _suggestTimer = setTimeout(() => {
        _suggestTimer = null;
        fetchSuggestions(query);
    }, SUGGEST_DEBOUNCE_MS);
}

function onSearchKeydown(e) {
    const open = _suggestions.length > 0;

    if (e.key === 'ArrowDown' && open) {
        e.preventDefault();
        moveActiveSuggestion(1);
    } else if (e.key === 'ArrowUp' && open) {
        e.preventDefault();
        moveActiveSuggestion(-1);
    } else if (e.key === 'Escape') {
        closeSuggestions();
    } else if (e.key === 'Enter') {
        if (open && _activeSuggestion >= 0) {
            // preventDefault here also suppresses the keypress handler
            // below, so the search doesn't run twice.
            e.preventDefault();
            acceptSuggestion(_activeSuggestion);
        } else {
            closeSuggestions();
        }
    }
}

// Set up search input handlers
export function setupSearchHandlers() {
    const input = document.getElementById('search-input');

    // Add Enter key support for search input
    input.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            search();
        }
    });

    input.addEventListener('input', onSearchInput);
    input.addEventListener('keydown', onSearchKeydown);
    input.addEventListener('blur', closeSuggestions);

    // iOS Safari fix: add touchend handler for search button
    // This helps when the keyboard is open and click events don't fire properly
    document.getElementById('search-button').addEventListener('touchend', function(e) {
        e.preventDefault();
        search();
    });
}
