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

// Resolves once identity registration has landed server-side (session cookie
// has user_id). Callers that need an authenticated endpoint should await
// waitForIdentity() first — see fetchAuthed() below, which does this
// automatically and also recovers if the cookie gets clobbered afterward.
let _resolveIdentityReady;
let identityReadyPromise = new Promise((resolve) => {
    _resolveIdentityReady = resolve;
});

export function waitForIdentity() {
    return identityReadyPromise;
}

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

// Fetch wrapper for endpoints that require an authenticated session
// (require_user on the backend). Waits for initial identity registration,
// and if the session cookie was clobbered by a concurrent unauthenticated
// poll response in the meantime (a known race — see auth.js history),
// re-registers once and retries instead of surfacing a 401 to the user.
export async function fetchAuthed(url, options) {
    await identityReadyPromise;
    let response = await fetch(url, options);
    if (response.status === 401) {
        await registerUser(userId, userName);
        response = await fetch(url, options);
    }
    return response;
}

// Save user name from modal
export async function saveUserName() {
    const nameInput = document.getElementById('name-modal-input');
    if (!nameInput) return;

    const name = nameInput.value.trim();
    if (!name) {
        alert('Please enter your name');
            return;
        }
    setUserName(name);
    // Generate new UUID if we don't have one, otherwise keep existing
    if (!userId) {
        setUserId(generateUUID());
    }
    localStorage.setItem('kbox_user_name', userName);
    localStorage.setItem('kbox_user_id', userId);

    // Register with server - must await so the session cookie has user_id
    // before any concurrent loadQueue tick overwrites it
    await registerUser(userId, userName);
    _resolveIdentityReady();

    // Hide modal
    const modal = document.getElementById('name-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
    }
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
        // Already authenticated, show status
        alert('You are authenticated as operator');
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
