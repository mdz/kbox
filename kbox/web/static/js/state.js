/**
 * Shared state for kbox web UI.
 * All modules import from here to share state.
 */

// User and operator state
export let isOperator = false;
export let currentQueue = [];
export let userName = null;
export let userId = null;

// Controls lock state (for pitch controls in Now Playing)
export let controlsLocked = true;
export let autoLockTimer = null;
export const AUTO_LOCK_TIMEOUT = 30000; // 30 seconds

// Playback controls lock state (separate from pitch controls)
export let playbackControlsLocked = true;
export let playbackAutoLockTimer = null;
export const PLAYBACK_AUTO_LOCK_TIMEOUT = 30000; // 30 seconds

// Store pending action to execute after PIN authentication
export let pendingOperatorAction = null;

// Store current config data for saving
export let currentConfigData = null;

// Store current queue item being edited
export let currentQueueItemToEdit = null;

// Queue depth (estimated wait time, computed by backend)
export let queueDepthSeconds = 0;
export let queueDepthCount = 0;

// Store current video being added
export let currentVideoToAdd = null;

// Setters for state that needs to be modified from other modules
export function setIsOperator(value) { isOperator = value; }
export function setCurrentQueue(value) { currentQueue = value; }
export function setUserName(value) { userName = value; }
export function setUserId(value) { userId = value; }
export function setControlsLocked(value) { controlsLocked = value; }
export function setAutoLockTimer(value) { autoLockTimer = value; }
export function setPlaybackControlsLocked(value) { playbackControlsLocked = value; }
export function setPlaybackAutoLockTimer(value) { playbackAutoLockTimer = value; }
export function setPendingOperatorAction(value) { pendingOperatorAction = value; }
export function setCurrentConfigData(value) { currentConfigData = value; }
export function setCurrentQueueItemToEdit(value) { currentQueueItemToEdit = value; }
export function setQueueDepthSeconds(value) { queueDepthSeconds = value; }
export function setQueueDepthCount(value) { queueDepthCount = value; }
export function setCurrentVideoToAdd(value) { currentVideoToAdd = value; }
