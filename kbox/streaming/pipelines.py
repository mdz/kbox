"""
The always-on display pipeline that owns the screen.

DisplayPipeline builds it, works out what size to render at, and holds the
overlay elements it linked in so OverlayController can be handed them.
"""

import logging
from typing import Any, Optional

from .elements import (
    create_qr_overlay_element,
    create_text_overlay_element,
    create_videoscale_element,
)
from .gst import _get_gst

logger = logging.getLogger(__name__)

# intervideosink/intervideosrc channel connecting playbin to the display
# pipeline. Any string works as long as both ends agree.
DISPLAY_CHANNEL = "kbox-display"

# Frames are rendered at this height (or the display's, if smaller) and
# the sink scales up from there. Rendering straight at 1080p costs
# ~46 ms/frame, which caps the pipeline near 22fps and cannot sustain
# 30fps video; at 720p it is ~25 ms/frame. Sources at or below this
# height are not degraded, since the sink's upscale is free.
MAX_RENDER_HEIGHT = 720


def _render_caps_string(width: int, height: int) -> str:
    """Caps string for the fixed render frame fed to the display sink.

    Pulled out so test/test_pipeline_benchmark.py can build the exact same
    caps the real display pipeline uses instead of a hand-copied string that
    can drift from it.
    """
    return f"video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1"


def choose_render_size(display_size):
    """
    Pick the fixed frame size to render at, or None if unknown.

    Keeps the display's aspect ratio so the sink, which scales to fit
    while preserving aspect, ends up covering the whole screen -- that is
    what stops the console showing through the margins (issue #93).

    The size is fixed for the session on purpose. kmssink allocates DRM
    framebuffers for one frame size and fails to reallocate them if the
    caps change underneath it ("failed to activate bufferpool", followed
    by an internal data stream error and a dead pipeline), so every source
    has to be fitted into the same frame rather than the frame following
    the source.
    """
    if not display_size:
        return None

    display_width, display_height = display_size
    if display_height <= MAX_RENDER_HEIGHT:
        return (display_width, display_height)

    height = MAX_RENDER_HEIGHT
    width = int(round(display_width * height / display_height))
    # Even dimensions keep chroma-subsampled formats happy.
    return (width - (width % 2), height - (height % 2))


def query_display_size(sink):
    """
    Return the sink's (width, height), or None if it can't report one.

    kmssink exposes display-width/display-height once it has opened the
    DRM device. Other sinks (autovideosink on macOS, fakesink in tests)
    have no such properties, in which case output stays unconstrained.

    Three paths return None and only the first is expected, so they are not
    logged alike. A sink with no such property is the ordinary macOS and
    test case. A sink that advertises the property but cannot answer it, or
    answers with a falsy size, is a kmssink that opened the DRM device
    without settling on a usable mode -- which is worth a warning, because
    an unknown display size is not a cosmetic degrade on that hardware:

      - choose_render_size() has nothing to work with, so the capsfilter is
        never pinned and the frame size stops being fixed for the session.
        That is the condition its docstring describes, where changing caps
        leave kmssink unable to reallocate its DRM framebuffers ("failed to
        activate bufferpool") and the display pipeline dies, typically at
        the first song whose resolution differs from the last.
      - Nothing pads the frame to the display's aspect ratio, so the kernel
        console shows through the bars again (issue #93).
      - Rendering is no longer capped at MAX_RENDER_HEIGHT, so a 1080p
        source costs ~46 ms/frame and cannot sustain 30fps.
      - StreamingController skips overlay sizing, leaving the QR code at
        QR_INITIAL_SIZE on a full-size screen.
    """
    try:
        if sink.find_property("display-width") is None:
            return None
        width = sink.get_property("display-width")
        height = sink.get_property("display-height")
        if width and height:
            return (int(width), int(height))
        logger.warning(
            "Sink advertises a display mode but reported %r x %r; "
            "render size will be left unconstrained",
            width,
            height,
        )
    except Exception:
        # Deliberately broad. This is a query, not a state change, and
        # failing to answer it must never stop the display pipeline coming
        # up -- an unconstrained screen is bad, no screen is worse. The
        # level, not the breadth, is what makes it diagnosable.
        logger.warning(
            "Failed to query the display mode from a sink that advertises one; "
            "render size will be left unconstrained",
            exc_info=True,
        )
    return None


