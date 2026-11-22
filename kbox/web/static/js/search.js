/**
 * Search and add song functions for kbox web UI.
 */

import { escapeHtml } from './utils.js';
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
// m:ss (h:mm:ss for an hour or more); empty when the length is unknown.
function formatDuration(totalSeconds) {
    const secs = Math.round(Number(totalSeconds));
    if (!Number.isFinite(secs) || secs <= 0) return '';
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = String(secs % 60).padStart(2, '0');
    return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

// Results whose exact video has been performed here before get a check badge
// on the thumbnail (#216); this text is its screen-reader and tooltip wording,
// and the add-song modal spells the meaning out. A yes/no about this version,
// never a count: a count reads as how popular the song is and could put
// guests off a crowd favourite.
const TESTED_LABEL = 'Tested track';

function renderResultsList(container, videos) {
    container.innerHTML = '';
    videos.forEach(video => {
        const div = document.createElement('div');
        div.className = 'search-result';
        div.tabIndex = 0;
        div.setAttribute('role', 'button');
        const duration = formatDuration(video.duration_seconds);
        const details = [video.channel, duration].filter(Boolean).join(' · ');
        const ariaDetails = [details, video.performed_before ? TESTED_LABEL : '']
            .filter(Boolean).join(', ');
        div.setAttribute('aria-label',
            `Select ${video.title}${ariaDetails ? ` (${ariaDetails})` : ''}`);
        const tested = video.performed_before
            ? `<span class="search-result-tested" title="${TESTED_LABEL}" aria-hidden="true">&#x2713;</span>`
            : '';
        div.innerHTML = `
            <div class="search-result-thumb">
                <img src="${escapeHtml(video.thumbnail)}" alt="${escapeHtml(video.title)}" />
                ${tested}
            </div>
            <div class="search-result-info">
                <div class="search-result-title">${escapeHtml(video.title)}</div>
                <div class="search-result-channel">${escapeHtml(details)}</div>
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
            resultsDiv.innerHTML = `<div class="suggestions-error">${escapeHtml(error.detail || 'Could not get suggestions')}</div>`;
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

    // Drop focus so mobile browsers put the on-screen keyboard away. iOS
    // Safari leaves it up after a search otherwise, covering the results
    // (#85).
    document.getElementById('search-input').blur();

    const resultsDiv = document.getElementById('search-results');
    resultsDiv.innerHTML = 'Searching...';

    _searchInFlight = true;
    try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        const data = await response.json();

        // Replacing a song whose download failed: its own upload is no
        // use, so leave it out.
        const exclude = currentSelectionTarget && currentSelectionTarget.excludeVideoId;
        const results = exclude
            ? (data.results || []).filter(video => video.id !== exclude)
            : data.results;
        renderResultsList(resultsDiv, results);
    } catch (e) {
        // The guest gets a message either way; without this the cause was
        // discarded entirely.
        console.error('Search failed:', e);
        resultsDiv.innerHTML = '<div class="error">Error searching</div>';
    } finally {
        _searchInFlight = false;
    }
}

// Soft warnings shown in the add-song modal. None of these block: the
// confirm button just becomes "... anyway". The duplicate-singer nudge doesn't
// apply when replacing an existing entry in place (see #88) - that's
// correcting a mistake, not queueing a second turn.
function computeAddWarnings(video, replacing) {
    const warnings = [];

    const duplicate = currentQueue?.find(item => item.video_id === video.id);
    if (duplicate) {
        warnings.push(duplicate.is_played
            ? 'This song was already performed tonight.'
            : 'This song is already in the queue.');
    }

    const warningMinutes = window.kboxConfig?.longSongWarningMinutes || 0;
    const durationSeconds = video.duration_seconds || 0;
    if (warningMinutes > 0 && durationSeconds > warningMinutes * 60) {
        const mins = Math.floor(durationSeconds / 60);
        const secs = String(Math.round(durationSeconds) % 60).padStart(2, '0');
        warnings.push(`This song is ${mins}:${secs} long.`);
    }

    if (!replacing && window.kboxConfig?.duplicateSingerNudgeEnabled) {
        if (currentQueue?.some(item => item.user_id === userId && !item.is_played)) {
            warnings.push('You already have a song queued.');
        }
    }

    return warnings;
}

function renderWarningsHtml(warnings) {
    return warnings.map(w =>
        `<div class="add-song-warning" style="color: #ffb347; font-size: 0.9em; margin-top: 8px; padding: 8px 12px; background: rgba(255, 179, 71, 0.1); border-radius: 6px; text-align: center;">&#x26A0;&#xFE0F; ${escapeHtml(w)}</div>`
    ).join('');
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
        additionalControls = `<div style="color: #aaa; font-size: 0.9em; margin-top: 8px; padding: 8px 12px; background: rgba(255, 255, 255, 0.05); border-radius: 6px; text-align: center;">Replacing “${escapeHtml(currentSelectionTarget.label)}” for ${escapeHtml(currentSelectionTarget.userName)}</div>`;
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

    if (video.performed_before) {
        additionalControls += '<div style="color: #4aff6e; font-size: 0.9em; margin-top: 8px; padding: 8px 12px; background: rgba(74, 255, 110, 0.08); border-radius: 6px; text-align: center;">&#x2713; This version has worked well here before</div>';
    }

    const warnings = computeAddWarnings(video, currentSelectionTarget);
    additionalControls += renderWarningsHtml(warnings);
    if (warnings.length) {
        confirmBtn.textContent = currentSelectionTarget ? 'Replace anyway' : 'Add anyway';
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
// slot is {position, estimated_seconds} from the add/replace response.
function positionSuffix(slot) {
    if (!slot) return '';
    if (slot.position === 1) return ' — you\'re up next';
    const minutes = Math.round(slot.estimated_seconds / 60);
    const wait = minutes < 1 ? 'less than a minute' : `about ${minutes} min`;
    return ` — #${slot.position} in the queue, ${wait}`;
}

export async function confirmAddToQueue() {
    if (!currentVideoToAdd) return;

    const replacing = currentSelectionTarget;

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
            const slot = (await response.json()).slot;
            loadQueue();
            setCurrentVideoToAdd(null);
            setCurrentSelectionTarget(null);
            updateReplaceTargetBanner();
            const verb = replacing ? 'Replaced with' : 'Added';
            showToast(`✓ ${verb} ${title}${positionSuffix(slot)}`);
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
