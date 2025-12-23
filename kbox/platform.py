"""
Platform-specific code for macOS and Linux.

This module isolates platform-specific functionality to keep the main codebase
platform-agnostic. Includes GStreamer sink creation for different platforms.
"""

import ctypes
import logging
import os
import subprocess
import sys
import threading
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def list_audio_output_devices() -> List[dict]:
    """
    List available audio output devices using GStreamer DeviceMonitor.

    Returns:
        List of device dictionaries with 'value' (device identifier) and 'label' (display name).
        Always includes a "System Default" option, even if GStreamer is unavailable.
    """
    devices = []
    gstreamer_available = False

    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        # Ensure GStreamer is initialized
        if not Gst.is_initialized():
            Gst.init(None)
        gstreamer_available = True
    except (ImportError, ValueError) as e:
        logger.warning("GStreamer not available for device enumeration: %s", e)

    if gstreamer_available:
        monitor = None
        try:
            monitor = Gst.DeviceMonitor.new()
            # Filter for audio sinks (output devices)
            monitor.add_filter("Audio/Sink", None)
            monitor.start()

            for device in monitor.get_devices():
                display_name = device.get_display_name()
                props = device.get_properties()

                # Get the device identifier - try different property names
                device_id = None
                if props:
                    # On Linux/ALSA, look for the ALSA device string
                    if sys.platform == "linux":
                        # Check if this is an ALSA device
                        device_api = props.get_string("device.api")
                        if device_api == "alsa":
                            # Get the actual ALSA device path (e.g., "plughw:CARD=USB,DEV=0")
                            card = props.get_string("alsa.card")
                            device_num = props.get_string("alsa.device") or "0"
                            card_name = props.get_string("alsa.card_name")
                            if card is not None:
                                device_id = f"plughw:CARD={card},DEV={device_num}"
                            elif card_name:
                                # Some devices only expose the card name
                                device_id = f"plughw:CARD={card_name},DEV={device_num}"
                        # For non-ALSA devices (e.g., PulseAudio), device_id stays None
                        # and falls through to the generic fallbacks below

                    # On macOS, devices are typically auto-selected
                    if device_id is None:
                        device_id = props.get_string("device.name")
                    if device_id is None:
                        device_id = props.get_string("object.id")

                # Skip devices we can't identify
                if not device_id:
                    logger.debug("Skipping device without identifier: %s", display_name)
                    continue

                devices.append({"value": device_id, "label": display_name})

        except Exception as e:
            logger.warning("Error enumerating audio devices: %s", e)
        finally:
            if monitor is not None:
                monitor.stop()

    # Always include a "System Default" option
    if sys.platform == "darwin":
        # On macOS, empty/None means use system default via autoaudiosink
        devices.insert(0, {"value": "", "label": "System Default"})
    else:
        # On Linux, we can suggest common defaults
        if not any(d["value"] == "default" for d in devices):
            devices.insert(0, {"value": "", "label": "System Default"})

    return devices


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
        logger.warning("Could not find libgstreamer-1.0.dylib, falling back to regular main")
        return main_func()

    try:
        # Load GStreamer library
        gst_lib = ctypes.CDLL(gst_lib_path)

        # Define GstMainFunc type
        GstMainFunc = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p))

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
        logger.warning("Could not use gst_macos_main: %s, falling back to regular main", e)
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

    server_thread = threading.Thread(target=run_server, daemon=False, name="UvicornServer")
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
        sink = Gst.ElementFactory.make("fakesink", "video_sink")
        if sink is None:
            raise RuntimeError("Failed to create fakesink for headless testing")
        return sink

    if sys.platform == "linux":
        # Use kmssink for direct kernel mode setting (Raspberry Pi)
        sink = Gst.ElementFactory.make("kmssink", "video_sink")
        if sink is None:
            logger.warning("kmssink not available, falling back to autovideosink")
            sink = Gst.ElementFactory.make("autovideosink", "video_sink")
        if sink is None:
            raise RuntimeError("No video sink available on Linux")
        return sink
    else:  # macOS
        # Try autovideosink first (auto-detects best sink)
        sink = Gst.ElementFactory.make("autovideosink", "video_sink")
        if sink is None:
            # Fallback to osxvideosink
            sink = Gst.ElementFactory.make("osxvideosink", "video_sink")
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
        sink = Gst.ElementFactory.make("fakesink", "audio_sink")
        if sink is None:
            raise RuntimeError("Failed to create fakesink for headless testing")
        return sink

    if sys.platform == "linux":
        # Use alsasink for ALSA (Raspberry Pi)
        sink = Gst.ElementFactory.make("alsasink", "audio_sink")
        if sink is None:
            raise RuntimeError("alsasink not available on Linux")
        if device:
            sink.set_property("device", device)
        return sink
    else:  # macOS
        # Use autoaudiosink (auto-detects best sink)
        sink = Gst.ElementFactory.make("autoaudiosink", "audio_sink")
        if sink is None:
            raise RuntimeError("No audio sink available on macOS")
        return sink
