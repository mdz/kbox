/**
 * Playback control functions for kbox web UI.
 */

import { isOperator, userId, controlsLocked } from './state.js';
import { renderSongSettings } from './song-settings.js';
import { createPitchControlHTML, updatePitchButtons, updatePitchDisplay } from './pitch.js';
import { toggleControlsLock } from './controls.js';

// Track the currently displayed song to detect song changes
let currentDisplayedSongId = null;

// Update play/pause toggle button based on state
export function updatePlayPauseButton(state) {
    // Update button in playback controls section
    const button = document.getElementById('play-pause-toggle');
    if (button) {
        if (state === 'playing') {
            button.textContent = '⏸ Pause';
        } else {
            button.textContent = '▶ Play';
        }
    }
}

// Show/hide buffering indicator
export function showBufferingIndicator(show) {
    const container = document.getElementById('now-playing-content');
    if (!container) return;

    if (show) {
        // Show buffering message
        container.innerHTML = '<div style="text-align: center; color: #4a9eff; padding: 20px; font-size: 18px;">⏳ Starting playback...</div>';
    } else {
        // Clear buffering message - loadQueue will refresh the content
        container.innerHTML = '';
    }
}

// Toggle play/pause
export async function togglePlayPause() {
    // Import loadQueue dynamically to avoid circular dependency
    const { loadQueue } = await import('./queue.js');

    try {
        // Get current state
        const statusResponse = await fetch('/api/playback/status');
        const statusData = await statusResponse.json();
        const currentState = statusData.state;

        // Toggle based on current state
        const action = (currentState === 'playing') ? 'pause' : 'play';

        // Only show buffering when starting fresh playback, not resuming from pause
        const isStartingFresh = action === 'play' && (currentState === 'idle' || currentState === 'stopped');
        if (isStartingFresh) {
            showBufferingIndicator(true);
        }

        const response = await fetch(`/api/playback/${action}`, {method: 'POST'});
        if (!response.ok) {
            if (isStartingFresh) showBufferingIndicator(false);
            alert('Error: ' + (await response.json()).detail);
        } else {
            // Update button immediately for better UX
            updatePlayPauseButton(action === 'play' ? 'playing' : 'paused');

            // Hide buffering indicator and refresh queue after a short delay
            // to allow playback to start (only if we showed it)
            if (isStartingFresh) {
                setTimeout(() => {
                    showBufferingIndicator(false);
                    loadQueue();
                }, 500);
            } else {
                loadQueue();
            }
        }
    } catch (e) {
        showBufferingIndicator(false);
        alert('Error controlling playback');
    }
}

// Stop playback
export async function stopPlayback() {
    // Import loadQueue dynamically to avoid circular dependency
    const { loadQueue } = await import('./queue.js');

    try {
        const response = await fetch('/api/playback/stop', {method: 'POST'});
        if (!response.ok) {
            alert('Error: ' + (await response.json()).detail);
        } else {
            // Update UI
            updatePlayPauseButton('idle');
            loadQueue();
        }
    } catch (e) {
        alert('Error stopping playback');
    }
}

// Playback controls (for skip, previous, etc.)
export async function playback(action) {
    // Import loadQueue dynamically to avoid circular dependency
    const { loadQueue } = await import('./queue.js');

    try {
        const response = await fetch(`/api/playback/${action}`, {method: 'POST'});
        const data = await response.json();

        // No next/previous song is an expected boundary condition (first/last
        // song in the queue), not an error - silently no-op rather than
        // interrupting with an alert.
        if (data.status === 'no_next_song' || data.status === 'no_previous_song') {
            return;
        }

        if (!response.ok) {
            alert('Error: ' + data.detail);
        } else {
            // Refresh queue on successful skip/previous
            loadQueue();
        }
    } catch (e) {
        alert('Error controlling playback');
    }
}

// Restart song from the beginning
export async function restartSong() {
    try {
        const response = await fetch('/api/playback/restart', {method: 'POST'});
        const data = await response.json();

        // Nothing playing is an expected condition (controls are always
        // visible regardless of state), not an error - silently no-op.
        if (data.status === 'nothing_playing') return;

        if (!response.ok) {
            alert('Error: ' + data.detail);
        }
    } catch (e) {
        alert('Error restarting song');
    }
}

