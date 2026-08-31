"""
Playbin sink bins, the always-on display pipeline, and render-size sizing.

See docs/development/gstreamer-pipeline.md for why the display pipeline is
separate from playbin and why the render size is fixed and capped.
"""

import logging
import threading
from typing import TYPE_CHECKING, Any

from . import elements, gst


def _render_caps_string(width: int, height: int) -> str:
    """Caps string for the fixed render frame fed to the display sink.

    Pulled out so test/test_pipeline_benchmark.py can build the exact same
    caps the real display pipeline uses instead of a hand-copied string that
    can drift from it.
    """
    return f"video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1"


class PipelineMixin:
    """StreamingController methods for building the playbin sink bins and
    the persistent display pipeline."""

    if TYPE_CHECKING:
        # Provided by StreamingController.__init__ and the other mixins it
        # combines with; declared here only so mypy can typecheck this
        # mixin's methods on their own.
        logger: logging.Logger
        config_manager: Any
        use_fakesinks: bool
        pitch_shift_semitones: int
        volume_gain_linear: float
        DISPLAY_CHANNEL: str

        def _ensure_gst_initialized(self) -> None: ...
        def _on_eos(self, bus, message) -> None: ...
        def _on_error(self, bus, message) -> None: ...
        def _on_warning(self, bus, message) -> None: ...
        def _start_bus_polling(self) -> None: ...
        def _create_qr_overlay_element(self): ...
        def _create_text_overlay_element(self): ...
        def _update_qr_size_for_resolution(self, width, height) -> None: ...
        def _update_text_layout_for_resolution(self, width) -> None: ...
        def _on_source_caps_event(self, pad, info): ...

    def _create_persistent_pipeline(self):
        """Create the persistent playbin pipeline with custom sink bins."""
        self._ensure_gst_initialized()

        Gst = gst._get_gst()

        # Bring up the display pipeline FIRST. It claims the screen once and
        # holds it for the entire session, independently of playbin.
        self._create_display_pipeline()

        self.playbin = Gst.ElementFactory.make("playbin", "playbin")
        if self.playbin is None:
            raise RuntimeError("Failed to create playbin element")

        # Create and attach custom audio sink bin (with pitch shift)
        self.audio_bin = self._create_audio_sink_bin()
        self.playbin.set_property("audio-sink", self.audio_bin)

        # Create and attach custom video sink bin
        self.video_bin = self._create_video_sink_bin()
        self.playbin.set_property("video-sink", self.video_bin)

        # Connect bus handlers for EOS, errors, and warnings
        bus = self.playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)

        # Start bus polling thread for EOS/error handling
        # (signal watch requires GLib main loop which may not be running)
        self._start_bus_polling()

        # Start in READY state (idle, no output)
        ret = self.playbin.set_state(Gst.State.READY)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to set pipeline to READY state")

        self.logger.info("Persistent pipeline created successfully")

    def _create_audio_sink_bin(self):
        """Create audio sink bin with pitch shift element and channel upmixing."""
        Gst = gst._get_gst()
        audio_bin = Gst.Bin.new("audio_sink_bin")

        # Create elements: audioconvert -> pitch_shift -> audioconvert -> [capsfilter] -> sink
        ac1 = Gst.ElementFactory.make("audioconvert", "ac1")
        if ac1 is None:
            raise RuntimeError("Failed to create audioconvert element")

        # Create pitch shift element or identity passthrough
        self.pitch_shift_element = self._create_pitch_shift_or_identity()

        ac2 = Gst.ElementFactory.make("audioconvert", "ac2")
        if ac2 is None:
            raise RuntimeError("Failed to create audioconvert element")

        # Volume element for per-song loudness normalization
        self.volume_element = Gst.ElementFactory.make("volume", "volume")
        if self.volume_element is None:
            raise RuntimeError("Failed to create volume element")
        self.volume_element.set_property("volume", self.volume_gain_linear)

        # Build element chain
        elements_ = [ac1, self.pitch_shift_element, ac2, self.volume_element]

        # Add capsfilter for channel upmixing if configured for more than 2 channels
        num_channels = self.config_manager.get_int("audio_output_channels", 2)
        if num_channels > 2:
            self.logger.info("Adding %d-channel upmix capsfilter", num_channels)
            capsfilter = Gst.ElementFactory.make("capsfilter", "channel_caps")
            if capsfilter is not None:
                caps = Gst.Caps.from_string(f"audio/x-raw,channels={num_channels}")
                capsfilter.set_property("caps", caps)
                elements_.append(capsfilter)
            else:
                self.logger.warning("Could not create capsfilter for channel upmix")

        # Create platform-appropriate audio sink
        from ..platform import create_audio_sink

        audio_output_device = self.config_manager.get("audio_output_device")
        sink = create_audio_sink(use_fakesinks=self.use_fakesinks, device=audio_output_device)
        elements_.append(sink)

        # Add all elements to bin
        for elem in elements_:
            audio_bin.add(elem)

        # Link elements in order
        for i in range(len(elements_) - 1):
            if not elements_[i].link(elements_[i + 1]):
                raise RuntimeError(
                    f"Failed to link {elements_[i].get_name()} to {elements_[i + 1].get_name()}"
                )

        # Create ghost pad pointing to first element's sink pad
        sink_pad = ac1.get_static_pad("sink")
        ghost_pad = Gst.GhostPad.new("sink", sink_pad)
        audio_bin.add_pad(ghost_pad)

        self.logger.info("Audio sink bin created with pitch shift")
        return audio_bin

    def _create_display_pipeline(self):
        """
        Create the always-on display pipeline that owns the screen.

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
        interstitials -- see _create_interstitial_pipeline.

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
        """
        Gst = gst._get_gst()

        pipeline = Gst.Pipeline.new("display_pipeline")
        pipeline_elements = []

        # 1. intervideosrc - receives frames from playbin's intervideosink
        src = Gst.ElementFactory.make("intervideosrc", "intervideosrc")
        if src is None:
            raise RuntimeError("Failed to create intervideosrc element")
        src.set_property("channel", self.DISPLAY_CHANNEL)
        pipeline_elements.append(src)

        # 2. videoconvert (required)
        vc = Gst.ElementFactory.make("videoconvert", "videoconvert")
        if vc is None:
            raise RuntimeError("Failed to create videoconvert element")
        pipeline_elements.append(vc)

        # 3. videoscale with borders - fits any source into the fixed frame
        # below, letterboxing rather than distorting.
        vs = elements.create_videoscale_element()
        pipeline_elements.append(vs)

        # 4. capsfilter - pins the frame handed to the sink to one fixed size
        # for the whole session. Caps are set once the display is known.
        capsfilter = Gst.ElementFactory.make("capsfilter", "display_caps")
        if capsfilter is None:
            raise RuntimeError("Failed to create capsfilter element")
        pipeline_elements.append(capsfilter)

        # Overlays come AFTER the capsfilter on purpose, so they are
        # composited into that fixed frame: its top-left is the screen's
        # top-left even when the video is inset between bars, and a
        # proportion of it is a constant size on screen because the sink
        # scales the whole frame by one factor.

        # 5. QR code overlay (optional - graceful fallback if unavailable)
        self.qr_overlay = self._create_qr_overlay_element()
        if self.qr_overlay:
            pipeline_elements.append(self.qr_overlay)

        # 6. Text overlay for notifications (optional - graceful fallback)
        self.text_overlay = self._create_text_overlay_element()
        if self.text_overlay:
            pipeline_elements.append(self.text_overlay)

        # Initialize notification lock
        self._notification_lock = threading.Lock()

        # 7. Platform-appropriate video sink
        from ..platform import create_video_sink

        sink = create_video_sink(use_fakesinks=self.use_fakesinks)
        pipeline_elements.append(sink)

        for elem in pipeline_elements:
            pipeline.add(elem)

        for i in range(len(pipeline_elements) - 1):
            if not pipeline_elements[i].link(pipeline_elements[i + 1]):
                raise RuntimeError(
                    f"Failed to link {pipeline_elements[i].get_name()} to "
                    f"{pipeline_elements[i + 1].get_name()}"
                )

        self.display_pipeline = pipeline

        # Watch this pipeline's bus. Without it a failure here is completely
        # silent: the app logs a clean startup and the screen just shows
        # whatever the console was showing.
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_display_error)
        bus.connect("message::warning", self._on_display_warning)

        # READY opens the DRM device, which is what makes kmssink able to
        # report the connector's mode.
        ret = pipeline.set_state(Gst.State.READY)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Display pipeline failed to reach READY state")
        pipeline.get_state(5 * Gst.SECOND)

        self._display_size = self._query_display_size(sink)
        self._render_size = self._choose_render_size(self._display_size)

        if self._render_size:
            width, height = self._render_size
            capsfilter.set_property(
                "caps", Gst.Caps.from_string(_render_caps_string(width, height))
            )
            self.logger.info(
                "Rendering at %dx%d; the sink scales that to the %dx%d display",
                width,
                height,
                self._display_size[0],
                self._display_size[1],
            )
            self._update_qr_size_for_resolution(width, height)
            self._update_text_layout_for_resolution(width)
        else:
            # Sinks that cannot report a mode (autovideosink on macOS,
            # fakesink in tests). Leave the caps open and let the sink cope.
            self.logger.info("Display size unknown; leaving output caps unconstrained")

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Display pipeline failed to reach PLAYING state")

        self.logger.info(
            "Display pipeline started and holding the screen (qr=%s, text=%s)",
            self.qr_overlay is not None,
            self.text_overlay is not None,
        )

    # Frames are rendered at this height (or the display's, if smaller) and
    # the sink scales up from there. Rendering straight at 1080p costs
    # ~46 ms/frame, which caps the pipeline near 22fps and cannot sustain
    # 30fps video; at 720p it is ~25 ms/frame. Sources at or below this
    # height are not degraded, since the sink's upscale is free.
    MAX_RENDER_HEIGHT = 720

    def _choose_render_size(self, display_size):
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
        if display_height <= self.MAX_RENDER_HEIGHT:
            return (display_width, display_height)

        height = self.MAX_RENDER_HEIGHT
        width = int(round(display_width * height / display_height))
        # Even dimensions keep chroma-subsampled formats happy.
        return (width - (width % 2), height - (height % 2))

    def _on_display_error(self, bus, message):
        """Log errors from the display pipeline, which are otherwise silent."""
        err, debug = message.parse_error()
        self.logger.error("Display pipeline error: %s (%s)", err, debug)

    def _on_display_warning(self, bus, message):
        """Log warnings from the display pipeline."""
        err, debug = message.parse_warning()
        self.logger.warning("Display pipeline warning: %s (%s)", err, debug)

    def _query_display_size(self, sink):
        """
        Return the sink's (width, height), or None if it can't report one.

        kmssink exposes display-width/display-height once it has opened the
        DRM device. Other sinks (autovideosink on macOS, fakesink in tests)
        have no such properties, in which case output stays unconstrained.
        """
        try:
            if sink.find_property("display-width") is None:
                return None
            width = sink.get_property("display-width")
            height = sink.get_property("display-height")
            if width and height:
                return (int(width), int(height))
        except Exception as e:
            self.logger.debug("Could not query display size: %s", e)
        return None

    def _create_video_sink_bin(self):
        """
        Create the bin playbin renders video into.

        This is only a bridge to the display pipeline (see
        _create_display_pipeline). Everything that actually touches the
        screen lives over there, so that playbin's per-song state changes
        never reach the real video sink.
        """
        Gst = gst._get_gst()
        video_bin = Gst.Bin.new("video_sink_bin")

        vc = Gst.ElementFactory.make("videoconvert", "playbin_videoconvert")
        if vc is None:
            raise RuntimeError("Failed to create videoconvert element")

        inter = Gst.ElementFactory.make("intervideosink", "intervideosink")
        if inter is None:
            raise RuntimeError("Failed to create intervideosink element")
        inter.set_property("channel", self.DISPLAY_CHANNEL)
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
        vc.get_static_pad("src").add_probe(
            Gst.PadProbeType.EVENT_DOWNSTREAM, self._on_source_caps_event
        )

        self.logger.info("Video sink bin created (bridging to display pipeline)")
        return video_bin

    def _create_signalsmith_pitch_shift(self):
        """Try to create the native signalsmithpitch element, registering its
        plugin .so from disk first if it isn't already known to GStreamer.

        Returns the element, or None if unavailable (caller falls back to
        identity).
        """
        import os

        Gst = gst._get_gst()

        if Gst.ElementFactory.find("signalsmithpitch") is None:
            plugin_path = self.config_manager.get("signalsmith_pitch_plugin_path") or os.path.join(
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
                self.logger.warning("Failed to load signalsmithpitch plugin: %s", e)
                return None

        elem = Gst.ElementFactory.make("signalsmithpitch", "pitch_shift")
        if elem is None:
            return None

        elem.set_property("semitones", self.pitch_shift_semitones)
        self.logger.info("Using native signalsmithpitch element for pitch shift")
        return elem

    def _create_pitch_shift_or_identity(self):
        """Create the native signalsmithpitch element, or identity passthrough
        if its plugin isn't available.
        """
        Gst = gst._get_gst()

        signalsmith_elem = self._create_signalsmith_pitch_shift()
        if signalsmith_elem is not None:
            return signalsmith_elem

        self.logger.warning("No pitch shift element available, falling back to identity")
        return Gst.ElementFactory.make("identity", "pitch_shift")
