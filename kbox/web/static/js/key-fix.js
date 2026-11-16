/**
 * "Too high? / Too low?" key fix for the singer's own song.
 *
 * A singer normally doesn't look at their phone while performing; when they
 * do, it's because something is wrong. So whenever they look, this control
 * is already there, full size, for the whole song: it fixes the key in one
 * or two taps without any music theory, moving in whole steps (2 semitones)
 * and using plain words. It never changes anything on its own.
 *
 * It reuses POST /api/playback/pitch, which allows the owner of the current
 * song to change its pitch whether or not operator controls are unlocked.
 */

import { escapeHtml } from './utils.js';
import { userId } from './state.js';

// One tap moves the key by a whole step: enough to be clearly noticeable,
// so a fix usually takes one or two taps.
export const KEY_FIX_STEP = 2;
export const KEY_FIX_LIMIT = 12;

// Pure: the pitch after one tap in `direction` (-1 = lower, +1 = higher),
// clamped to the range the pipeline accepts.
export function nextKeyShift(current, direction, step = KEY_FIX_STEP) {
    const value = (Number.isFinite(current) ? current : 0) + direction * step;
    return Math.max(-KEY_FIX_LIMIT, Math.min(KEY_FIX_LIMIT, value));
}

// Pure: plain-language description of a shift. One "step" is one semitone,
// matching the numbers the singer sees elsewhere in the app.
export function describeKeyShift(semitones) {
    if (!semitones) return 'Original key';
    const n = Math.abs(semitones);
    const steps = n === 1 ? '1 step' : `${n} steps`;
    return `${steps} ${semitones < 0 ? 'lower' : 'higher'}`;
}

// --- DOM ---

// Per-song UI state. Reset when the song changes.
let state = { songKey: null, pending: false, pitch: 0 };

function setDisabled(id, disabled) {
    const el = document.getElementById(id);
    if (el) el.disabled = disabled;
}

function paint() {
    const current = document.querySelector('#key-fix .key-fix-current');
    if (!current) return;
    current.textContent = describeKeyShift(state.pitch);
    setDisabled('key-fix-lower', state.pitch <= -KEY_FIX_LIMIT);
    setDisabled('key-fix-higher', state.pitch >= KEY_FIX_LIMIT);
    setDisabled('key-fix-nudge-lower', state.pitch <= -KEY_FIX_LIMIT);
    setDisabled('key-fix-nudge-higher', state.pitch >= KEY_FIX_LIMIT);
    const reset = document.getElementById('key-fix-reset');
    if (reset) reset.hidden = state.pitch === 0;
}

// Build the control for the current song. `songKey` identifies the song so
// state from the previous song never leaks into the next one.
export function renderKeyFix(container, songKey, pitch) {
    if (state.songKey !== songKey) {
        state = { songKey, pending: false, pitch };
    } else if (!state.pending) {
        state.pitch = pitch;
    }

    const root = document.createElement('div');
    root.id = 'key-fix';
    root.className = 'key-fix';
    root.innerHTML = `
        <div class="key-fix-heading">How's the key?</div>
        <div class="key-fix-buttons">
            <button type="button" id="key-fix-lower" class="key-fix-button"
                    onclick="keyFixTap(-1)">
                <span class="key-fix-ask">Too high?</span>
                <span class="key-fix-act">Go lower</span>
            </button>
            <button type="button" id="key-fix-higher" class="key-fix-button"
                    onclick="keyFixTap(1)">
                <span class="key-fix-ask">Too low?</span>
                <span class="key-fix-act">Go higher</span>
            </button>
        </div>
        <div class="key-fix-status">
            Now: <strong class="key-fix-current">${escapeHtml(describeKeyShift(state.pitch))}</strong>
        </div>
        <div class="key-fix-fine">
            <button type="button" id="key-fix-nudge-lower" class="key-fix-link"
                    onclick="keyFixTap(-1, 1)">a little lower</button>
            <span aria-hidden="true">·</span>
            <button type="button" id="key-fix-nudge-higher" class="key-fix-link"
                    onclick="keyFixTap(1, 1)">a little higher</button>
            <button type="button" id="key-fix-reset" class="key-fix-link"
                    onclick="keyFixReset()">back to original</button>
        </div>
    `;
    container.appendChild(root);
    paint();
}

// Refresh from a status poll without rebuilding the buttons.
export function updateKeyFix(pitch) {
    if (!state.pending) state.pitch = pitch;
    paint();
}

async function applyPitch(newValue) {
    const previous = state.pitch;
    if (newValue === previous) return;
    state.pending = true;
    state.pitch = newValue;
    paint();
    try {
        const response = await fetch('/api/playback/pitch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ semitones: newValue, user_id: userId }),
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            state.pitch = previous;
            const detail = typeof error.detail === 'string' ? error.detail : 'please try again';
            alert('Could not change the key: ' + detail);
        }
    } catch (e) {
        state.pitch = previous;
        alert('Could not change the key');
    } finally {
        state.pending = false;
        paint();
    }
}

export function keyFixTap(direction, step = KEY_FIX_STEP) {
    return applyPitch(nextKeyShift(state.pitch, direction, step));
}

export function keyFixReset() {
    return applyPitch(0);
}
