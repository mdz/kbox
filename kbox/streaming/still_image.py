"""
Feeds a still image into the display bridge while an interstitial is showing.

Named still_image rather than interstitials because kbox/interstitials.py
already exists -- that module generates the PNGs this one puts on screen.
"""

import logging
from typing import Any

from .gst import _get_gst


class StillImageFeeder:
    """
    Owns the pipeline that pushes one still image at the display bridge.

    Mutually exclusive with playbin: only one of them may feed the bridge at
    a time, which is the caller's job to arrange.
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
        Gst = _get_gst()

        self.stop()

        try:
            self.pipeline = self.create_pipeline(image_path)
        except Exception as e:
            self.logger.error("Failed to build interstitial pipeline: %s", e, exc_info=True)
            return False

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error("Failed to start interstitial playback")
            self.stop()
            return False

        ret, state, pending = self.pipeline.get_state(5 * Gst.SECOND)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error("Interstitial failed to reach PLAYING state")
            self.stop()
            return False

        return True

    def stop(self):
        """Tear down the interstitial pipeline if one is running."""
        if not self.pipeline:
            return

        Gst = _get_gst()
        try:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_state(5 * Gst.SECOND)
        except Exception as e:
            self.logger.warning("Error stopping interstitial pipeline: %s", e)
        finally:
            self.pipeline = None
