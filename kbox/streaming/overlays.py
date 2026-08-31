"""
QR code and text overlay: creation, resolution-based sizing, notifications,
and visibility.
"""

import logging
import threading
from typing import TYPE_CHECKING, Any, Optional

from . import elements


class OverlayMixin:
    """StreamingController methods for the QR code and text overlays."""

    if TYPE_CHECKING:
        # Provided by StreamingController.__init__ and the other mixins it
        # combines with; declared here only so mypy can typecheck this
        # mixin's methods on their own.
        logger: logging.Logger
        config_manager: Any
        qr_overlay: Any
        text_overlay: Any
        _notification_lock: Any
        _notification_timer: Any
        _display_size: Optional[tuple]
        _qr_image_path: Optional[str]

        def _requires_gst(self) -> bool: ...

    def _create_qr_overlay_element(self):
        """Create gdkpixbufoverlay element for QR code, or None if unavailable."""
        qr, position, size_percent, current_size, current_padding = (
            elements.create_qr_overlay_element(self.config_manager, self.logger)
        )
        if qr is not None:
            self._qr_position = position
            self._qr_size_percent = size_percent
            self._qr_current_size = current_size
            self._qr_current_padding = current_padding
        return qr

    def _create_text_overlay_element(self):
        """Create textoverlay element for notifications, or None if unavailable."""
        return elements.create_text_overlay_element(self.logger)

    def _caps_dimensions(self, caps):
        """
        Best-effort (width, height) from a caps object, or (None, None).

        Tries several access styles because the structure API varies between
        PyGObject versions, falling back to parsing the caps string.
        """
        struct = caps.get_structure(0) if caps else None
        if struct is None:
            return (None, None)

        # Dictionary-style access first (most compatible)
        if hasattr(struct, "__getitem__"):
            try:
                return (struct["width"], struct["height"])
            except (KeyError, TypeError):
                pass

        # Some versions expose get_value instead
        if hasattr(struct, "get_value"):
            try:
                return (struct.get_value("width"), struct.get_value("height"))
            except Exception:
                pass

        # Last resort: parse the serialised caps
        import re

        caps_str = caps.to_string()
        width_match = re.search(r"width=\(int\)(\d+)", caps_str)
        height_match = re.search(r"height=\(int\)(\d+)", caps_str)
        if width_match and height_match:
            return (int(width_match.group(1)), int(height_match.group(1)))

        return (None, None)

    def _on_source_caps_event(self, pad, info):
        """
        Log the dimensions of the video stream playbin is actually decoding.

        Purely diagnostic. The display pipeline rescales everything to the
        screen, so without this the source resolution is invisible in the logs
        and has to be recovered by probing the file on disk.
        """
        from . import gst

        Gst = gst._get_gst()

        try:
            event = info.get_event()
            if event is None or event.type != Gst.EventType.CAPS:
                return Gst.PadProbeReturn.OK

            caps = event.parse_caps()
            width, height = self._caps_dimensions(caps)
            if width is not None and height is not None:
                display = self._display_size
                if display:
                    self.logger.info(
                        "Source video stream: %dx%d (scaled to %dx%d for display)",
                        width,
                        height,
                        display[0],
                        display[1],
                    )
                else:
                    self.logger.info("Source video stream: %dx%d", width, height)
            else:
                self.logger.debug(
                    "Could not extract source dimensions from caps: %s",
                    caps.to_string()[:200] if caps else None,
                )
        except Exception as e:
            self.logger.warning("Error logging source video caps: %s", e)

        return Gst.PadProbeReturn.OK

    def _update_qr_size_for_resolution(self, width, height):
        """Update QR overlay size and position based on video resolution."""
        if not self.qr_overlay:
            return

        try:
            # Size QR as configured percentage of video height
            percent = self._qr_size_percent / 100.0
            qr_size = max(48, int(height * percent))  # Minimum 48px for scannability
            padding = max(10, int(height * 0.02))  # ~2% padding

            # Calculate position based on configured corner
            position = self._qr_position
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
            self._qr_current_size = qr_size
            self._qr_current_padding = padding

            self.logger.info(
                "QR overlay sized for %dx%d: size=%dpx (%d%%), position=%s at (%d,%d)",
                width,
                height,
                qr_size,
                self._qr_size_percent,
                position,
                x,
                y,
            )

        except Exception as e:
            self.logger.warning("Failed to update QR size for resolution: %s", e)

    # textoverlay's auto-resize scales the font relative to a 640-pixel-wide
    # frame. Overlay geometry below is expressed against that same basis so it
    # scales consistently with the font.
    TEXT_SCALE_BASIS_WIDTH = 640
    TEXT_BASE_PADDING = 20

    def _update_text_layout_for_resolution(self, width):
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
            scale = width / self.TEXT_SCALE_BASIS_WIDTH
            padding = max(10, int(self.TEXT_BASE_PADDING * scale))

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
    # Overlay Control
    # =========================================================================

    def show_notification(self, text: str, duration_seconds: float = 5.0):
        """
        Show transient text notification that auto-hides.

        Args:
            text: Notification text to display
            duration_seconds: How long to show the notification (default 5s)
        """
        from .gst import _escape_overlay_text

        if not self._requires_gst():
            return
        if not self.text_overlay:
            self.logger.warning("Text overlay not available, skipping notification")
            return

        if not self._notification_lock:
            self.logger.warning("Notification lock not initialized, skipping notification")
            return

        with self._notification_lock:
            # Cancel any pending hide timer
            if self._notification_timer:
                self._notification_timer.cancel()
                self._notification_timer = None

            try:
                # Show the text
                self.text_overlay.set_property("text", _escape_overlay_text(text))
                self.text_overlay.set_property("silent", False)
                self.logger.info("Showing notification: %s", text)

                # No frame regeneration needed. The text overlay lives in the
                # display pipeline, which is fed continuously -- by playbin
                # during a song, by imagefreeze during an interstitial -- so
                # the overlay is composited onto every frame as it passes.
                # This used to seek playbin to force a still image to be
                # redrawn, which is now both unnecessary and wrong: playbin is
                # in NULL while an interstitial shows.

                # Schedule hide after duration
                def hide_notification():
                    self._hide_notification()

                self._notification_timer = threading.Timer(duration_seconds, hide_notification)
                self._notification_timer.daemon = True
                self._notification_timer.start()

            except Exception as e:
                self.logger.warning("Failed to show notification: %s", e)

    def _hide_notification(self):
        """Hide the current notification."""
        if not self.text_overlay:
            return

        if not self._notification_lock:
            return

        with self._notification_lock:
            try:
                self.text_overlay.set_property("text", "")
                self.text_overlay.set_property("silent", True)
                self._notification_timer = None

                # No frame regeneration needed here either -- see
                # show_notification.

                self.logger.debug("Notification hidden")
            except Exception as e:
                self.logger.warning("Failed to hide notification: %s", e)

    def set_overlay_text(self, text: str):
        """
        Set persistent overlay text (does not auto-hide).
        Use empty string to clear the overlay.

        Args:
            text: Text to display, or empty string to hide
        """
        from .gst import _escape_overlay_text

        if not self._requires_gst():
            return
        if not self.text_overlay:
            self.logger.debug("Text overlay not available, skipping")
            return

        if not self._notification_lock:
            return

        with self._notification_lock:
            # Cancel any pending auto-hide timer
            if self._notification_timer:
                self._notification_timer.cancel()
                self._notification_timer = None

            try:
                if text:
                    self.text_overlay.set_property("text", _escape_overlay_text(text))
                    self.text_overlay.set_property("silent", False)
                    self.logger.debug("Set persistent overlay text: %s", text)
                else:
                    self.text_overlay.set_property("text", "")
                    self.text_overlay.set_property("silent", True)
                    self.logger.debug("Cleared overlay text")
            except Exception as e:
                self.logger.warning("Failed to set overlay text: %s", e)

    def update_qr_overlay(self, image_path: str):
        """
        Update QR code overlay image.

        Args:
            image_path: Path to the QR code PNG image
        """
        self._qr_image_path = image_path

        if not self._requires_gst():
            return
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
        if not self._requires_gst():
            return
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
