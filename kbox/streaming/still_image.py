"""
Feeds a still picture into the display bridge: an interstitial image, or the
last video frame while playback is paused.

Named still_image rather than interstitials because kbox/interstitials.py
already exists -- that module generates the PNGs this one puts on screen.
"""

import logging
from typing import Any

from .gst import _get_gst


class StillImageFeeder:
    """
    Owns the pipeline that pushes one still picture at the display bridge.

    Mutually exclusive with playbin feeding the bridge: only one of them may
    feed it at a time, which is the caller's job to arrange. A paused playbin
    does not count as feeding -- it pushes one preroll frame and goes quiet,
    which is why start_frame exists.
    """

    def __init__(self, channel: str, framerate: int):
        """
        Args:
            channel: intervideosink channel to feed (must match the display
                pipeline's intervideosrc)
            framerate: rate to push frames at -- see create_pipeline
        """
        self.channel = channel
        self.framerate = framerate
        self.logger = logging.getLogger(__name__)

        self.pipeline: Any = None

    def create_pipeline(self, image_path: str):
        """
        Build a pipeline that feeds a still image into the display bridge
        continuously.

        imagefreeze is the point of this. A still image decoded on its own
        produces exactly one buffer and then end-of-stream, which starves
        intervideosrc: it serves a buffer only a couple of times before it
        starts generating black frames instead. That is what blanked the idle
        screen to black (bar the QR, composited further down the display
        pipeline) a moment after every stop. imagefreeze repeats the frame for
        as long as the pipeline runs, so the bridge keeps being fed and the
        screen keeps showing the interstitial.

        This used to go through playbin, on the assumption that playbin would
        insert imagefreeze itself for still images. It does not. It worked
        anyway only because kmssink used to sit in the playbin pipeline and
        went on scanning out its last framebuffer after EOS -- persistence
        that disappeared once the sink moved to its own pipeline.

        Frames are pushed at the configured framerate, which needs to roughly
        match the rate the display pipeline pulls at, or black frames
        interleave with the image.
        """
        Gst = _get_gst()

        # parse_launch handles decodebin's dynamic pads for us. The file path
        # is set as a property afterwards rather than interpolated, so paths
        # needing escaping in the launch syntax cannot break the parse.
        pipeline = Gst.parse_launch(
            "filesrc name=isrc ! decodebin ! videoconvert ! imagefreeze name=ifreeze "
            f"! video/x-raw,framerate={self.framerate}/1 "
            "! videoconvert ! intervideosink name=isink"
        )

        pipeline.get_by_name("isrc").set_property("location", image_path)
        sink = pipeline.get_by_name("isink")
        sink.set_property("channel", self.channel)
        # Paced by the clock rather than pushed as fast as possible, which
        # would spin a core for a static image.
        sink.set_property("sync", True)

        return pipeline

    def start(self, image_path: str) -> bool:
        """
        Build and start feeding `image_path`, replacing anything running.

        Returns True once the pipeline is PLAYING, False (having cleaned up)
        if any step failed. Whether the caller is "showing an interstitial" is
        the caller's state, not this object's.
        """
        self.stop()

        try:
            self.pipeline = self.create_pipeline(image_path)
        except Exception as e:
            self.logger.error("Failed to build interstitial pipeline: %s", e, exc_info=True)
            return False

        return self._run()

    def create_frame_pipeline(self, caps):
        """
        Build a pipeline that repeats one already-decoded frame into the
        display bridge, the same way create_pipeline repeats an image file.

        start_frame pushes the frame into the appsrc once the pipeline has
        started: an appsrc refuses buffers before then.
        """
        Gst = _get_gst()

        pipeline = Gst.parse_launch(
            "appsrc name=asrc format=time ! imagefreeze "
            f"! video/x-raw,framerate={self.framerate}/1 "
            "! videoconvert ! intervideosink name=isink"
        )
        pipeline.get_by_name("asrc").set_property("caps", caps)
        sink = pipeline.get_by_name("isink")
        sink.set_property("channel", self.channel)
        sink.set_property("sync", True)

        return pipeline

    def start_frame(self, sample) -> bool:
        """
        Build and start feeding the frame in `sample` (a Gst.Sample: buffer
        plus the caps it was negotiated with), replacing anything running.

        For holding the picture while playback is paused. playbin pushes one
        preroll frame into the bridge across the PAUSED transition and then
        stops, and intervideosrc serves a frame only a couple of times before
        falling back to black (its "timeout" property does not hold one --
        see docs/development/gstreamer-pipeline.md). Without this the screen
        blanks to black behind the overlays a moment into every pause.

        Returns True once the pipeline is PLAYING, False (having cleaned up)
        if any step failed.
        """
        self.stop()

        try:
            self.pipeline = self.create_frame_pipeline(sample.get_caps())
            # A copy with timestamps reset: the original carries the song's
            # running time, which means nothing to this fresh pipeline.
            buf = sample.get_buffer().copy()
            buf.pts = 0
            buf.dts = 0
        except Exception as e:
            self.logger.error("Failed to build pause frame pipeline: %s", e, exc_info=True)
            self.stop()
            return False

        def push_frame():
            appsrc = self.pipeline.get_by_name("asrc")
            appsrc.emit("push-buffer", buf)
            appsrc.emit("end-of-stream")

        return self._run(after_start=push_frame)

    def _run(self, after_start=None) -> bool:
        """
        Set the built pipeline PLAYING and wait for it to get there.

        `after_start` runs between requesting the state change and waiting
        on it: an appsrc-fed pipeline cannot preroll until it has data, and
        cannot take data until it has started.
        """
        Gst = _get_gst()

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error("Failed to start still image pipeline")
            self.stop()
            return False

        if after_start is not None:
            after_start()

        ret, state, pending = self.pipeline.get_state(5 * Gst.SECOND)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error("Still image pipeline failed to reach PLAYING state")
            self.stop()
            return False

        return True

    def stop(self):
        """Tear down the still image pipeline if one is running."""
        if not self.pipeline:
            return

        Gst = _get_gst()
        try:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_state(5 * Gst.SECOND)
        except Exception as e:
            self.logger.warning("Error stopping still image pipeline: %s", e)
        finally:
            self.pipeline = None
