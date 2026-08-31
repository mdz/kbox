"""
Interstitial display: a still-image imagefreeze pipeline feeding the same
display bridge playbin uses, and the display_image() entry point.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from . import gst


class StillImageMixin:
    """StreamingController methods for showing static images (interstitials)."""

    if TYPE_CHECKING:
        # Provided by StreamingController.__init__ and the other mixins it
        # combines with; declared here only so mypy can typecheck this
        # mixin's methods on their own.
        logger: logging.Logger
        playbin: Any
        current_file: Optional[str]
        DISPLAY_CHANNEL: str
        INTERSTITIAL_FRAMERATE: int

        def _requires_gst(self) -> bool: ...

    def _stop_interstitial_pipeline(self):
        """Tear down the interstitial pipeline if one is running."""
        if not self.interstitial_pipeline:
            return

        Gst = gst._get_gst()
        try:
            self.interstitial_pipeline.set_state(Gst.State.NULL)
            self.interstitial_pipeline.get_state(5 * Gst.SECOND)
        except Exception as e:
            self.logger.warning("Error stopping interstitial pipeline: %s", e)
        finally:
            self.interstitial_pipeline = None

    def _create_interstitial_pipeline(self, image_path: str):
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

        Frames are pushed at INTERSTITIAL_FRAMERATE, which needs to roughly
        match the rate the display pipeline pulls at, or black frames
        interleave with the image.
        """
        Gst = gst._get_gst()

        # parse_launch handles decodebin's dynamic pads for us. The file path
        # is set as a property afterwards rather than interpolated, so paths
        # needing escaping in the launch syntax cannot break the parse.
        pipeline = Gst.parse_launch(
            "filesrc name=isrc ! decodebin ! videoconvert ! imagefreeze name=ifreeze "
            f"! video/x-raw,framerate={self.INTERSTITIAL_FRAMERATE}/1 "
            "! videoconvert ! intervideosink name=isink"
        )

        pipeline.get_by_name("isrc").set_property("location", image_path)
        sink = pipeline.get_by_name("isink")
        sink.set_property("channel", self.DISPLAY_CHANNEL)
        # Paced by the clock rather than pushed as fast as possible, which
        # would spin a core for a static image.
        sink.set_property("sync", True)

        return pipeline

    def display_image(self, image_path: str):
        """
        Display a static image (interstitial screen).

        The image is displayed indefinitely until another file is loaded.
        Interstitials are silent, so no audio is involved at all now that they
        no longer go through playbin.

        Args:
            image_path: Path to the image file to display
        """
        self.logger.debug("Displaying image: %s", image_path)

        if not self._requires_gst():
            return

        Gst = gst._get_gst()

        # Mark that we're showing an interstitial
        self._is_interstitial = True

        # Only one thing may feed the display bridge at a time.
        self.playbin.set_state(Gst.State.NULL)
        self._stop_interstitial_pipeline()

        try:
            self.interstitial_pipeline = self._create_interstitial_pipeline(image_path)
        except Exception as e:
            self.logger.error("Failed to build interstitial pipeline: %s", e, exc_info=True)
            self._is_interstitial = False
            return

        ret = self.interstitial_pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error("Failed to start interstitial playback")
            self._stop_interstitial_pipeline()
            self._is_interstitial = False
            return

        ret, state, pending = self.interstitial_pipeline.get_state(5 * Gst.SECOND)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error("Interstitial failed to reach PLAYING state")
            self._stop_interstitial_pipeline()
            self._is_interstitial = False
            return

        self.state = "playing"
        self.current_file = image_path
        self.logger.info("Image displayed successfully")

    def is_showing_interstitial(self) -> bool:
        """Check if currently showing an interstitial."""
        return self._is_interstitial
