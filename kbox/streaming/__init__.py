"""
GStreamer-based streaming controller for audio/video playback.

Uses a persistent playbin pipeline with custom sink bins for pitch shifting.
The pipeline is created at initialization and stays alive, switching between
READY (idle) and PLAYING (song) states.

The package splits that into pieces that each own their own state:

- gst: lazy GStreamer/GLib imports and caps helpers
- elements: pure factory functions for individual elements and sink bins
- pipelines: DisplayPipeline, the always-on pipeline that owns the screen
- overlays: OverlayController, the QR and text overlays
- still_image: StillImageFeeder, the interstitial image feed
- bus: BusWatcher, the bus polling thread
- controller: StreamingController, the facade that wires them together
"""

from .controller import StreamingController
from .gst import _get_gst
from .pipelines import _render_caps_string

__all__ = ["StreamingController", "_get_gst", "_render_caps_string"]
