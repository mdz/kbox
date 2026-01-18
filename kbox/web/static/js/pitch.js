/**
 * Pitch control functions for kbox web UI.
 */

import { userId } from './state.js';

// Convert semitones to musical interval name
export function getIntervalName(semitones) {
    const intervals = {
        0: 'Unison',
        1: 'Minor Second',
        2: 'Major Second',
        3: 'Minor Third',
        4: 'Major Third',
        5: 'Perfect Fourth',
        6: 'Tritone',
        7: 'Perfect Fifth',
        8: 'Minor Sixth',
        9: 'Major Sixth',
        10: 'Minor Seventh',
        11: 'Major Seventh',
        12: 'Octave'
    };

    const absSemitones = Math.abs(semitones);
    const interval = intervals[absSemitones] || `${absSemitones} semitones`;

    if (semitones === 0) {
        return interval;
    } else if (semitones < 0) {
        return `${interval} down`;
    } else {
        return `${interval} up`;
    }
}

// Update pitch display with number and interval name
export function updatePitchDisplay(displayId, value, fontSize = 24) {
    const display = document.getElementById(displayId);
    if (!display) return;

    // Format value with + for positive numbers
    const formattedValue = value > 0 ? `+${value}` : value.toString();
    const intervalName = getIntervalName(value);
    display.innerHTML = `<div style="font-size: ${fontSize}px; font-weight: bold; color: #4a9eff;">${formattedValue}</div><div style="font-size: ${fontSize * 0.5}px; color: #aaa; margin-top: 2px;">${intervalName}</div>`;
}

// Create reusable pitch control HTML
export function createPitchControlHTML(prefix, initialValue = 0, fontSize = 24) {
    return `
        <div class="pitch-control">
            <button class="pitch-button" onclick="adjustPitch('${prefix}', -1)" id="${prefix}-pitch-minus">−</button>
            <div class="pitch-display" id="${prefix}-pitch-display">
                <div style="font-size: ${fontSize}px; font-weight: bold; color: #4a9eff;">${initialValue > 0 ? '+' + initialValue : initialValue}</div>
                <div style="font-size: ${fontSize * 0.5}px; color: #aaa; margin-top: 2px;">${getIntervalName(initialValue)}</div>
            </div>
            <button class="pitch-button" onclick="adjustPitch('${prefix}', 1)" id="${prefix}-pitch-plus">+</button>
        </div>
        <input type="hidden" id="${prefix}-pitch-input" value="${initialValue}" />
    `;
}

// Update pitch control buttons state
export function updatePitchButtons(prefix, value) {
    const minusBtn = document.getElementById(`${prefix}-pitch-minus`);
    const plusBtn = document.getElementById(`${prefix}-pitch-plus`);
    if (minusBtn) minusBtn.disabled = (value <= -12);
    if (plusBtn) plusBtn.disabled = (value >= 12);
}

// Adjust pitch for now-playing song (immediate effect)
export async function adjustNowPlayingPitch(delta) {
    // Get current pitch from input
    const input = document.getElementById('now-playing-pitch-input');
    if (!input) return;

    let currentValue = parseInt(input.value) || 0;
    let newValue = currentValue + delta;

    // Clamp to -12 to +12 range
    newValue = Math.max(-12, Math.min(12, newValue));

    // Update display immediately for responsive UI
    updatePitchDisplay('now-playing-pitch-display', newValue);
    input.value = newValue;
    updatePitchButtons('now-playing', newValue);

    // Apply pitch change immediately via API
    try {
        const response = await fetch('/api/playback/pitch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                semitones: newValue,
                user_id: userId
            })
        });

        if (!response.ok) {
            const error = await response.json();
            console.error('Error setting pitch:', error);
            // Revert display on error
            updatePitchDisplay('now-playing-pitch-display', currentValue);
            input.value = currentValue;
            updatePitchButtons('now-playing', currentValue);
            alert('Error adjusting pitch: ' + (error.detail || 'Unknown error'));
        }
    } catch (e) {
        console.error('Error setting pitch:', e);
        // Revert display on error
        updatePitchDisplay('now-playing-pitch-display', currentValue);
        input.value = currentValue;
        updatePitchButtons('now-playing', currentValue);
        alert('Error adjusting pitch');
    }
}

// Adjust pitch value with +/- buttons (reusable for all contexts)
export function adjustPitch(context, delta) {
    // Handle special cases with immediate effect (now-playing and operator)
    if (context === 'now-playing') {
        adjustNowPlayingPitch(delta);
        return;
    }


    // For other contexts (add-song, edit-queue-item), just update the UI
    const inputId = context === 'add-song' ? 'add-song-pitch-input' :
                   'edit-queue-item-pitch-input';
    const displayId = context === 'add-song' ? 'add-song-pitch-display' :
                     'edit-queue-item-pitch-display';

    const input = document.getElementById(inputId);

    if (!input) return;

    let currentValue = parseInt(input.value) || 0;
    let newValue = currentValue + delta;

    // Clamp to -12 to +12 range
    newValue = Math.max(-12, Math.min(12, newValue));

    input.value = newValue;
    updatePitchDisplay(displayId, newValue);

    // Update button states
    if (context === 'edit-queue-item') {
        updatePitchButtons('edit-queue-item', newValue);
    }
}
