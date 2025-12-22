"""
Platform-specific code for macOS and Linux.

This module isolates platform-specific functionality to keep the main codebase
platform-agnostic. Includes GStreamer sink creation for different platforms.
"""

import logging
import sys
import ctypes
import os
import subprocess
import threading
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)


def is_macos() -> bool:
    """Check if running on macOS."""
    return sys.platform == "darwin"


def find_gstreamer_library() -> Optional[str]:
    """
    Find the GStreamer library path on macOS.

    Returns:
        Path to libgstreamer-1.0.dylib, or None if not found
    """
    if not is_macos():
        return None

    # Try common Homebrew locations
    possible_paths = [
        os.path.join(os.path.expanduser("~/.homebrew"), "lib/libgstreamer-1.0.dylib"),
        "/opt/homebrew/lib/libgstreamer-1.0.dylib",
    ]

    for path in possible_paths:
        if os.path.exists(path):
            return path

    # Try to find via brew command
    try:
        result = subprocess.run(
            ["brew", "--prefix", "gstreamer"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            gst_prefix = result.stdout.strip()
            path = f"{gst_prefix}/lib/libgstreamer-1.0.dylib"
            if os.path.exists(path):
                return path
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        pass

    return None


def run_with_gst_macos_main(main_func: Callable[[], int]) -> int:
    """
    Run the main function using gst_macos_main() on macOS.

    This is required for proper GStreamer video support on macOS, as it sets up
    the NSRunLoop that GStreamer needs.

    Args:
        main_func: The main function to run

    Returns:
        Exit code from main_func, or 0 if gst_macos_main is not available
    """
    if not is_macos():
        return main_func()

    gst_lib_path = find_gstreamer_library()
    if not gst_lib_path:
        logger.warning(
            "Could not find libgstreamer-1.0.dylib, falling back to regular main"
        )
        return main_func()

    try:
        # Load GStreamer library
        gst_lib = ctypes.CDLL(gst_lib_path)

        # Define GstMainFunc type
        GstMainFunc = ctypes.CFUNCTYPE(
            ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)
        )

        # Get gst_macos_main function
        gst_macos_main = gst_lib.gst_macos_main
        gst_macos_main.argtypes = [
            GstMainFunc,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.c_void_p,
        ]
        gst_macos_main.restype = ctypes.c_int

        # Convert argv to ctypes format
        argc = len(sys.argv)
        argv = (ctypes.c_char_p * (argc + 1))()
        for i, arg in enumerate(sys.argv):
            argv[i] = arg.encode("utf-8")
        argv[argc] = None

        # Create wrapper function
        def wrapper(argc, argv):
            return main_func()

        # Call gst_macos_main
        return gst_macos_main(GstMainFunc(wrapper), argc, argv, None)
    except Exception as e:
        logger.warning(
            "Could not use gst_macos_main: %s, falling back to regular main", e
        )
        return main_func()


def run_uvicorn_in_thread(uvicorn_server) -> Tuple[threading.Thread, Callable]:
    """
    Run uvicorn server in a background thread on macOS.

    On macOS, we need to run uvicorn in a thread so the main thread can run
    the NSRunLoop required by GStreamer.

    Args:
        uvicorn_server: The uvicorn.Server instance
f
    Returns:
        Tuple of (thread, wait_function)
        The wait_function should be called to wait for the server thread
    """
    if not is_macos():
        raise ValueError("run_uvicorn_in_thread is macOS-only")

    def run_server():
        uvicorn_server.run()

    server_thread = threading.Thread(
        target=run_server, daemon=False, name="UvicornServer"
    )
    server_thread.start()

    def wait_for_shutdown():
        """Wait for thread completion."""
        try:
            server_thread.join()
        except KeyboardInterrupt:
            uvicorn_server.should_exit = True
            raise

    return server_thread, wait_for_shutdown


def create_video_sink(use_fakesinks: bool = False):
    """
    Create appropriate video sink for the current platform.
    
    Args:
        use_fakesinks: If True, create a fakesink for headless testing (internal use only)
        
    Returns:
        GStreamer video sink element
        
    Raises:
        RuntimeError: If unable to create a video sink
    """
    try:
        from gi.repository import Gst
    except ImportError:
        raise RuntimeError("GStreamer Python bindings not available")
    
    if use_fakesinks:
        sink = Gst.ElementFactory.make('fakesink', 'video_sink')
        if sink is None:
            raise RuntimeError("Failed to create fakesink for headless testing")
        return sink
    
    if sys.platform == 'linux':
        # Use kmssink for direct kernel mode setting (Raspberry Pi)
        sink = Gst.ElementFactory.make('kmssink', 'video_sink')
        if sink is None:
            logger.warning("kmssink not available, falling back to autovideosink")
            sink = Gst.ElementFactory.make('autovideosink', 'video_sink')
        if sink is None:
            raise RuntimeError("No video sink available on Linux")
        return sink
    else:  # macOS
        # Try autovideosink first (auto-detects best sink)
        sink = Gst.ElementFactory.make('autovideosink', 'video_sink')
        if sink is None:
            # Fallback to osxvideosink
            sink = Gst.ElementFactory.make('osxvideosink', 'video_sink')
        if sink is None:
            raise RuntimeError("No video sink available on macOS")
        return sink


def create_audio_sink(use_fakesinks: bool = False, device: Optional[str] = None):
    """
    Create appropriate audio sink for the current platform.
    
    Args:
        use_fakesinks: If True, create a fakesink for headless testing (internal use only)
        device: ALSA device name (Linux only), e.g., 'plughw:CARD=USB,DEV=0'
        
    Returns:
        GStreamer audio sink element
        
    Raises:
        RuntimeError: If unable to create an audio sink
    """
    try:
        from gi.repository import Gst
    except ImportError:
        raise RuntimeError("GStreamer Python bindings not available")
    
    if use_fakesinks:
        sink = Gst.ElementFactory.make('fakesink', 'audio_sink')
        if sink is None:
            raise RuntimeError("Failed to create fakesink for headless testing")
        return sink
    
    if sys.platform == 'linux':
        # Use alsasink for ALSA (Raspberry Pi)
        sink = Gst.ElementFactory.make('alsasink', 'audio_sink')
        if sink is None:
            raise RuntimeError("alsasink not available on Linux")
        if device:
            sink.set_property('device', device)
        return sink
    else:  # macOS
        # Use autoaudiosink (auto-detects best sink)
        sink = Gst.ElementFactory.make('autoaudiosink', 'audio_sink')
        if sink is None:
            raise RuntimeError("No audio sink available on macOS")
        return sink