class DisplayPipeline:
    """
    The always-on display pipeline that owns the screen.

    This is a separate GstPipeline from playbin, started once and left in
    PLAYING for the whole life of the process.

    It has to be separate. playbin owns whatever element is set as its
    "video-sink", so every song change drags that sink down with it --
    and GstBaseSink.stop() runs on the PAUSED->READY transition, which
    for kmssink means closing the DRM file descriptor. Closing it drops
    DRM master and hands the screen back to the kernel console, which is
    what made the getty login prompt flash between songs (issue #94).
    Dropping to READY instead of NULL does not help: both pass through
    PAUSED->READY. The sink's lifetime, not its state value, is the bug.

    Keeping the sink in its own pipeline means the display is claimed
    once at startup and never released while kbox runs. playbin is then
    free to cycle NULL/READY/PLAYING per song without touching it.

    Frames cross from playbin over an intervideosink/intervideosrc
    channel. intervideosink keeps sync=true, so playbin still does A/V
    sync before handing buffers over and lyric timing is preserved.
    Something must keep feeding this bridge for the screen to stay up:
    intervideosrc serves a received buffer only a couple of times and then
    generates black, and its "timeout" property does not change that (it
    governs waiting for a first buffer, not holding one). So playbin feeds
    it during songs and a separate imagefreeze pipeline feeds it during
    interstitials -- see StillImageFeeder.create_pipeline.

    kmssink already scales its plane to fill the screen in hardware, so
    nothing here upscales in software. Verified on a Pi 5 (vc4-drm):
    given a 640x360 frame on a 1920x1080 screen it issues
    "drmModeSetPlane at (0,0) 1920x1080 sourcing at (0,0) 640x360".

    What it will not do is cover the screen when the frame's aspect ratio
    differs from the display's: it scales to fit while preserving aspect,
    so a 4:3 source lands as 1440x1080 centred and the kernel console
    shows through the 240px bars either side (issue #93). videobox fixes
    that by padding the frame out to the display's aspect ratio at source
    resolution -- cheap, since it only adds borders -- after which
    kmssink's own scaling covers the whole screen.

    Doing the upscale in software instead cost ~46 ms/frame, which caps
    the pipeline at ~22fps and cannot sustain 30fps video. Padding is
    0.4-1.3 ms/frame. See test/test_pipeline_benchmark.py.

    Overlays are composited after the padding, so they sit in the padded
    frame's coordinate space: the top-left corner is the screen's corner
    even when the video itself is inset between bars. Sizing them as a
    proportion of the padded frame keeps them a constant size on screen,
    because everything downstream is scaled by the same factor.

    Construction takes it as far as READY with the render caps pinned, so
    the caller can size the overlays before start() takes it to PLAYING --
    the order the overlays were sized in when all of this was one method.
    """

    def __init__(self, config_manager, use_fakesinks: bool = False):
        """
        Args:
            config_manager: Configuration manager instance
            use_fakesinks: If True, use fakesinks for headless testing
        """
        self.config_manager = config_manager
        self.use_fakesinks = use_fakesinks
        self.logger = logging.getLogger(__name__)

        self.pipeline: Any = None
        self.display_size: Optional[tuple] = None  # (width, height) if known
        self.render_size: Optional[tuple] = None  # fixed frame size fed to the sink

        # Overlay elements, linked into the pipeline below. Handed to
        # OverlayController, which drives them.
        self.qr_overlay = None
        self.text_overlay = None

        self._build()

    def _build(self):
        """Assemble the pipeline and take it to READY with the caps pinned."""
        Gst = _get_gst()

        pipeline = Gst.Pipeline.new("display_pipeline")
        elements = []

        # 1. intervideosrc - receives frames from playbin's intervideosink
        src = Gst.ElementFactory.make("intervideosrc", "intervideosrc")
        if src is None:
            raise RuntimeError("Failed to create intervideosrc element")
        src.set_property("channel", DISPLAY_CHANNEL)
        elements.append(src)

        # 2. videoconvert (required)
        vc = Gst.ElementFactory.make("videoconvert", "videoconvert")
        if vc is None:
            raise RuntimeError("Failed to create videoconvert element")
        elements.append(vc)

        # 3. videoscale with borders - fits any source into the fixed frame
        # below, letterboxing rather than distorting.
        vs = create_videoscale_element()
        elements.append(vs)

        # 4. capsfilter - pins the frame handed to the sink to one fixed size
        # for the whole session. Caps are set once the display is known.
        capsfilter = Gst.ElementFactory.make("capsfilter", "display_caps")
        if capsfilter is None:
            raise RuntimeError("Failed to create capsfilter element")
        elements.append(capsfilter)

        # Overlays come AFTER the capsfilter on purpose, so they are
        # composited into that fixed frame: its top-left is the screen's
        # top-left even when the video is inset between bars, and a
        # proportion of it is a constant size on screen because the sink
        # scales the whole frame by one factor.

        # 5. QR code overlay (optional - graceful fallback if unavailable)
        self.qr_overlay = create_qr_overlay_element(self.config_manager)
        if self.qr_overlay:
            elements.append(self.qr_overlay)

        # 6. Text overlay for notifications (optional - graceful fallback)
        self.text_overlay = create_text_overlay_element()
        if self.text_overlay:
            elements.append(self.text_overlay)

        # 7. Platform-appropriate video sink
        from ..platform import create_video_sink

        sink = create_video_sink(use_fakesinks=self.use_fakesinks)
        elements.append(sink)

        for elem in elements:
            pipeline.add(elem)

        for i in range(len(elements) - 1):
            if not elements[i].link(elements[i + 1]):
                raise RuntimeError(
                    f"Failed to link {elements[i].get_name()} to {elements[i + 1].get_name()}"
                )

        self.pipeline = pipeline

        # This pipeline's bus is polled alongside playbin's by
        # StreamingController._start_bus_polling(), which the caller starts
        # once this pipeline exists -- dispatch_bus_message below is what it
        # routes through. Without that a failure here is completely silent:
        # the app logs a clean startup and the screen just shows whatever the
        # console was showing.

        # READY opens the DRM device, which is what makes kmssink able to
        # report the connector's mode.
        ret = pipeline.set_state(Gst.State.READY)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Display pipeline failed to reach READY state")
        pipeline.get_state(5 * Gst.SECOND)

        self.display_size = query_display_size(sink)
        self.render_size = choose_render_size(self.display_size)

        if self.render_size:
            width, height = self.render_size
            capsfilter.set_property(
                "caps", Gst.Caps.from_string(_render_caps_string(width, height))
            )
            self.logger.info(
                "Rendering at %dx%d; the sink scales that to the %dx%d display",
                width,
                height,
                self.display_size[0],
                self.display_size[1],
            )
        else:
            # Sinks that cannot report a mode (autovideosink on macOS,
            # fakesink in tests). Leave the caps open and let the sink cope.
            self.logger.info("Display size unknown; leaving output caps unconstrained")

    def start(self):
        """Take the pipeline to PLAYING, claiming the screen for the session."""
        Gst = _get_gst()

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Display pipeline failed to reach PLAYING state")

        self.logger.info(
            "Display pipeline started and holding the screen (qr=%s, text=%s)",
            self.qr_overlay is not None,
            self.text_overlay is not None,
        )

    def stop(self):
        """Release the screen. Logs rather than raises -- this runs on shutdown."""
        try:
            Gst = _get_gst()
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        except Exception as e:
            self.logger.error("Error stopping display pipeline: %s", e, exc_info=True)

    def release_for_rebuild(self):
        """
        Drop to NULL and wait for it, so a replacement can claim the screen.

        It holds the DRM device, and the rebuilt pipeline cannot claim the
        screen until this one lets go. This does briefly show the console, but
        only on an explicit config change, not on the per-song path that #94
        is about.
        """
        Gst = _get_gst()
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline.get_state(5 * Gst.SECOND)
        self.pipeline = None
        self.display_size = None

    def bus(self):
        """This pipeline's bus, or None once it has been torn down."""
        return self.pipeline.get_bus() if self.pipeline else None

    def dispatch_bus_message(self, bus, msg):
        """Route one message from this pipeline's bus to its handler."""
        Gst = _get_gst()
        if msg.type == Gst.MessageType.ERROR:
            self._on_error(bus, msg)
        elif msg.type == Gst.MessageType.WARNING:
            self._on_warning(bus, msg)

    def _on_error(self, bus, message):
        """Log errors from the display pipeline, which are otherwise silent."""
        err, debug = message.parse_error()
        self.logger.error("Display pipeline error: %s (%s)", err, debug)

    def _on_warning(self, bus, message):
        """Log warnings from the display pipeline."""
        err, debug = message.parse_warning()
        self.logger.warning("Display pipeline warning: %s (%s)", err, debug)
