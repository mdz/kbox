"""
Platform-specific code for macOS.

This module isolates all macOS-specific functionality to keep the main codebase
platform-agnostic.
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
