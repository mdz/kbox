/**
 * Search and add song functions for kbox web UI.
 */

import { userName, userId, currentVideoToAdd, setCurrentVideoToAdd } from './state.js';
import { renderSongSettings } from './song-settings.js';
import { loadQueue } from './queue.js';

// Search for videos
export async function search() {
    const query = document.getElementById('search-input').value;
    
    if (!query) {
        alert('Please enter a search query');
        return;
    }
    
    if (!userName) {
        alert('Please enter your name first');
        document.getElementById('name-modal').classList.remove('hidden');
        return;
    }
    
    const resultsDiv = document.getElementById('search-results');
    resultsDiv.innerHTML = 'Searching...';
    
    try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        const data = await response.json();
        
        resultsDiv.innerHTML = '';
        data.results.forEach(video => {
            const div = document.createElement('div');
            div.className = 'search-result';
            div.innerHTML = `
                <img src="${video.thumbnail}" alt="${video.title}" />
                <div class="search-result-info">
                    <div class="search-result-title">${video.title}</div>
                    <div class="search-result-channel">${video.channel}</div>
                </div>
            `;
            div.onclick = () => showAddSongModal(video);
            resultsDiv.appendChild(div);
        });
    } catch (e) {
        resultsDiv.innerHTML = '<div class="error">Error searching</div>';
    }
}

// Show add song confirmation modal
export async function showAddSongModal(video) {
    if (!userName) {
        alert('Please enter your name first');
        document.getElementById('name-modal').classList.remove('hidden');
        return;
    }
    
    setCurrentVideoToAdd(video);
    
    // Fetch saved settings for this video and user (pitch preset, etc.)
    let savedPitch = 0;
    try {
        const settingsResponse = await fetch(`/api/queue/settings/${encodeURIComponent(video.id)}?user_id=${encodeURIComponent(userId)}`);
        if (settingsResponse.ok) {
            const settingsData = await settingsResponse.json();
            savedPitch = settingsData.settings?.pitch_semitones || 0;
        }
    } catch (e) {
        // If fetch fails, just use default 0
        console.debug('Could not fetch saved settings:', e);
    }
    
    // Use reusable song settings component with saved pitch
    renderSongSettings('add-song-modal-content', {
        title: video.title,
        thumbnail_url: video.thumbnail,
        channel: video.channel,
        duration_seconds: video.duration_seconds,
        pitch_semitones: savedPitch
    }, {
        context: 'add-song',
        live: false,
        showStatus: false,
        showThumbnail: true,
        showUser: true
    });
    
    // Show modal
    const modal = document.getElementById('add-song-modal');
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
}

// Cancel adding song
export function cancelAddToQueue() {
    const modal = document.getElementById('add-song-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
    setCurrentVideoToAdd(null);
}

// Confirm and add song to queue
export async function confirmAddToQueue() {
    if (!currentVideoToAdd) return;
    
    const pitchInput = document.getElementById('add-song-pitch-input');
    if (!pitchInput) {
        alert('Pitch control not initialized');
        return;
    }
    const pitchSemitones = parseInt(pitchInput.value) || 0;
    
    try {
        const response = await fetch('/api/queue', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                user_id: userId,
                video_id: currentVideoToAdd.id,
                title: currentVideoToAdd.title,
                duration_seconds: currentVideoToAdd.duration_seconds,
                thumbnail_url: currentVideoToAdd.thumbnail,
                pitch_semitones: pitchSemitones
            })
        });
        
        if (response.ok) {
            const modal = document.getElementById('add-song-modal');
            modal.classList.add('hidden');
            modal.style.display = 'none';
            document.getElementById('search-input').value = '';
            document.getElementById('search-results').innerHTML = '';
            loadQueue();
            setCurrentVideoToAdd(null);
        } else {
            // Try to get error detail from response
            let errorMessage = 'Error adding song to queue';
            try {
                const errorData = await response.json();
                if (errorData.detail) {
                    errorMessage = errorData.detail;
                }
            } catch (parseError) {
                // Ignore parse errors, use default message
            }
            alert(errorMessage);
        }
    } catch (e) {
        alert('Error adding song to queue');
    }
}

// Set up search input handlers
export function setupSearchHandlers() {
    // Add Enter key support for search input
    document.getElementById('search-input').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            search();
        }
    });
    
    // iOS Safari fix: add touchend handler for search button
    // This helps when the keyboard is open and click events don't fire properly
    document.getElementById('search-button').addEventListener('touchend', function(e) {
        e.preventDefault();
        search();
    });
}
