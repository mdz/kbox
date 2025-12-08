#!/usr/bin/env python3
"""
Test signal handling: uvicorn in thread, main thread polls for shutdown.
After 3 seconds, sends itself SIGINT to test shutdown.

Usage:
  python test_signal_working.py           # Without gst_macos_main
  python test_signal_working.py --gst     # With gst_macos_main
"""

import sys
import signal
import threading
import logging
import os
import time
import ctypes
import subprocess
from typing import Callable, Optional
from fastapi import FastAPI
import uvicorn

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Global shutdown event - must be set up before gst_macos_main
shutdown_event = threading.Event()

def signal_handler(signum, frame):
    """Signal handler - must be simple and fast."""
    # Write to stderr directly to ensure it's seen even if logging is buffered
    import sys
    print(f"Signal {signum} received!", file=sys.stderr, flush=True)
    logger.info(f"Signal {signum} received, setting shutdown event")
    shutdown_event.set()

# Register signal handlers BEFORE gst_macos_main (must be in main thread)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def run_test():
    """Run the actual test."""

    app = FastAPI()

    @app.get("/")
    def root():
        return {"message": "Test server running"}

    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)

    def run_server():
        """Run uvicorn server in thread."""
        try:
            server.run()
        except Exception as e:
            if not shutdown_event.is_set():
                logger.error(f"Server error: {e}", exc_info=True)

    # Start server in thread
    server_thread = threading.Thread(target=run_server, daemon=False, name="UvicornServer")
    server_thread.start()

    logger.info("Server started on http://localhost:8000")
    logger.info(f"Process ID: {os.getpid()}")

    # Send SIGINT to self after 3 seconds
    def send_sigint():
        time.sleep(3)
        logger.info("Sending SIGINT to self...")
        os.kill(os.getpid(), signal.SIGINT)

    sigint_thread = threading.Thread(target=send_sigint, daemon=True)
    sigint_thread.start()

    try:
        # Main thread: poll for shutdown event (simulates NSRunLoop behavior)
        # When running under gst_macos_main, this loop runs inside the NSRunLoop
        # The signal handler should set shutdown_event, which we check here
        while not shutdown_event.is_set() and server_thread.is_alive():
            # Wait with short timeout so we can check if event is set
            if shutdown_event.wait(timeout=0.1):
                logger.info("Shutdown event detected, stopping server...")
                server.should_exit = True
                break
            # Also check explicitly (in case wait() didn't return True)
            if shutdown_event.is_set():
                logger.info("Shutdown event is set, stopping server...")
                server.should_exit = True
                break
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt caught")
        shutdown_event.set()
        server.should_exit = True

    # Wait for server thread to finish
    logger.info("Waiting for server thread to finish...")
    server_thread.join(timeout=3.0)
    if server_thread.is_alive():
        logger.error("FAILED: Server thread did not stop within timeout")
        return 1
    else:
        logger.info("SUCCESS: Server thread stopped cleanly")

    logger.info("Test completed successfully")
    # Return from this function causes gst_macos_main to return
    return 0


def find_gstreamer_library() -> Optional[str]:
    """Find the GStreamer library path on macOS."""
    if sys.platform != "darwin":
        return None

    possible_paths = [
        os.path.join(os.path.expanduser("~/.homebrew"), "lib/libgstreamer-1.0.dylib"),
        "/opt/homebrew/lib/libgstreamer-1.0.dylib",
    ]

    for path in possible_paths:
        if os.path.exists(path):
            return path

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
    
    Important: Signal handlers must be set up BEFORE calling this function,
    because gst_macos_main runs an NSRunLoop that may not be in the main Python thread.
    The function passed to gst_macos_main should poll for shutdown events and return
    when it's time to exit.
    """
    if sys.platform != "darwin":
        return main_func()

    gst_lib_path = find_gstreamer_library()
    if not gst_lib_path:
        logger.warning("Could not find libgstreamer-1.0.dylib, using regular main")
        return main_func()

    try:
        gst_lib = ctypes.CDLL(gst_lib_path)
        GstMainFunc = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p))
        gst_macos_main = gst_lib.gst_macos_main
        gst_macos_main.argtypes = [
            GstMainFunc,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.c_void_p,
        ]
        gst_macos_main.restype = ctypes.c_int

        argc = len(sys.argv)
        argv = (ctypes.c_char_p * (argc + 1))()
        for i, arg in enumerate(sys.argv):
            argv[i] = arg.encode("utf-8")
        argv[argc] = None

        def wrapper(argc, argv):
            # This runs inside gst_macos_main's NSRunLoop
            # It should poll for shutdown and return when done
            return main_func()

        result = gst_macos_main(GstMainFunc(wrapper), argc, argv, None)
        logger.info(f"gst_macos_main returned: {result}")
        return result
    except Exception as e:
        logger.warning("Could not use gst_macos_main: %s, using regular main", e)
        return main_func()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--gst":
        logger.info("Running WITH gst_macos_main")
        sys.exit(run_with_gst_macos_main(run_test))
    else:
        logger.info("Running WITHOUT gst_macos_main")
        sys.exit(run_test())


