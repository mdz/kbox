/**
 * Non-blocking toast notifications.
 *
 * Text is always set via textContent - callers pass untrusted strings such
 * as song titles.
 */

const TOAST_DURATION_MS = 4500;
const ERROR_DURATION_MS = 6000;

let dismissTimer = null;

function getToastEl() {
    let el = document.getElementById('toast');
    if (!el) {
        el = document.createElement('div');
        el.id = 'toast';
        el.className = 'toast hidden';
        // role=status is implicitly aria-live=polite; spelled out for older AT.
        el.setAttribute('role', 'status');
        el.setAttribute('aria-live', 'polite');
        el.addEventListener('click', hideToast);
        document.body.appendChild(el);
    }
    return el;
}

export function hideToast() {
    clearTimeout(dismissTimer);
    dismissTimer = null;
    const el = document.getElementById('toast');
    if (el) el.classList.add('hidden');
}

/**
 * Show a toast, replacing any currently visible one.
 * @param {string} message - plain text
 * @param {{error?: boolean, durationMs?: number}} [opts]
 */
export function showToast(message, opts = {}) {
    const el = getToastEl();
    el.textContent = message;
    el.classList.toggle('toast-error', !!opts.error);
    el.classList.remove('hidden');
    clearTimeout(dismissTimer);
    dismissTimer = setTimeout(
        hideToast,
        opts.durationMs ?? (opts.error ? ERROR_DURATION_MS : TOAST_DURATION_MS)
    );
}
