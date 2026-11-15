/**
 * Utility functions for kbox web UI.
 */

// Generate UUID (with fallback for non-secure contexts)
export function generateUUID() {
    // Try native crypto.randomUUID first (requires HTTPS or localhost)
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    // Fallback: generate UUID v4 manually
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// Utility function to escape HTML
export function escapeHtml(text) {
    // Escape quotes too so the result is safe inside attribute values.
    return String(text ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Guest-friendly labels for queue item content_status values
// (see QueueManager.STATUS_*). Shared by the queue rows and the turn bar.
const STATUS_LABELS = {
    pending: 'Getting ready…',
    preparing: 'Getting ready…',
    ready: 'Ready',
    error: 'Download failed',
};

export function statusLabel(contentStatus) {
    return STATUS_LABELS[contentStatus] || contentStatus;
}
