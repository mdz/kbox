"""
GStreamer-based streaming controller for audio/video playback.

Uses a persistent playbin pipeline with custom sink bins for pitch shifting.
The pipeline is created at initialization and stays alive, switching between
READY (idle) and PLAYING (song) states.
"""

from .controller import StreamingController
from .gst import _get_gst
from .pipelines import _render_caps_string

__all__ = ["StreamingController", "_get_gst", "_render_caps_string"]
