"""
The QR and text overlay elements composited into the display pipeline.

OverlayController owns everything about the overlays that is not the elements
themselves: the remembered QR image path and the QR's current geometry. The
elements are built by DisplayPipeline (they are linked into it) and handed in
here, so this object can equally well be constructed with stub elements that
only implement set_property / get_property.

It holds no lock and runs no timer. What the text says, and for how long, is
decided by kbox.playback.overlay.OverlayCoordinator, which calls
set_overlay_text() under its own lock.
"""

import logging
from typing import Any, Optional

from .elements import QR_INITIAL_PADDING, QR_INITIAL_SIZE
from .gst import _escape_overlay_text

# textoverlay's auto-resize scales the font relative to a 640-pixel-wide
# frame. Overlay geometry below is expressed against that same basis so it
# scales consistently with the font.
TEXT_SCALE_BASIS_WIDTH = 640
TEXT_BASE_PADDING = 20


class OverlayController:
    """Drives the QR and text overlay elements of the display pipeline."""

    def __init__(
        self,
        qr_overlay: Any,
        text_overlay: Any,
        config_manager,
        qr_image_path: Optional[str] = None,
    ):
        """
        Args:
            qr_overlay: the gdkpixbufoverlay element, or None if unavailable
            text_overlay: the textoverlay element, or None if unavailable
            config_manager: Configuration manager instance
            qr_image_path: QR image to remember from a previous incarnation,
                so it can be re-applied after the pipeline is rebuilt
        """
        self.qr_overlay = qr_overlay
        self.text_overlay = text_overlay
        self.config_manager = config_manager
        self.logger = logging.getLogger(__name__)

        self._qr_image_path: Optional[str] = qr_image_path  # Track QR image path for reinit

        # QR geometry. Config is read once here; the current size/padding
        # start at the values create_qr_overlay_element() put on the element
        # and are updated as soon as the render size is known.
        self.qr_position = self.config_manager.get("overlay_qr_position") or "top-left"
        self.qr_size_percent = self.config_manager.get_int("overlay_qr_size_percent", 10)
        self.qr_current_size = QR_INITIAL_SIZE
        self.qr_current_padding = QR_INITIAL_PADDING

    @property
    def qr_image_path(self) -> Optional[str]:
        """The last QR image handed to update_qr_overlay, if any."""
        return self._qr_image_path

    # =========================================================================
    # Sizing
    # =========================================================================

    def update_qr_size_for_resolution(self, width, height):
        """Update QR overlay size and position based on video resolution."""
        if not self.qr_overlay:
            return

        try:
            # Size QR as configured percentage of video height
            percent = self.qr_size_percent / 100.0
            qr_size = max(48, int(height * percent))  # Minimum 48px for scannability
            padding = max(10, int(height * 0.02))  # ~2% padding

            # Calculate position based on configured corner
            position = self.qr_position
            if position == "top-left":
                x, y = padding, padding
            elif position == "top-right":
                x, y = width - qr_size - padding, padding
            elif position == "bottom-left":
                x, y = padding, height - qr_size - padding
            else:  # bottom-right
                x, y = width - qr_size - padding, height - qr_size - padding

            # Update overlay properties
            self.qr_overlay.set_property("overlay-width", qr_size)
            self.qr_overlay.set_property("overlay-height", qr_size)
            self.qr_overlay.set_property("offset-x", x)
            self.qr_overlay.set_property("offset-y", y)

            # Store current values
            self.qr_current_size = qr_size
            self.qr_current_padding = padding

            self.logger.info(
                "QR overlay sized for %dx%d: size=%dpx (%d%%), position=%s at (%d,%d)",
                width,
                height,
                qr_size,
                self.qr_size_percent,
                position,
                x,
                y,
            )

        except Exception as e:
            self.logger.warning("Failed to update QR size for resolution: %s", e)

    def update_text_layout_for_resolution(self, width):
        """
        Scale the notification text's padding to the frame it is drawn on.

        The font size deliberately is NOT set here. textoverlay's auto-resize
        property (on by default) already scales the font by
        width / TEXT_SCALE_BASIS_WIDTH, so setting a size derived from the
        display would scale it a second time -- on a 1080p screen that turned
        "Sans 9" into roughly 66pt of text across most of the screen.

        Leaving the font fixed lets auto-resize do the scaling: on a 1920-wide
        screen "Sans 9" renders at the same effective size it had when the
        overlay sat upstream of the scaler on a 640-wide frame.

        xpad/ypad are raw pixels that auto-resize does not touch, so those do
        have to be scaled here to keep the margins looking the same.
        """
        if not self.text_overlay:
            return

        try:
            scale = width / TEXT_SCALE_BASIS_WIDTH
            padding = max(10, int(TEXT_BASE_PADDING * scale))

            self.text_overlay.set_property("xpad", padding)
            self.text_overlay.set_property("ypad", padding)

            self.logger.info(
                "Text overlay padding scaled for width %d: %dpx (font left to auto-resize)",
                width,
                padding,
            )
        except Exception as e:
            self.logger.warning("Failed to update text layout for resolution: %s", e)

    # =========================================================================
    # Text
    # =========================================================================

    def set_overlay_text(self, text: str):
        """
        Set the overlay text. Use empty string to clear the overlay.

        Args:
            text: Text to display, or empty string to hide
        """
        if not self.text_overlay:
            self.logger.debug("Text overlay not available, skipping")
            return

        # No frame regeneration needed. The text overlay lives in the display
        # pipeline, which is fed continuously -- by playbin during a song, by
        # imagefreeze during an interstitial -- so the overlay is composited
        # onto every frame as it passes.
        try:
            if text:
                self.text_overlay.set_property("text", _escape_overlay_text(text))
                self.text_overlay.set_property("silent", False)
                self.logger.debug("Set overlay text: %s", text)
            else:
                self.text_overlay.set_property("text", "")
                self.text_overlay.set_property("silent", True)
                self.logger.debug("Cleared overlay text")
        except Exception as e:
            self.logger.warning("Failed to set overlay text: %s", e)

    # =========================================================================
    # QR
    # =========================================================================

    def update_qr_overlay(self, image_path: str):
        """
        Update QR code overlay image.

        Args:
            image_path: Path to the QR code PNG image
        """
        # Recorded before the element guard on purpose, so the path survives a
        # period with no element at all -- GStreamer missing, or a
        # config-change rebuild in progress -- and reinitialize_pipeline can
        # re-apply it to the next one.
        self._qr_image_path = image_path

        if not self.qr_overlay:
            self.logger.debug("QR overlay not available")
            return

        try:
            import os

            if not os.path.exists(image_path):
                self.logger.warning("QR image not found: %s", image_path)
                return

            # Verify file size
            file_size = os.path.getsize(image_path)
            self.logger.debug("QR image file size: %d bytes", file_size)

            self.qr_overlay.set_property("location", image_path)

            # Log current overlay properties for debugging
            try:
                loc = self.qr_overlay.get_property("location")
                ox = self.qr_overlay.get_property("offset-x")
                oy = self.qr_overlay.get_property("offset-y")
                ow = self.qr_overlay.get_property("overlay-width")
                oh = self.qr_overlay.get_property("overlay-height")
                alpha = self.qr_overlay.get_property("alpha")
                self.logger.info(
                    "QR overlay configured: location=%s, offset=(%d,%d), size=%dx%d, alpha=%.2f",
                    loc,
                    ox,
                    oy,
                    ow,
                    oh,
                    alpha,
                )
            except Exception as prop_err:
                self.logger.warning("Could not read overlay properties: %s", prop_err)

        except Exception as e:
            self.logger.warning("Failed to update QR overlay: %s", e)

    def set_qr_visible(self, visible: bool):
        """
        Toggle QR code visibility.

        Args:
            visible: True to show, False to hide
        """
        if not self.qr_overlay:
            self.logger.debug("QR overlay not available")
            return

        try:
            if visible:
                self.qr_overlay.set_property("alpha", 0.9)
            else:
                self.qr_overlay.set_property("alpha", 0.0)
            self.logger.debug("QR overlay visibility set to: %s", visible)

        except Exception as e:
            self.logger.warning("Failed to set QR visibility: %s", e)
