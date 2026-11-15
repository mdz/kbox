"""
Factory functions for the individual GStreamer elements and sink bins.

These are pure functions of their arguments: nothing here reads or writes
state belonging to anyone else, so each one can be called -- and benchmarked,
and tested -- on its own without a StreamingController in sight.
"""

import logging
from typing import Any, Callable, NamedTuple, Optional

from .gst import _get_gst

logger = logging.getLogger(__name__)

# Starting geometry for the QR overlay, used until the render size is known
# and OverlayController resizes it. Shared with OverlayController so the
# element's initial properties and the tracked values cannot drift apart.
QR_INITIAL_SIZE = 72
QR_INITIAL_PADDING = 15


class AudioSinkBin(NamedTuple):
    """The audio sink bin plus the elements inside it callers need to poke."""

    bin: Any
    pitch_shift_element: Any
    volume_element: Any


def create_videoscale_element():
    """Create the videoscale element that fits source video into the
    fixed render frame, letterboxing rather than distorting.

    Pulled out of DisplayPipeline so test/test_pipeline_benchmark.py can
    benchmark the actual element instead of a hand-copied stand-in.

    method=nearest-neighbour instead of the bilinear default. Overlays
    (QR/text) are composited downstream of this element, after the
    capsfilter, so they are unaffected either way -- this only changes
    how the source video itself is scaled. Measured on a Pi 5, 640x360
    source upscaled to a 1280x720 render frame: bilinear costs
    ~25 ms/frame, nearest-neighbour ~12 ms/frame -- roughly half, for
    content that has no detail beyond 360 lines to begin with. See
    docs/development/gstreamer-pipeline.md.
    """
    Gst = _get_gst()
    vs = Gst.ElementFactory.make("videoscale", "videoscale")
    if vs is None:
        raise RuntimeError("Failed to create videoscale element")
    vs.set_property("add-borders", True)
    vs.set_property("method", 0)  # nearest-neighbour
    return vs


def create_qr_overlay_element(config_manager):
    """Create gdkpixbufoverlay element for QR code, or None if unavailable."""
    Gst = _get_gst()

    try:
        qr = Gst.ElementFactory.make("gdkpixbufoverlay", "qr_overlay")
        if qr is None:
            logger.warning("gdkpixbufoverlay not available, QR overlay disabled")
            return None

        # Set initial size and alpha - will be updated when we know video dimensions
        qr.set_property("overlay-width", QR_INITIAL_SIZE)
        qr.set_property("overlay-height", QR_INITIAL_SIZE)
        qr.set_property("offset-x", QR_INITIAL_PADDING)
        qr.set_property("offset-y", QR_INITIAL_PADDING)
        qr.set_property("alpha", 0.7)  # Semi-transparent

        logger.info(
            "QR overlay element created (size_percent=%d%%, position=%s)",
            config_manager.get_int("overlay_qr_size_percent", 10),
            config_manager.get("overlay_qr_position") or "top-left",
        )
        return qr

    except Exception as e:
        logger.warning("Failed to create QR overlay: %s", e)
        return None


def create_text_overlay_element():
    """Create textoverlay element for notifications, or None if unavailable."""
    Gst = _get_gst()

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
        # OverlayController.update_text_layout_for_resolution.
        text.set_property("font-desc", "Sans 9")
        text.set_property("shaded-background", True)
        text.set_property("silent", True)  # No text initially

        logger.info("Text overlay element created")
        return text

    except Exception as e:
        logger.warning("Failed to create text overlay: %s", e)
        return None


def create_signalsmith_pitch_shift(config_manager, semitones):
    """Try to create the native signalsmithpitch element, registering its
    plugin .so from disk first if it isn't already known to GStreamer.

    Returns the element, or None if unavailable (caller falls back to
    identity).
    """
    import os

    Gst = _get_gst()

    if Gst.ElementFactory.find("signalsmithpitch") is None:
        # Three dirnames up from kbox/streaming/elements.py is the project
        # root, where native/ lives.
        plugin_path = config_manager.get("signalsmith_pitch_plugin_path") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "native",
            "gst-signalsmith-pitch",
            "build",
            "libgstsignalsmithpitch.so",
        )
        if not os.path.exists(plugin_path):
            return None
        try:
            if Gst.Plugin.load_file(plugin_path) is None:
                return None
        except Exception as e:
            logger.warning("Failed to load signalsmithpitch plugin: %s", e)
            return None

    elem = Gst.ElementFactory.make("signalsmithpitch", "pitch_shift")
    if elem is None:
        return None

    elem.set_property("semitones", semitones)
    logger.info("Using native signalsmithpitch element for pitch shift")
    return elem


def create_pitch_shift_or_identity(config_manager, semitones):
    """Create the native signalsmithpitch element, or identity passthrough
    if its plugin isn't available.
    """
    Gst = _get_gst()

    signalsmith_elem = create_signalsmith_pitch_shift(config_manager, semitones)
    if signalsmith_elem is not None:
        return signalsmith_elem

    logger.warning("No pitch shift element available, falling back to identity")
    return Gst.ElementFactory.make("identity", "pitch_shift")


