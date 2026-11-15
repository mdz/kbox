"""
Lazy GStreamer/GLib imports and small helpers that only need them.

Kept as module-level functions with module-level caches so the imports happen
once per process and nothing has to own them.
"""

import logging
from typing import Optional

# Defer GStreamer imports until actually needed to avoid crashes on import
# On macOS, importing GStreamer can cause segfaults due to library conflicts
_Gst = None


_gst_available: Optional[bool] = None

logger = logging.getLogger(__name__)


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


def caps_dimensions(caps):
    """
    Best-effort (width, height) from a caps object, or (None, None).

    Tries several access styles because the structure API varies between
    PyGObject versions, falling back to parsing the caps string.
    """
    struct = caps.get_structure(0) if caps else None
    if struct is None:
        return (None, None)

    # Dictionary-style access first (most compatible)
    if hasattr(struct, "__getitem__"):
        try:
            return (struct["width"], struct["height"])
        except (KeyError, TypeError):
            pass

    # Some versions expose get_value instead
    if hasattr(struct, "get_value"):
        try:
            return (struct.get_value("width"), struct.get_value("height"))
        except Exception:
            pass

    # Last resort: parse the serialised caps
    import re

    caps_str = caps.to_string()
    width_match = re.search(r"width=\(int\)(\d+)", caps_str)
    height_match = re.search(r"height=\(int\)(\d+)", caps_str)
    if width_match and height_match:
        return (int(width_match.group(1)), int(height_match.group(1)))

    return (None, None)
