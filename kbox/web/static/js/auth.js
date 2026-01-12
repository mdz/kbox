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

// Save user name from modal
export function saveUserName() {
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
    
    // Register with server
    registerUser(userId, userName);
    
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
export function initializeUserIdentity() {
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
    } else {
        // Existing user - register/update with server to ensure display name is current
        registerUser(userId, userName);
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
