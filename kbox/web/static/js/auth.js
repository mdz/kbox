/**
 * User identity and operator authentication for kbox web UI.
 */

import {
    isOperator, setIsOperator,
    userName, setUserName,
    userId, setUserId,
    pendingOperatorAction, setPendingOperatorAction
} from './state.js';
import { generateUUID } from './utils.js';

// Name a guest just typed, held while a recognition-candidate lookup is in
// flight and until they resolve it (pick a candidate or say "I'm new").
let _pendingTypedName = null;

// Resolves once identity registration has landed server-side (session cookie
// has user_id). initializeUserIdentity()/app.js await this before starting
// any recurring polling, which is what prevents the session-cookie race
// described below in the first place.
let _resolveIdentityReady;
let identityReadyPromise = new Promise((resolve) => {
    _resolveIdentityReady = resolve;
});

// Register user with server (creates or updates)
export async function registerUser(uid, displayName) {
    try {
        const response = await fetch('/api/users', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                user_id: uid,
                display_name: displayName
            })
        });
        if (!response.ok) {
            console.error('Failed to register user:', await response.text());
        }
    } catch (e) {
        console.error('Error registering user:', e);
    }
}

// Backstop for the session-cookie race: Starlette's SessionMiddleware
// re-signs the cookie on every response from whatever session dict that
// request started with, so a concurrent unauthenticated poll response can
// still (rarely) land after a registration POST and clobber a just-set
// user_id, even with the identityReadyPromise sequencing above. When that
// happens, any endpoint that keys off the session's user_id — whether it
// 401s (require_user) or 403s (a manual "this isn't your resource" check,
// e.g. /api/history) — fails until the browser re-registers.
//
// Rather than have every call site remember to opt in, patch window.fetch
// once: any /api/ request that comes back 401/403 gets a single
// re-register-and-retry. This covers every current and future
// identity-dependent endpoint automatically. It excludes /api/users itself
// (avoid recursion) and /api/auth/* (operator PIN auth — a 401 there is a
// genuine wrong-PIN rejection, not a stale identity, and retrying would
// just resubmit the same wrong PIN).
const _nativeFetch = window.fetch.bind(window);

window.fetch = async function (input, init) {
    const response = await _nativeFetch(input, init);
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const isIdentityDependent = (response.status === 401 || response.status === 403)
        && url.startsWith('/api/')
        && !url.startsWith('/api/users')
        && !url.startsWith('/api/auth/');
    if (isIdentityDependent) {
        await registerUser(userId, userName);
        return _nativeFetch(input, init);
    }
    return response;
};

// Claim an identity — either an existing one the guest recognized, or a
// fresh UUID for "I'm new" — and (re)bind the session to it.
// See ldocs/GUEST_IDENTITY_CONTINUITY.md / GUEST_IDENTITY_TECHNICAL_DESIGN.md.
async function claimUser(uid, displayName) {
    try {
        const response = await fetch('/api/users/claim', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                user_id: uid,
                display_name: displayName
            })
        });
        if (!response.ok) {
            console.error('Failed to claim identity:', await response.text());
        }
    } catch (e) {
        console.error('Error claiming identity:', e);
    }
}

// Look up existing identities matching a typed name. Returns [] (never
// throws) on a genuinely new name or on a network error — either way the
// caller should just let the guest through as new rather than get stuck.
async function lookupCandidates(name) {
    try {
        const response = await fetch(`/api/users/lookup?name=${encodeURIComponent(name)}`);
        if (response.ok) {
            const data = await response.json();
            return data.candidates || [];
        }
    } catch (e) {
        console.error('Error looking up name:', e);
    }
    return [];
}

