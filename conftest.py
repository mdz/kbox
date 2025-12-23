"""
Pytest configuration for kbox tests.

Provides:
- @pytest.mark.gstreamer marker for tests requiring GStreamer
- Auto-skip of GStreamer tests when gi module is unavailable
"""

import pytest


def _is_gstreamer_available():
    """Check if GStreamer Python bindings are available."""
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        return True
    except (ImportError, ValueError):
        return False


GSTREAMER_AVAILABLE = _is_gstreamer_available()


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "gstreamer: marks tests as requiring GStreamer (skipped if unavailable)"
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip GStreamer tests when GStreamer is unavailable."""
    if GSTREAMER_AVAILABLE:
        return

    skip_gstreamer = pytest.mark.skip(reason="GStreamer not available (gi module not found)")
    for item in items:
        if "gstreamer" in item.keywords:
            item.add_marker(skip_gstreamer)
