/**
 * Main entry point for kbox web UI.
 * Imports all modules and attaches functions to window for HTML onclick handlers.
 */

// Import all modules
import { escapeHtml } from './utils.js';
import {
    saveUserName, checkOperatorStatus, showOperatorPinModal, cancelOperatorPin,
    submitOperatorPin, promptOperatorAuth, updateOperatorButton,
    initializeUserIdentity, setupPinInputHandler
} from './auth.js';
import { adjustPitch } from './pitch.js';
import { toggleControlsLock, togglePlaybackControlsLock } from './controls.js';
import {
    toggleConfigScreen, loadConfiguration, saveConfiguration,
    toggleCustomInput, updateSliderDisplay, selectPosition
} from './config.js';
import {
    loadQueue, showEditQueueItemModal, cancelEditQueueItem, saveQueueItemPitch,
    jumpToQueueItem, playNextQueueItem, moveToEndQueueItem, removeQueueItem,
    moveUpQueueItem, moveDownQueueItem, removeOwnQueueItem, clearQueue
} from './queue.js';
import {
    search, showAddSongModal, cancelAddToQueue, confirmAddToQueue, setupSearchHandlers,
    getSuggestions
} from './search.js';
import {
    togglePlayPause, stopPlayback, playback, restartSong, seekForward, seekBackward
} from './playback.js';
import { showHelp, hideHelp, showHistoryModal, hideHistoryModal } from './modals.js';

// Attach functions to window for HTML onclick handlers
// Auth
window.saveUserName = saveUserName;
window.showOperatorPinModal = showOperatorPinModal;
window.cancelOperatorPin = cancelOperatorPin;
window.submitOperatorPin = submitOperatorPin;
window.promptOperatorAuth = promptOperatorAuth;

// Pitch
window.adjustPitch = adjustPitch;

// Controls
window.toggleControlsLock = toggleControlsLock;
window.togglePlaybackControlsLock = togglePlaybackControlsLock;

// Config
window.toggleConfigScreen = toggleConfigScreen;
window.saveConfiguration = saveConfiguration;
window.toggleCustomInput = toggleCustomInput;
window.updateSliderDisplay = updateSliderDisplay;
window.selectPosition = selectPosition;

// Queue
window.showEditQueueItemModal = showEditQueueItemModal;
window.cancelEditQueueItem = cancelEditQueueItem;
window.saveQueueItemPitch = saveQueueItemPitch;
window.jumpToQueueItem = jumpToQueueItem;
window.playNextQueueItem = playNextQueueItem;
window.moveToEndQueueItem = moveToEndQueueItem;
window.removeQueueItem = removeQueueItem;
window.moveUpQueueItem = moveUpQueueItem;
window.moveDownQueueItem = moveDownQueueItem;
window.removeOwnQueueItem = removeOwnQueueItem;
window.clearQueue = clearQueue;

// Search
window.search = search;
window.showAddSongModal = showAddSongModal;
window.cancelAddToQueue = cancelAddToQueue;
window.confirmAddToQueue = confirmAddToQueue;
window.getSuggestions = getSuggestions;

// Playback
window.togglePlayPause = togglePlayPause;
window.stopPlayback = stopPlayback;
window.playback = playback;
window.restartSong = restartSong;
window.seekForward = seekForward;
window.seekBackward = seekBackward;

// Modals
window.showHelp = showHelp;
window.hideHelp = hideHelp;
window.showHistoryModal = showHistoryModal;
window.hideHistoryModal = hideHistoryModal;

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Initialize user identity
    initializeUserIdentity();
    
    // Check operator status
    checkOperatorStatus();
    
    // Set up PIN input handler
    setupPinInputHandler();
    
    // Update operator button
    updateOperatorButton();
    
    // Set up search handlers
    setupSearchHandlers();
    
    // Load queue on page load
    loadQueue();
    
    // Auto-refresh queue every 1 second
    setInterval(loadQueue, 1000);
});