// Finish identity resolution: claim uid/displayName with the server,
// persist locally, resolve the caller in app.js waiting on identity, and
// hide whichever of the two identity modals is showing.
async function finishIdentityResolution(uid, displayName) {
    setUserName(displayName);
    setUserId(uid);

    // Must await so the session cookie has user_id before any concurrent
    // loadQueue tick overwrites it.
    await claimUser(userId, userName);

    // Only persist to localStorage after the server confirms the claim —
    // otherwise a guest who abandons the recognition modal mid-flow (closes
    // the tab) could end up with a locally-cached identity the server never
    // actually bound.
    localStorage.setItem('kbox_user_name', userName);
    localStorage.setItem('kbox_user_id', userId);

    _resolveIdentityReady();

    for (const id of ['name-modal', 'recognition-modal']) {
        const modal = document.getElementById(id);
        if (modal) {
            modal.classList.add('hidden');
            modal.style.display = 'none';
        }
    }
}

// Render the recognition candidate list via DOM APIs (not innerHTML) —
// display names and last-song titles are guest/external free text, and
// building HTML attribute strings out of free text is exactly how you get
// an attribute-breakout XSS bug.
function renderRecognitionCandidates(candidates) {
    const list = document.getElementById('recognition-candidate-list');
    if (!list) return;
    list.innerHTML = '';

    for (const candidate of candidates) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'recognition-candidate';
        button.style.borderColor = candidate.color || '#555';

        const icon = document.createElement('span');
        icon.className = 'recognition-candidate-icon';
        icon.textContent = candidate.icon || '👤';

        const info = document.createElement('span');
        const nameEl = document.createElement('span');
        nameEl.className = 'recognition-candidate-name';
        nameEl.textContent = candidate.display_name;
        const contextEl = document.createElement('span');
        contextEl.className = 'recognition-candidate-context';
        contextEl.textContent = candidate.last_song_title
            ? `Last sang "${candidate.last_song_title}"`
            : 'No songs yet';
        info.appendChild(nameEl);
        info.appendChild(contextEl);

        button.appendChild(icon);
        button.appendChild(info);
        button.addEventListener('click', () => {
            finishIdentityResolution(candidate.user_id, candidate.display_name);
        });

        list.appendChild(button);
    }
}

function showRecognitionModal(typedName, candidates) {
    _pendingTypedName = typedName;
    renderRecognitionCandidates(candidates);

    const nameModal = document.getElementById('name-modal');
    if (nameModal) {
        nameModal.classList.add('hidden');
        nameModal.style.display = 'none';
    }
    const modal = document.getElementById('recognition-modal');
    if (modal) {
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
    }
}

// "None of these — I'm new" from the recognition modal.
export async function chooseNewIdentity() {
    if (!_pendingTypedName) return;
    await finishIdentityResolution(generateUUID(), _pendingTypedName);
    _pendingTypedName = null;
}

// Save user name from modal — looks up existing identities for the typed
// name and either claims straight through (no match — "nothing changes
// from today") or shows the recognition list for the guest to resolve.
export async function saveUserName() {
    const nameInput = document.getElementById('name-modal-input');
    if (!nameInput) return;

    const name = nameInput.value.trim();
    if (!name) {
        alert('Please enter your name');
        return;
    }

    const candidates = await lookupCandidates(name);
    if (candidates.length === 0) {
        await finishIdentityResolution(userId || generateUUID(), name);
        return;
    }

    showRecognitionModal(name, candidates);
}

// Check operator status on page load
export async function checkOperatorStatus() {
    try {
        const response = await fetch('/api/auth/operator');
        if (response.ok) {
            const data = await response.json();
            setIsOperator(data.operator);
        }
    } catch (e) {
        console.error('Error checking operator status:', e);
    }
}

// Show operator PIN modal
export function showOperatorPinModal(action) {
    setPendingOperatorAction(action);
    const modal = document.getElementById('operator-pin-modal');
    const pinInput = document.getElementById('operator-pin-input');
    const messageDiv = document.getElementById('operator-pin-message');
    if (modal) {
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
        if (pinInput) {
            pinInput.value = '';
            setTimeout(() => pinInput.focus(), 100);
        }
        if (messageDiv) {
            messageDiv.innerHTML = '';
        }
    }
}

