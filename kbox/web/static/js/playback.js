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

        // Handle warning responses (no next/previous song)
        if (data.status === 'no_next_song' || data.status === 'no_previous_song') {
            // Show warning message instead of error
            const message = data.message || `No ${action} song available`;
            alert(message);
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
        if (!response.ok) {
            alert('Error: ' + (await response.json()).detail);
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
        if (!response.ok) {
            alert('Error: ' + (await response.json()).detail);
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
        if (!response.ok) {
            alert('Error: ' + (await response.json()).detail);
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

// Render Up Next section
// nextSong is provided by the backend (single source of truth)
// queue is the full queue array for computing user's upcoming turn
export function renderUpNext(statusData, nextSong, queue) {
    const section = document.getElementById('up-next-section');
    const content = document.getElementById('up-next-content');
    if (!section || !content) return;

    section.style.display = 'block';

    const currentSong = statusData.current_song;

    // Helper to format time
    function formatTime(secs) {
        const m = Math.floor(secs / 60);
        const s = secs % 60;
        return `${m}:${s.toString().padStart(2, '0')}`;
    }

    if (!nextSong) {
        content.innerHTML = `<span style="font-size: 1.1em; color: #666;">Up Next: No one in queue</span>`;
        return;
    }

    // Calculate time remaining in current song
    const position = statusData.position_seconds || 0;
    const currentDuration = currentSong ? (currentSong.duration_seconds || 0) : 0;
    const currentRemaining = Math.max(0, currentDuration - position);

    const isNextYou = nextSong.user_id === userId;
    const nameStyle = isNextYou ? 'color: #4aff6e; font-size: 1.2em;' : 'color: #4a9eff;';
    const youLabel = isNextYou ? '🎤 ' : '';

    // Build main "Up Next" message
    let html = '';
    if (!currentSong) {
        html = `<span style="font-size: 1.1em;">${youLabel}Up Next: <strong style="${nameStyle}">${isNextYou ? 'YOU!' : nextSong.user_name}</strong></span>`;
    } else {
        const timeStr = formatTime(currentRemaining);
        html = `<span style="font-size: 1.1em;">${youLabel}Up Next: <strong style="${nameStyle}">${isNextYou ? 'YOU!' : nextSong.user_name}</strong> in ${timeStr}</span>`;
    }

    // Calculate when the current user's next song is coming up
    // (only if it's not already the immediate next song)
    if (queue && userId && !isNextYou) {
        // Get unplayed songs after the current one, in queue order
        const upcomingItems = queue.filter(item => !item.is_current && !item.is_played);

        let timeUntilUserSong = currentSong ? currentRemaining : 0;
        let songsAway = 0;
        let userNextFound = false;

        for (const item of upcomingItems) {
            if (item.user_id === userId) {
                userNextFound = true;
                break;
            }
            songsAway++;
            timeUntilUserSong += (item.duration_seconds || 0);
        }

        if (userNextFound) {
            const timeStr = formatTime(timeUntilUserSong);
            const songsLabel = songsAway === 1 ? '1 song' : `${songsAway} songs`;
            html += `<br><span style="font-size: 0.95em; color: #4aff6e;">🎤 Your turn in ~${timeStr} (${songsLabel} away)</span>`;
        }
    }

    content.innerHTML = html;
}
