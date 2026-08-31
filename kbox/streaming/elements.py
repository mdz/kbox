"""
Pure GStreamer element factories.

These are factory functions of config/arguments only -- no dependency on
StreamingController instance state -- so they live at module level rather
than as bound methods.
"""

from . import gst


def create_videoscale_element():
    """Create the videoscale element that fits source video into the
    fixed render frame, letterboxing rather than distorting.

    Pulled out of _create_display_pipeline so
    test/test_pipeline_benchmark.py can benchmark the actual element
    instead of a hand-copied stand-in.

    method=nearest-neighbour instead of the bilinear default. Overlays
    (QR/text) are composited downstream of this element, after the
    capsfilter, so they are unaffected either way -- this only changes
    how the source video itself is scaled. Measured on a Pi 5, 640x360
    source upscaled to a 1280x720 render frame: bilinear costs
    ~25 ms/frame, nearest-neighbour ~12 ms/frame -- roughly half, for
    content that has no detail beyond 360 lines to begin with. See
    docs/development/gstreamer-pipeline.md.
    """
    Gst = gst._get_gst()
    vs = Gst.ElementFactory.make("videoscale", "videoscale")
    if vs is None:
        raise RuntimeError("Failed to create videoscale element")
    vs.set_property("add-borders", True)
    vs.set_property("method", 0)  # nearest-neighbour
    return vs


def create_qr_overlay_element(config_manager, logger):
    """Create gdkpixbufoverlay element for QR code, or None if unavailable.

    Returns a (element, position, size_percent, current_size, current_padding)
    tuple -- the caller is responsible for tracking the config-derived
    position/size and the element's current geometry, since those are read
    later when the video resolution becomes known (see
    OverlayMixin._update_qr_size_for_resolution). All fields are None if the
    plugin is unavailable.
    """
    Gst = gst._get_gst()

    try:
        qr = Gst.ElementFactory.make("gdkpixbufoverlay", "qr_overlay")
        if qr is None:
            logger.warning("gdkpixbufoverlay not available, QR overlay disabled")
            return None, None, None, None, None

        # Store config for later use when positioning
        position = config_manager.get("overlay_qr_position") or "top-left"
        size_percent = config_manager.get_int("overlay_qr_size_percent", 10)
        current_size = 72  # Default size, will be updated by caps probe
        current_padding = 15

        # Set initial size and alpha - will be updated when we know video dimensions
        qr.set_property("overlay-width", current_size)
        qr.set_property("overlay-height", current_size)
        qr.set_property("offset-x", current_padding)
        qr.set_property("offset-y", current_padding)
        qr.set_property("alpha", 0.7)  # Semi-transparent

        logger.info(
            "QR overlay element created (size_percent=%d%%, position=%s)",
            size_percent,
            position,
        )
        return qr, position, size_percent, current_size, current_padding

    except Exception as e:
        logger.warning("Failed to create QR overlay: %s", e)
        return None, None, None, None, None


def create_text_overlay_element(logger):
    """Create textoverlay element for notifications, or None if unavailable."""
    Gst = gst._get_gst()

    try:
        text = Gst.ElementFactory.make("textoverlay", "text_overlay")
        if text is None:
            logger.warning("textoverlay not available, text notifications disabled")
            return None

        # Configure text overlay - subtle, top-right corner
        text.set_property("text", "")  # Start with no text
        text.set_property("valignment", "top")
        text.set_property("halignment", "right")
        text.set_property("xpad", 20)
        text.set_property("ypad", 20)
        # Sized against textoverlay's 640-wide auto-resize basis, not the
        # actual screen: auto-resize scales this up for us. See
        # _update_text_layout_for_resolution.
        text.set_property("font-desc", "Sans 9")
        text.set_property("shaded-background", True)
        text.set_property("silent", True)  # No text initially

        logger.info("Text overlay element created")
        return text

    except Exception as e:
        logger.warning("Failed to create text overlay: %s", e)
        return None