// Cancel operator PIN entry
export function cancelOperatorPin() {
    const modal = document.getElementById('operator-pin-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
    }
    setPendingOperatorAction(null);
}

// Submit operator PIN
export async function submitOperatorPin() {
    const pinInput = document.getElementById('operator-pin-input');
    const messageDiv = document.getElementById('operator-pin-message');

    if (!pinInput) return;

    const pin = pinInput.value.trim();
    if (!pin) {
        if (messageDiv) {
            messageDiv.innerHTML = '<div class="error">Please enter a PIN</div>';
        }
        return;
    }

    try {
        const response = await fetch('/api/auth/operator', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({pin: pin})
        });

        if (response.ok) {
            const data = await response.json();
            setIsOperator(data.operator);
            if (isOperator) {
                // Save pending action before closing modal (which clears it)
                const action = pendingOperatorAction;
                // Close modal
                cancelOperatorPin();
                // Execute pending action if any
                if (action) {
                    action();
                }
            }
        } else {
            const error = await response.json();
            if (messageDiv) {
                messageDiv.innerHTML = '<div class="error">Invalid PIN</div>';
            }
            pinInput.value = '';
            pinInput.focus();
        }
    } catch (e) {
        console.error('Error authenticating:', e);
        if (messageDiv) {
            messageDiv.innerHTML = '<div class="error">Error authenticating</div>';
        }
    }
}

// Prompt for operator authentication
export function promptOperatorAuth() {
    if (isOperator) {
        // Already authenticated - the button already shows a checkmark, so
        // there's nothing further to confirm.
        return;
    }
    // Show PIN modal, refresh page on success
    showOperatorPinModal(() => {
        // Reload page to show operator controls everywhere
        location.reload();
    });
}

// Update operator button to show auth state
export function updateOperatorButton() {
    const button = document.getElementById('operator-auth-button');
    if (button) {
        button.innerHTML = isOperator ? '✓' : '🔑';
        button.style.background = isOperator ? '#f39c12' : '#666';
        button.title = isOperator ? 'Operator (Authenticated)' : 'Operator Authentication';
    }
}

// Initialize user identity from localStorage
// Returns a promise that resolves once the user is registered with the server
// (i.e. the session has user_id set). Callers MUST await this before making
// any other API calls, otherwise a concurrent response's Set-Cookie will
// overwrite the session cookie and lose the user_id.
export async function initializeUserIdentity() {
    const storedName = localStorage.getItem('kbox_user_name');
    const storedId = localStorage.getItem('kbox_user_id');
    setUserName(storedName);
    setUserId(storedId);

    if (!userName || !userId) {
        // Show name prompt modal (will generate userId when name is entered)
        const modal = document.getElementById('name-modal');
        if (modal) {
            modal.classList.remove('hidden');
            modal.style.display = 'flex';
            // Handle Enter key in name input
            const nameInput = document.getElementById('name-modal-input');
            if (nameInput) {
                nameInput.addEventListener('keypress', function(e) {
                    if (e.key === 'Enter') {
                        saveUserName();
                    }
                });
                // Focus the input
                setTimeout(() => nameInput.focus(), 100);
            }
        }
        // Wait for saveUserName() to register and resolve identityReadyPromise.
        // This does not block the UI — the modal is already visible and
        // interactive; it just delays the caller's subsequent API calls.
        await identityReadyPromise;
    } else {
        // Existing user - register/update with server to ensure display name is current
        // Must await so the session cookie with user_id is set before other API calls
        await registerUser(userId, userName);
        _resolveIdentityReady();
    }
}

// Set up PIN input Enter key handler
export function setupPinInputHandler() {
    const pinInput = document.getElementById('operator-pin-input');
    if (pinInput) {
        pinInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                submitOperatorPin();
            }
        });
    }
}
