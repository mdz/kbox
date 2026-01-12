/**
 * Reusable song settings component for kbox web UI.
 */

import { userId } from './state.js';
import { getIntervalName, createPitchControlHTML, updatePitchButtons } from './pitch.js';

// Render reusable song settings component
// songData: { title, thumbnail_url, user_name/channel, duration_seconds, download_status?, pitch_semitones }
// options: { context, live, fontSize, showStatus, showThumbnail, additionalControls }
export function renderSongSettings(containerId, songData, options = {}) {
    const container = document.getElementById(containerId);
    if (!container) {
        console.error('Container not found:', containerId);
        return;
    }
    
    const {
        context = 'song-settings',
        live = false,
        fontSize = 24,
        showStatus = false,
        showThumbnail = true,
        showUser = true,
        showPitchControls = true,
        additionalControls = '' // For future extensions like volume
    } = options;
    
    // Format duration/progress
    function formatTime(secs) {
        const m = Math.floor(secs / 60);
        const s = secs % 60;
        return `${m}:${s.toString().padStart(2, '0')}`;
    }
    const duration = songData.duration_seconds || 0;
    const position = songData.position_seconds;
    let durationText;
    if (position !== undefined && position !== null && duration > 0) {
        // Show progress: "1:23 / 3:45"
        durationText = `${formatTime(position)} / ${formatTime(duration)}`;
    } else if (duration > 0) {
        durationText = formatTime(duration);
    } else {
        durationText = 'Unknown';
    }
    
    // Get user/channel name (highlight if it's the current user)
    const isCurrentUser = songData.user_id === userId;
    const userText = songData.user_name 
        ? (isCurrentUser 
            ? `🎤 <strong style="color: #4aff6e;">Your song!</strong>` 
            : `Requested by: ${songData.user_name}`)
        : (songData.channel ? songData.channel : '');
    
    // Build song info HTML
    let thumbnailHTML = '';
    if (showThumbnail && songData.thumbnail_url) {
        thumbnailHTML = `<img src="${songData.thumbnail_url}" alt="${songData.title || ''}" style="width: 100%; max-width: 240px; height: auto; object-fit: cover; border-radius: 4px; display: block; margin: 0 auto 15px auto;" />`;
    }
    
    // Progress bar (only when playing)
    const progressPercent = (position !== undefined && position !== null && duration > 0) 
        ? Math.min(100, (position / duration) * 100) 
        : 0;
    const progressBarHTML = (position !== undefined && position !== null) 
        ? `<div style="margin-top: 8px; height: 4px; background: #333; border-radius: 2px; overflow: hidden;">
             <div style="height: 100%; width: ${progressPercent}%; background: #4a9eff; transition: width 0.3s;"></div>
           </div>`
        : '';
    
    const infoHTML = `
        <div>
            <div style="font-weight: 500; margin-bottom: 8px; font-size: 1.1em;">${songData.title || 'Unknown'}</div>
            ${showUser && userText ? `<div style="color: #aaa; margin-bottom: 5px; font-size: 0.9em;">${userText}</div>` : ''}
            <div style="color: #888; font-size: 0.85em;" id="${context}-progress">${position !== undefined && position !== null ? '' : 'Duration: '}${durationText}</div>
            ${progressBarHTML}
            ${showStatus && songData.download_status ? `<div style="color: #888; font-size: 0.85em; margin-top: 3px;">Status: ${songData.download_status}</div>` : ''}
        </div>
    `;
    
    const pitchValue = songData.pitch_semitones || 0;
    
    // Build song info section (only if we have info to show)
    const hasSongInfo = (thumbnailHTML || songData.title) && (showThumbnail || songData.title);
    const songInfoSection = hasSongInfo ? `<div style="margin-bottom: 20px;">${thumbnailHTML}${infoHTML}</div>` : '';
    
    // Build pitch control section (only if enabled)
    const pitchSection = showPitchControls ? `
        <div style="margin-bottom: 15px;">
            <label class="pitch-label" style="${live ? 'text-align: center; display: block; margin-bottom: 10px; color: #fff; font-weight: 500; font-size: 1.1em;' : ''}">${live ? 'Adjust Pitch (Live)' : 'Pitch Adjustment (semitones):'}</label>
            <div id="${context}-pitch-control-container"></div>
        </div>
    ` : '';
    
    // Build the complete HTML
    const html = `
        ${songInfoSection}
        ${pitchSection}
        ${additionalControls}
    `;
    
    container.innerHTML = html;
    
    // Initialize pitch control (only if showing)
    if (showPitchControls) {
        const pitchControlContainer = document.getElementById(`${context}-pitch-control-container`);
        if (pitchControlContainer) {
            pitchControlContainer.innerHTML = createPitchControlHTML(context, pitchValue, fontSize);
            updatePitchButtons(context, pitchValue);
        }
    }
}