// Seek forward 10 seconds
export async function seekForward() {
    try {
        const response = await fetch('/api/playback/seek', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({delta_seconds: 10})
        });
        const data = await response.json();

        if (data.status === 'nothing_playing') return;

        if (!response.ok) {
            alert('Error: ' + data.detail);
        }
    } catch (e) {
        alert('Error seeking');
    }
}

// Seek backward 10 seconds
export async function seekBackward() {
    try {
        const response = await fetch('/api/playback/seek', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({delta_seconds: -10})
        });
        const data = await response.json();

        if (data.status === 'nothing_playing') return;

        if (!response.ok) {
            alert('Error: ' + data.detail);
        }
    } catch (e) {
        alert('Error seeking');
    }
}

// Update song info without destroying controls
export function updateNowPlayingSongInfo(currentSong, positionSeconds) {
    // Update progress text
    const progressEl = document.getElementById('now-playing-progress');
    if (progressEl && currentSong.duration_seconds) {
        function formatTime(secs) {
            const m = Math.floor(secs / 60);
            const s = secs % 60;
            return `${m}:${s.toString().padStart(2, '0')}`;
        }
        progressEl.textContent = `${formatTime(positionSeconds)} / ${formatTime(currentSong.duration_seconds)}`;
    }
}

// Update pitch value display without destroying buttons
export function updateNowPlayingPitchValue(pitchValue) {
    const input = document.getElementById('now-playing-pitch-input');
    if (input && parseInt(input.value) !== pitchValue) {
        input.value = pitchValue;
        updatePitchDisplay('now-playing-pitch-display', pitchValue);
        updatePitchButtons('now-playing', pitchValue);
    }
}

// Render Now Playing section
export function renderNowPlaying(statusData) {
    const container = document.getElementById('now-playing-content');
    if (!container) return;

    const currentSong = statusData.current_song;

    // Show/hide playback controls section based on operator status
    const playbackControlsSection = document.getElementById('playback-controls-section');
    if (playbackControlsSection) {
        playbackControlsSection.style.display = isOperator ? 'block' : 'none';
    }

    // If no current song, just show message
    if (!currentSong) {
        container.innerHTML = '<div style="text-align: center; color: #666; padding: 15px;">Nothing playing</div>';
        currentDisplayedSongId = null;  // Reset so next song triggers full render
        return;
    }

    // Check if user can access pitch controls (their song or operator)
    const hasControlAccess = (currentSong.user_id === userId) || isOperator;

    // Check if we already have pitch controls rendered
    const hasExistingControls = document.getElementById('now-playing-lock-button') !== null;

    // Determine song identity (use video_id if available, fall back to title)
    const songId = currentSong.video_id || currentSong.title;
    const songChanged = songId !== currentDisplayedSongId;

    // Only do partial update if controls exist, user has access, AND song hasn't changed
    if (hasExistingControls && hasControlAccess && !songChanged) {
        // Controls already exist for same song - just update values and visibility without destroying buttons
        updateNowPlayingSongInfo(currentSong, statusData.position_seconds);
        updateNowPlayingPitchValue(currentSong.pitch_semitones || 0);

        // Update visibility based on lock state
        const pitchSection = document.getElementById('now-playing-pitch-section');
        if (pitchSection) pitchSection.style.display = controlsLocked ? 'none' : 'block';

        // Update lock button
        const lockButton = document.getElementById('now-playing-lock-button');
        if (lockButton) {
            lockButton.innerHTML = controlsLocked ? '🔒 Unlock Controls' : '🔓 Lock Controls';
            lockButton.style.background = controlsLocked ? '#555' : '#666';
        }
        return;
    }

    // Track current song for future change detection
    currentDisplayedSongId = songId;

    // Format display title: prefer extracted artist/song
    const hasExtracted = currentSong.artist && currentSong.song_name;
    const displayTitle = hasExtracted ? `${currentSong.song_name} by ${currentSong.artist}` : currentSong.title;

    // Full render - show song info
    renderSongSettings('now-playing-content', {
        title: displayTitle,
        original_title: hasExtracted ? currentSong.title : null,  // Show original as secondary if we have extracted
        thumbnail_url: currentSong.thumbnail_url,
        user_id: currentSong.user_id,
        user_name: currentSong.user_name,
        duration_seconds: currentSong.duration_seconds,
        position_seconds: statusData.position_seconds,
        pitch_semitones: currentSong.pitch_semitones || 0
    }, {
        context: 'now-playing',
        live: false,
        showStatus: false,
        showThumbnail: true,
        showUser: true,
        showPitchControls: false
    });

    // Add pitch controls and lock button if user has access
    if (hasControlAccess) {
        // Pitch controls section (hidden when locked)
        const pitchControls = document.createElement('div');
        pitchControls.id = 'now-playing-pitch-section';
        pitchControls.style.cssText = `margin-top: 15px; padding-top: 15px; border-top: 1px solid #444; display: ${controlsLocked ? 'none' : 'block'};`;
        pitchControls.innerHTML = `
            <h3 style="color: #4a9eff; font-size: 1em; margin-bottom: 10px;">Adjust Pitch (Live)</h3>
            <div id="now-playing-pitch-control-container"></div>
        `;
        container.appendChild(pitchControls);

        // Initialize pitch control
        const pitchValue = currentSong.pitch_semitones || 0;
        const pitchControlContainer = document.getElementById('now-playing-pitch-control-container');
        if (pitchControlContainer) {
            pitchControlContainer.innerHTML = createPitchControlHTML('now-playing', pitchValue, 24);
            updatePitchButtons('now-playing', pitchValue);
        }

        // Lock button (always visible if user has access)
        const lockButton = document.createElement('button');
        lockButton.id = 'now-playing-lock-button';
        lockButton.onclick = toggleControlsLock;
        lockButton.style.cssText = 'width: 100%; margin-top: 15px; padding: 12px; font-size: 16px;';

        if (controlsLocked) {
            lockButton.innerHTML = '🔒 Unlock Controls';
            lockButton.style.background = '#555';
        } else {
            lockButton.innerHTML = '🔓 Lock Controls';
            lockButton.style.background = '#666';
        }

        container.appendChild(lockButton);
    }
}

