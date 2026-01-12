/**
 * Controls lock/unlock functionality for kbox web UI.
 */

import {
    controlsLocked, setControlsLocked,
    autoLockTimer, setAutoLockTimer,
    AUTO_LOCK_TIMEOUT,
    playbackControlsLocked, setPlaybackControlsLocked,
    playbackAutoLockTimer, setPlaybackAutoLockTimer,
    PLAYBACK_AUTO_LOCK_TIMEOUT
} from './state.js';

// Toggle controls lock
export function toggleControlsLock() {
    setControlsLocked(!controlsLocked);
    
    if (!controlsLocked) {
        // Start auto-lock timer when unlocked
        resetAutoLockTimer();
    } else {
        // Clear timer when manually locked
        if (autoLockTimer) {
            clearTimeout(autoLockTimer);
            setAutoLockTimer(null);
        }
    }
    
    // Show/hide pitch controls and update button without destroying them
    const pitchSection = document.getElementById('now-playing-pitch-section');
    const lockButton = document.getElementById('now-playing-lock-button');
    
    if (pitchSection) pitchSection.style.display = controlsLocked ? 'none' : 'block';
    
    if (lockButton) {
        lockButton.innerHTML = controlsLocked ? '🔒 Unlock Controls' : '🔓 Lock Controls';
        lockButton.style.background = controlsLocked ? '#555' : '#666';
    }
}

// Reset the auto-lock timer
export function resetAutoLockTimer() {
    // Clear existing timer
    if (autoLockTimer) {
        clearTimeout(autoLockTimer);
    }
    
    // Only set timer if controls are unlocked
    if (!controlsLocked) {
        setAutoLockTimer(setTimeout(() => {
            setControlsLocked(true);
            setAutoLockTimer(null);
            // Hide pitch controls and update button
            const pitchSection = document.getElementById('now-playing-pitch-section');
            const lockButton = document.getElementById('now-playing-lock-button');
            
            if (pitchSection) pitchSection.style.display = 'none';
            if (lockButton) {
                lockButton.innerHTML = '🔒 Unlock Controls';
                lockButton.style.background = '#555';
            }
        }, AUTO_LOCK_TIMEOUT));
    }
}

// Toggle playback controls lock
export function togglePlaybackControlsLock() {
    setPlaybackControlsLocked(!playbackControlsLocked);
    
    if (!playbackControlsLocked) {
        // Start auto-lock timer when unlocked
        resetPlaybackAutoLockTimer();
    } else {
        // Clear timer when manually locked
        if (playbackAutoLockTimer) {
            clearTimeout(playbackAutoLockTimer);
            setPlaybackAutoLockTimer(null);
        }
    }
    
    // Show/hide playback buttons and update button
    const buttonsSection = document.getElementById('playback-buttons-section');
    const lockButton = document.getElementById('playback-lock-button');
    
    if (buttonsSection) buttonsSection.style.display = playbackControlsLocked ? 'none' : 'block';
    
    if (lockButton) {
        lockButton.innerHTML = playbackControlsLocked ? '🔒 Unlock Controls' : '🔓 Lock Controls';
        lockButton.style.background = playbackControlsLocked ? '#555' : '#666';
    }
}

// Reset the playback controls auto-lock timer
export function resetPlaybackAutoLockTimer() {
    // Clear existing timer
    if (playbackAutoLockTimer) {
        clearTimeout(playbackAutoLockTimer);
    }
    
    // Only set timer if controls are unlocked
    if (!playbackControlsLocked) {
        setPlaybackAutoLockTimer(setTimeout(() => {
            setPlaybackControlsLocked(true);
            setPlaybackAutoLockTimer(null);
            // Hide playback buttons and update button
            const buttonsSection = document.getElementById('playback-buttons-section');
            const lockButton = document.getElementById('playback-lock-button');
            
            if (buttonsSection) buttonsSection.style.display = 'none';
            if (lockButton) {
                lockButton.innerHTML = '🔒 Unlock Controls';
                lockButton.style.background = '#555';
            }
        }, PLAYBACK_AUTO_LOCK_TIMEOUT));
    }
}
