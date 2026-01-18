/**
 * Modal functions for kbox web UI.
 */

import { userName, userId } from './state.js';
import { escapeHtml } from './utils.js';

// Show help modal
export function showHelp() {
    const modal = document.getElementById('help-modal');
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
}

// Hide help modal
export function hideHelp() {
    const modal = document.getElementById('help-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
}

// Show history modal
export async function showHistoryModal() {
    if (!userName) {
        alert('Please set your name first');
        return;
    }

    const modal = document.getElementById('history-modal');
    const content = document.getElementById('history-content');

    // Show loading
    content.innerHTML = '<p style="text-align: center; color: #aaa;">Loading your history...</p>';
    modal.classList.remove('hidden');
    modal.style.display = 'flex';

    try {
        const response = await fetch(`/api/history/${encodeURIComponent(userId)}`);
        const data = await response.json();

        if (data.history && data.history.length > 0) {
            content.innerHTML = data.history.map(record => {
                const date = new Date(record.performed_at);
                const dateStr = date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
                const pitchStr = record.pitch_semitones ? ` (${record.pitch_semitones > 0 ? '+' : ''}${record.pitch_semitones})` : '';
                const completionStr = record.completion_percentage ? ` • ${Math.round(record.completion_percentage)}%` : '';

                return `
                    <div style="padding: 15px; margin-bottom: 10px; background: #2a2a2a; border-radius: 8px;">
                        <div style="font-weight: bold; margin-bottom: 5px;">${escapeHtml(record.title)}</div>
                        <div style="font-size: 0.9em; color: #aaa;">
                            ${dateStr}${pitchStr}${completionStr}
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            content.innerHTML = '<p style="text-align: center; color: #aaa;">No history yet. Sing some songs!</p>';
        }
    } catch (error) {
        console.error('Error loading history:', error);
        content.innerHTML = '<p style="text-align: center; color: #f66;">Error loading history</p>';
    }
}

// Hide history modal
export function hideHistoryModal() {
    const modal = document.getElementById('history-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
}
