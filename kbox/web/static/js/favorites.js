/**
 * Favorites (starred songs) functions for kbox web UI.
 */

import { userName, userId, favoriteVideoIds, setFavoriteVideoIds } from './state.js';
import { escapeHtml } from './utils.js';
import { showAddSongModal } from './search.js';

// Load the current user's favorited video IDs (used to render star state on search results)
export async function loadFavorites() {
    if (!userId) return;

    try {
        const response = await fetch(`/api/favorites/${encodeURIComponent(userId)}`);
        if (!response.ok) return;
        const data = await response.json();
        setFavoriteVideoIds(new Set((data.favorites || []).map(fav => fav.video_id)));
    } catch (e) {
        console.debug('Error loading favorites:', e);
    }
}

// Toggle favorite state for a video (video: {id, title, thumbnail, channel, duration_seconds})
export async function toggleFavorite(video, event) {
    if (event) event.stopPropagation();

    if (!userName) {
        alert('Please enter your name first');
        return;
    }

    const isFavorited = favoriteVideoIds.has(video.id);

    try {
        if (isFavorited) {
            const response = await fetch(`/api/favorites/${encodeURIComponent(video.id)}`, {method: 'DELETE'});
            if (!response.ok) throw new Error('Failed to unfavorite');
            favoriteVideoIds.delete(video.id);
        } else {
            const response = await fetch('/api/favorites', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    video_id: video.id,
                    title: video.title,
                    duration_seconds: video.duration_seconds,
                    thumbnail_url: video.thumbnail,
                    channel: video.channel
                })
            });
            if (!response.ok) throw new Error('Failed to favorite');
            favoriteVideoIds.add(video.id);
        }

        // Update any star buttons for this video currently on screen
        document.querySelectorAll(`.favorite-star[data-video-id="${cssEscape(video.id)}"]`).forEach(el => {
            el.classList.toggle('favorited', favoriteVideoIds.has(video.id));
            el.textContent = favoriteVideoIds.has(video.id) ? '★' : '☆';
        });
    } catch (e) {
        alert('Error updating favorite');
    }
}

// Minimal CSS.escape fallback for use in attribute selectors above
function cssEscape(value) {
    return String(value).replace(/["\\]/g, '\\$&');
}

// Render the star button HTML for a search result / favorites list item
export function renderFavoriteStar(video) {
    const isFavorited = favoriteVideoIds.has(video.id);
    return `<button type="button" class="favorite-star${isFavorited ? ' favorited' : ''}" data-video-id="${escapeHtml(video.id)}" title="${isFavorited ? 'Remove from favorites' : 'Save for later'}" aria-label="${isFavorited ? 'Remove from favorites' : 'Save for later'}">${isFavorited ? '★' : '☆'}</button>`;
}

// Attach the click handler for a rendered star button (call after inserting renderFavoriteStar's HTML)
export function bindFavoriteStar(starEl, video) {
    starEl.onclick = (e) => toggleFavorite(video, e);
}

// Show the favorites modal, listing all songs the user has starred
export async function showFavoritesModal() {
    if (!userName) {
        alert('Please set your name first');
        return;
    }

    const modal = document.getElementById('favorites-modal');
    const content = document.getElementById('favorites-content');

    content.innerHTML = '<p style="text-align: center; color: #aaa;">Loading your favorites...</p>';
    modal.classList.remove('hidden');
    modal.style.display = 'flex';

    try {
        const response = await fetch(`/api/favorites/${encodeURIComponent(userId)}`);
        const data = await response.json();

        if (data.favorites && data.favorites.length > 0) {
            content.innerHTML = '';
            data.favorites.forEach(fav => {
                const video = {
                    id: fav.video_id,
                    title: fav.title,
                    thumbnail: fav.thumbnail_url,
                    channel: fav.channel,
                    duration_seconds: fav.duration_seconds
                };

                const div = document.createElement('div');
                div.className = 'favorite-item';
                div.innerHTML = `
                    <img src="${fav.thumbnail_url || ''}" alt="${escapeHtml(fav.title)}" />
                    <div class="favorite-item-info">
                        <div class="favorite-item-title">${escapeHtml(fav.title)}</div>
                        <div class="favorite-item-channel">${escapeHtml(fav.channel || '')}</div>
                    </div>
                `;

                const starButton = document.createElement('button');
                starButton.type = 'button';
                starButton.className = 'favorite-star favorited';
                starButton.dataset.videoId = fav.video_id;
                starButton.title = 'Remove from favorites';
                starButton.setAttribute('aria-label', 'Remove from favorites');
                starButton.textContent = '★';
                starButton.onclick = async (e) => {
                    e.stopPropagation();
                    await toggleFavorite(video, e);
                    div.remove();
                    if (!content.children.length) {
                        content.innerHTML = '<p style="text-align: center; color: #aaa;">No favorites yet. Star a song from search results to save it for later!</p>';
                    }
                };
                div.appendChild(starButton);

                div.onclick = () => {
                    hideFavoritesModal();
                    showAddSongModal(video);
                };

                content.appendChild(div);
            });
        } else {
            content.innerHTML = '<p style="text-align: center; color: #aaa;">No favorites yet. Star a song from search results to save it for later!</p>';
        }
    } catch (error) {
        console.error('Error loading favorites:', error);
        content.innerHTML = '<p style="text-align: center; color: #f66;">Error loading favorites</p>';
    }
}

// Hide favorites modal
export function hideFavoritesModal() {
    const modal = document.getElementById('favorites-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
}
