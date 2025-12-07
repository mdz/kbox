#!/usr/bin/env python3
"""Simple test script for GStreamer video playback on macOS."""

import sys
import time
import threading
import ctypes
import gi
gi.require_version('GLib', '2.0')
gi.require_version('GObject', '2.0')
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst

# Define GstMainFunc type for ctypes
GstMainFunc = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p))

def actual_main():
    # Initialize GStreamer
    Gst.init(None)
    
    # Get video file path
    if len(sys.argv) > 1:
        video_path = sys.argv[1]
    else:
        video_path = "/Users/zero/.kbox/cache/9Lxm0iSnKNc.mp4"
    
    print(f"Playing: {video_path}")
    
    # Create playbin
    pipeline = Gst.Pipeline.new("test-pipeline")
    playbin = Gst.ElementFactory.make("playbin", "playbin")
    
    if not playbin:
        print("ERROR: Could not create playbin")
        return 1
    
    # Set URI
    playbin.set_property("uri", f"file://{video_path}")
    
    # Set video sink - try specific sinks first
    video_sink = None
    for sink_name in ["osxvideosink", "glimagesink", "autovideosink"]:
        video_sink = Gst.ElementFactory.make(sink_name, "video_sink")
        if video_sink:
            print(f"Video sink: {sink_name}")
            break
    
    if video_sink:
        playbin.set_property("video-sink", video_sink)
        # Try to set some properties
        try:
            video_sink.set_property("sync", True)
        except:
            pass
    else:
        print("ERROR: Could not create any video sink")
        return 1
    
    # Set audio sink
    audio_sink = Gst.ElementFactory.make("autoaudiosink", "audio_sink")
    if audio_sink:
        playbin.set_property("audio-sink", audio_sink)
        print("Audio sink: autoaudiosink")
    
    pipeline.add(playbin)
    
    # Set up bus for messages
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    
    def on_message(bus, message):
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"ERROR: {err}: {debug}")
            loop.quit()
        elif message.type == Gst.MessageType.EOS:
            print("End of stream")
            loop.quit()
        elif message.type == Gst.MessageType.STATE_CHANGED:
            if message.src == pipeline:
                old_state, new_state, pending_state = message.parse_state_changed()
                print(f"State changed: {old_state.value_name} -> {new_state.value_name}")
        elif message.type == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"WARNING: {warn}: {debug}")
        return True
    
    bus.connect("message", on_message)
    
    # Start playback
    print("Starting playback...")
    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        print("ERROR: Failed to start pipeline")
        return 1
    
    # Run GLib main loop
    print("Running GLib main loop (press Ctrl+C to stop)...")
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    
    # Cleanup
    pipeline.set_state(Gst.State.NULL)
    print("Done")
    return 0

def main():
    """Wrapper that uses gst_macos_main on macOS."""
    if sys.platform == 'darwin':
        try:
            # Load GStreamer library
            gst_lib = ctypes.CDLL('/opt/homebrew/lib/libgstreamer-1.0.dylib')
            
            # Get gst_macos_main function
            gst_macos_main = gst_lib.gst_macos_main
            gst_macos_main.argtypes = [GstMainFunc, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p), ctypes.c_void_p]
            gst_macos_main.restype = ctypes.c_int
            
            # Convert argv to ctypes format
            argc = len(sys.argv)
            argv = (ctypes.c_char_p * (argc + 1))()
            for i, arg in enumerate(sys.argv):
                argv[i] = arg.encode('utf-8')
            argv[argc] = None
            
            # Create wrapper function
            def wrapper(argc, argv):
                return actual_main()
            
            # Call gst_macos_main
            return gst_macos_main(GstMainFunc(wrapper), argc, argv, None)
        except Exception as e:
            print(f"Warning: Could not use gst_macos_main: {e}")
            print("Falling back to regular main...")
            return actual_main()
    else:
        return actual_main()

if __name__ == "__main__":
    sys.exit(main())

