/**
 * Main entry point for kbox web UI.
 * Imports all modules and attaches functions to window for HTML onclick handlers.
 */

// Import all modules
import { escapeHtml } from './utils.js';
import {
    saveUserName, checkOperatorStatus, showOperatorPinModal, cancelOperatorPin,
    submitOperatorPin, promptOperatorAuth, updateOperatorButton,
    initializeUserIdentity, setupPinInputHandler, chooseNewIdentity
} from './auth.js';
import { adjustPitch } from './pitch.js';
import { toggleControlsLock, togglePlaybackControlsLock, resetPlaybackAutoLockTimer } from './controls.js';
import {
    toggleConfigScreen, loadConfiguration, saveConfiguration,
    toggleCustomInput, updateSliderDisplay, selectPosition
} from './config.js';
import {
    loadQueue, showEditQueueItemModal, cancelEditQueueItem, saveQueueItemPitch,
    jumpToQueueItem, playNextQueueItem, moveToEndQueueItem, removeQueueItem,
    moveUpQueueItem, moveDownQueueItem, clearQueue, replaceQueueItem
} from './queue.js';
import {
    search, showAddSongModal, cancelAddToQueue, confirmAddToQueue, setupSearchHandlers,
    getSuggestions
} from './search.js';
import {
    togglePlayPause, stopPlayback, playback, restartSong, seekForward, seekBackward
} from './playback.js';
import { showHelp, hideHelp, showHistoryModal, hideHistoryModal } from './modals.js';
import { loadFavorites, showFavoritesModal, hideFavoritesModal } from './favorites.js';

// Attach functions to window for HTML onclick handlers
// Auth
window.saveUserName = saveUserName;
window.chooseNewIdentity = chooseNewIdentity;
window.showOperatorPinModal = showOperatorPinModal;
window.cancelOperatorPin = cancelOperatorPin;
window.submitOperatorPin = submitOperatorPin;
window.promptOperatorAuth = promptOperatorAuth;

// Pitch
window.adjustPitch = adjustPitch;

// Controls
window.toggleControlsLock = toggleControlsLock;
window.togglePlaybackControlsLock = togglePlaybackControlsLock;
window.resetPlaybackAutoLockTimer = resetPlaybackAutoLockTimer;

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
window.replaceQueueItem = replaceQueueItem;
window.moveUpQueueItem = moveUpQueueItem;
window.moveDownQueueItem = moveDownQueueItem;
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
window.showFavoritesModal = showFavoritesModal;
window.hideFavoritesModal = hideFavoritesModal;

// Initialize on page load
document.addEventListener('DOMContentLoaded', async function() {
    // Set up UI handlers first (no API calls, safe before registration)
    setupPinInputHandler();
    updateOperatorButton();
    setupSearchHandlers();

    // Initialize user identity and AWAIT registration with server. For a
    // brand-new guest this doesn't resolve until they submit the name modal
    // and registerUser() completes (see auth.js). This must complete before
    // any other API calls so the session cookie has user_id set — otherwise
    // a concurrent poll response can overwrite the cookie and lose it.
    await initializeUserIdentity();

    // Now safe to make API calls - session cookie has user_id.
    // Await operator status before the first loadQueue() so its initial
    // render (which shows/hides #playback-controls-section based on
    // isOperator) is correct immediately, instead of depending on a later
    // polling tick to catch up.
    await checkOperatorStatus();
    loadQueue();
    loadFavorites();

    // Auto-refresh queue every 1 second
    setInterval(loadQueue, 1000);
});