// --- Turn alert (sticky status bar, tab title, vibration) ---

const BASE_TITLE = document.title;
let lastTurnLevel = 'none';

function formatEta(secs) {
    const mins = Math.max(1, Math.round(secs / 60));
    return `~${mins} min`;
}

// Pure: decide what the guest should be told about their turn.
// level: 'none' | 'later' | 'next' | 'playing' | 'idle-first'
export function computeTurnStatus(statusData, nextSong, myNextTurn, myUserId) {
    const currentSong = statusData && statusData.current_song;
    if (currentSong && currentSong.user_id === myUserId) {
        return { level: 'playing', text: "🎤 You're on! Sing it!", title: "🎤 You're singing! – kbox" };
    }
    if (nextSong && nextSong.user_id === myUserId) {
        if (!currentSong) {
            return {
                level: 'idle-first',
                text: "🎤 You're first — waiting for the host to start",
                title: "🎤 You're first – kbox",
            };
        }
        return { level: 'next', text: "🎤 You're next! Head to the mic", title: "🎤 You're next! – kbox" };
    }
    if (myNextTurn) {
        const n = myNextTurn.songs_away;
        const songs = n === 1 ? '1 song' : `${n} songs`;
        const eta = myNextTurn.estimated_seconds > 0 ? ` (${formatEta(myNextTurn.estimated_seconds)})` : '';
        return {
            level: 'later',
            text: `🎤 You're up in ${songs}${eta}`,
            title: `🎤 Up in ${n} – kbox`,
        };
    }
    return { level: 'none', text: '', title: BASE_TITLE };
}

function applyTurnStatus(status) {
    const bar = document.getElementById('turn-bar');
    if (bar) {
        bar.textContent = status.text;
        bar.className = `turn-bar turn-${status.level}`;
        bar.hidden = status.level === 'none';
    }
    document.body.classList.toggle('has-turn-bar', status.level !== 'none');
    document.title = status.title;

    // Vibrate once on the transition into being the immediate next singer
    if (status.level === 'next' && lastTurnLevel !== 'next' && typeof navigator.vibrate === 'function') {
        try { navigator.vibrate([200, 100, 200]); } catch (e) { /* ignore */ }
    }
    lastTurnLevel = status.level;
}

// Update the turn-alert bar and tab title.
// nextSong and myNextTurn are provided by the backend (single source of truth).
// (This replaced the mid-page "Up Next" box: the display and the queue list
// already show who is singing next.)
export function renderUpNext(statusData, nextSong, myNextTurn) {
    applyTurnStatus(computeTurnStatus(statusData, nextSong, myNextTurn, userId));
}
