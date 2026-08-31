"""
Lazy GStreamer/GLib imports.

GStreamer imports are deferred until actually needed to avoid crashes on
import -- on macOS, importing GStreamer can cause segfaults due to library
conflicts.
"""

import logging
from typing import Optional

_Gst = None


_gst_available: Optional[bool] = None


def _get_gst():
    """Lazily import GStreamer.  Returns None if unavailable."""
    global _Gst, _gst_available
    if _gst_available is True:
        return _Gst
    if _gst_available is False:
        return None

    try:
        import gi

        gi.require_version("GLib", "2.0")
        gi.require_version("GObject", "2.0")
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst as _Gst_module

        _Gst = _Gst_module
        _gst_available = True
        return _Gst
    except Exception as e:
        logging.getLogger(__name__).error("Failed to import GStreamer: %s", e)
        _gst_available = False
        return None


_GLib = None
_glib_available: Optional[bool] = None


def _get_glib():
    """Lazily import GLib.  Returns None if unavailable."""
    global _GLib, _glib_available
    if _glib_available is True:
        return _GLib
    if _glib_available is False:
        return None

    try:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib as _GLib_module

        _GLib = _GLib_module
        _glib_available = True
        return _GLib
    except Exception as e:
        logging.getLogger(__name__).error("Failed to import GLib: %s", e)
        _glib_available = False
        return None


def _escape_overlay_text(text: str) -> str:
    """Escape text for safe use in the textoverlay element's Pango markup."""
    GLib = _get_glib()
    if GLib is None:
        return text
    return GLib.markup_escape_text(text)
