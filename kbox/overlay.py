"""
Overlay utilities for kbox.

Provides QR code generation and overlay text formatting.
"""

import logging
import os
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


def generate_qr_code(url: str, size: int = 100, cache_dir: Optional[str] = None) -> Optional[str]:
    """
    Generate a QR code PNG image for the given URL.

    Args:
        url: The URL to encode in the QR code
        size: Size of the QR code in pixels (default 100, should be small for overlay)
        cache_dir: Directory to store the QR code image (default: temp dir)

    Returns:
        Path to the generated QR code PNG, or None if generation failed
    """
    try:
        import qrcode
        from PIL import Image
    except ImportError:
        logger.warning("qrcode or PIL not installed, QR overlay disabled")
        return None

    try:
        # Create QR code with minimal border for small display
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=8,
            border=1,  # Minimal border
        )
        qr.add_data(url)
        qr.make(fit=True)

        # Create image with white background
        img = qr.make_image(fill_color="black", back_color="white")

        # Resize to target size (generate at higher res for quality)
        img = img.resize((size, size), Image.Resampling.LANCZOS)

        # Determine output path
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            output_path = os.path.join(cache_dir, "qr_code.png")
        else:
            # Use temp directory
            output_path = os.path.join(tempfile.gettempdir(), "kbox_qr_code.png")

        # Save image
        img.save(output_path, "PNG")
        logger.info("Generated QR code at: %s (size=%dpx)", output_path, size)

        return output_path

    except Exception as e:
        logger.error("Failed to generate QR code: %s", e, exc_info=True)
        return None


def format_notification(text: str, max_length: int = 50) -> str:
    """
    Format notification text for overlay display.

    Args:
        text: The notification text
        max_length: Maximum length before truncation

    Returns:
        Formatted notification text
    """
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text
