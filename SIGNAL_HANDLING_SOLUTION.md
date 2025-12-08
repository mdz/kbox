# Signal Handling Problem with gst_macos_main

## Problem
When using `gst_macos_main`, signals (SIGINT/SIGTERM) are NOT reaching Python's signal handlers. The signal handler is never called, so the shutdown event is never set, and the process hangs.

## Test Results

### WITHOUT gst_macos_main: ✅ WORKS
- Signal handler is called
- Shutdown event is set
- Server shuts down cleanly
- Process exits

### WITH gst_macos_main: ❌ FAILS
- Signal handler is NEVER called (even with self-sent SIGINT)
- Shutdown event is never set
- Process hangs and doesn't exit
- Must be killed with SIGKILL

## What We've Tried

1. Setting signal handlers BEFORE calling `gst_macos_main` - doesn't help
2. Using threading.Event for coordination - can't work if signal handler never runs
3. Polling loop in the function passed to `gst_macos_main` - works but has nothing to poll

## Root Cause
`gst_macos_main` runs an NSRunLoop that appears to intercept/consume signals before they reach Python's signal handlers. The NSRunLoop may be handling them at the Cocoa level.

## Possible Solutions (Not Yet Tested)

1. Use NSApplication terminate methods via PyObjC
2. Find a way to make the NSRunLoop respect Python signal handlers
3. Use a different mechanism to detect shutdown (file watch, socket, etc.)
4. Don't use `gst_macos_main` if video isn't needed immediately

## Status
**NO WORKING SOLUTION YET** - The process cannot be cleanly shut down with Ctrl+C when using `gst_macos_main`.