def create_audio_sink_bin(
    config_manager,
    use_fakesinks: bool,
    pitch_shift_semitones,
    volume_gain_linear: float,
) -> AudioSinkBin:
    """Create audio sink bin with pitch shift element and channel upmixing."""
    Gst = _get_gst()
    audio_bin = Gst.Bin.new("audio_sink_bin")

    # Create elements: audioconvert -> pitch_shift -> audioconvert -> [capsfilter] -> sink
    ac1 = Gst.ElementFactory.make("audioconvert", "ac1")
    if ac1 is None:
        raise RuntimeError("Failed to create audioconvert element")

    # Create pitch shift element or identity passthrough
    pitch_shift_element = create_pitch_shift_or_identity(config_manager, pitch_shift_semitones)

    ac2 = Gst.ElementFactory.make("audioconvert", "ac2")
    if ac2 is None:
        raise RuntimeError("Failed to create audioconvert element")

    # Volume element for per-song loudness normalization
    volume_element = Gst.ElementFactory.make("volume", "volume")
    if volume_element is None:
        raise RuntimeError("Failed to create volume element")
    volume_element.set_property("volume", volume_gain_linear)

    # Build element chain
    elements = [ac1, pitch_shift_element, ac2, volume_element]

    # Add capsfilter for channel upmixing if configured for more than 2 channels
    num_channels = config_manager.get_int("audio_output_channels", 2)
    if num_channels > 2:
        logger.info("Adding %d-channel upmix capsfilter", num_channels)
        capsfilter = Gst.ElementFactory.make("capsfilter", "channel_caps")
        if capsfilter is not None:
            caps = Gst.Caps.from_string(f"audio/x-raw,channels={num_channels}")
            capsfilter.set_property("caps", caps)
            elements.append(capsfilter)
        else:
            logger.warning("Could not create capsfilter for channel upmix")

    # Create platform-appropriate audio sink
    from ..platform import create_audio_sink

    audio_output_device = config_manager.get("audio_output_device")
    sink = create_audio_sink(use_fakesinks=use_fakesinks, device=audio_output_device)
    elements.append(sink)

    # Add all elements to bin
    for elem in elements:
        audio_bin.add(elem)

    # Link elements in order
    for i in range(len(elements) - 1):
        if not elements[i].link(elements[i + 1]):
            raise RuntimeError(
                f"Failed to link {elements[i].get_name()} to {elements[i + 1].get_name()}"
            )

    # Create ghost pad pointing to first element's sink pad
    sink_pad = ac1.get_static_pad("sink")
    ghost_pad = Gst.GhostPad.new("sink", sink_pad)
    audio_bin.add_pad(ghost_pad)

    logger.info("Audio sink bin created with pitch shift")
    return AudioSinkBin(audio_bin, pitch_shift_element, volume_element)


def create_video_sink_bin(
    channel: str,
    on_source_caps_event: Optional[Callable] = None,
    on_video_buffer: Optional[Callable] = None,
):
    """
    Create the bin playbin renders video into.

    This is only a bridge to the display pipeline (see DisplayPipeline).
    Everything that actually touches the screen lives over there, so that
    playbin's per-song state changes never reach the real video sink.
    """
    Gst = _get_gst()
    video_bin = Gst.Bin.new("video_sink_bin")

    vc = Gst.ElementFactory.make("videoconvert", "playbin_videoconvert")
    if vc is None:
        raise RuntimeError("Failed to create videoconvert element")

    inter = Gst.ElementFactory.make("intervideosink", "intervideosink")
    if inter is None:
        raise RuntimeError("Failed to create intervideosink element")
    inter.set_property("channel", channel)
    # sync=true (the default) keeps playbin responsible for A/V sync:
    # buffers are released on the clock, so lyrics stay aligned to audio.
    inter.set_property("sync", True)

    video_bin.add(vc)
    video_bin.add(inter)
    if not vc.link(inter):
        raise RuntimeError("Failed to link videoconvert to intervideosink")

    sink_pad = vc.get_static_pad("sink")
    ghost_pad = Gst.GhostPad.new("sink", sink_pad)
    video_bin.add_pad(ghost_pad)

    # Log what playbin is actually decoding. This is the only place the
    # source resolution is visible: everything downstream has been
    # rescaled to the display.
    if on_source_caps_event is not None:
        vc.get_static_pad("src").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, on_source_caps_event)

    # Sees every frame about to cross the bridge, so the controller can keep
    # the latest one on screen while paused (StillImageFeeder.start_frame).
    if on_video_buffer is not None:
        vc.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, on_video_buffer)

    logger.info("Video sink bin created (bridging to display pipeline)")
    return video_bin
