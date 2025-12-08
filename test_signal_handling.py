#!/usr/bin/env python3
"""
Minimal test program to test signal handling with gst_macos_main + uvicorn in thread.
"""

import sys
import signal
import threading
import logging
import ctypes
import os
import subprocess
import time
from typing import Callable, Optional, Tuple

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


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
    """Run the main function using gst_macos_main() on macOS."""
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
            return main_func()

        return gst_macos_main(GstMainFunc(wrapper), argc, argv, None)
    except Exception as e:
        logger.warning("Could not use gst_macos_main: %s, using regular main", e)
        return main_func()


def run_uvicorn_in_thread(uvicorn_server, shutdown_event) -> Tuple[threading.Thread, Callable]:
    """Run uvicorn server in a background thread."""
    def run_server():
        try:
            uvicorn_server.run()
        except Exception as e:
            if not shutdown_event.is_set():
                logger.error("Error in uvicorn server: %s", e, exc_info=True)

    server_thread = threading.Thread(target=run_server, daemon=False, name="UvicornServer")
    server_thread.start()

    def wait_for_shutdown():
        """Wait for shutdown event or thread completion."""
        try:
            # Poll for shutdown event while waiting for thread
            while not shutdown_event.is_set() and server_thread.is_alive():
                shutdown_event.wait(timeout=0.1)
                if shutdown_event.is_set():
                    logger.info("Shutdown event set, stopping uvicorn...")
                    uvicorn_server.should_exit = True
                    server_thread.join(timeout=2.0)
                    return
            # Thread finished on its own
            server_thread.join()
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt in wait_for_shutdown")
            shutdown_event.set()
            uvicorn_server.should_exit = True
            server_thread.join(timeout=2.0)
            raise

    return server_thread, wait_for_shutdown


def actual_main():
    """Actual main function."""
    from fastapi import FastAPI
    import uvicorn
    import threading

    app = FastAPI()

    @app.get("/")
    def root():
        return {"message": "Test server running"}

    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    shutdown_event = threading.Event()

    # Set up signal handler BEFORE starting server
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        shutdown_event.set()
        server.should_exit = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Starting test server...")
    logger.info("Press Ctrl+C to stop")

    if sys.platform == "darwin":
        # Run uvicorn in thread (like the real app)
        server_thread, wait_func = run_uvicorn_in_thread(server, shutdown_event)
        try:
            wait_func()
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt caught, shutting down...")
            shutdown_event.set()
            server.should_exit = True
            server_thread.join(timeout=2.0)
    else:
        # Run uvicorn normally
        try:
            server.run()
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt caught, shutting down...")

    logger.info("Test server stopped")
    return 0


def main():
    """Main entry point."""
    # Test without gst_macos_main first
    if len(sys.argv) > 1 and sys.argv[1] == "--no-gst":
        logger.info("Running WITHOUT gst_macos_main (direct test)")
        return actual_main()
    else:
        logger.info("Running WITH gst_macos_main (real scenario)")
        return run_with_gst_macos_main(actual_main)


if __name__ == "__main__":
    sys.exit(main())


