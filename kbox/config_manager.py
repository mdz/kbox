"""
Configuration management using database storage.

Provides access to configuration values with defaults and type conversion.
"""

import logging
import sys
from typing import Any, Optional

from .database import ConfigRepository, Database


class ConfigManager:
    """Manages configuration stored in database."""

    @staticmethod
    def _get_platform_defaults():
        """Get platform-specific default values."""
        if sys.platform == "darwin":
            return {
                "gstreamer_source": "osxaudiosrc",
                "gstreamer_sink": "autoaudiosink",
                "rubberband_plugin": "ladspa-ladspa-rubberband-dylib-rubberband-r3-pitchshifter-stereo",
                "audio_input_device": None,
                "audio_output_device": None,
                "video_input_device": None,
            }
        elif sys.platform == "linux":
            return {
                "gstreamer_source": "alsasrc",
                "gstreamer_sink": "alsasink",
                "rubberband_plugin": "ladspa-ladspa-rubberband-so-rubberband-r3-pitchshifter-stereo",
                "audio_input_device": "plughw:CARD=CODEC,DEV=0",
                "audio_output_device": "plughw:CARD=CODEC,DEV=0",
                "video_input_device": None,
            }
        else:
            # Fallback for other platforms
            return {
                "gstreamer_source": "autoaudiosrc",
                "gstreamer_sink": "autoaudiosink",
                "rubberband_plugin": None,
                "audio_input_device": None,
                "audio_output_device": None,
                "video_input_device": None,
            }

    # Default configuration values (merged with platform-specific)
    DEFAULTS = {
        "audio_input_device": None,  # Overridden by platform defaults
        "audio_output_device": None,  # Overridden by platform defaults
        "video_input_device": None,  # Overridden by platform defaults
        "gstreamer_source": None,  # Overridden by platform defaults
        "gstreamer_sink": None,  # Overridden by platform defaults
        "rubberband_plugin": None,  # Overridden by platform defaults
        "youtube_api_key": None,
        "cache_directory": None,  # Will default to ~/.kbox/cache
        "video_max_resolution": "480",  # Max video height for downloads (480, 720, 1080, etc.)
        "operator_pin": "1234",
        "default_mic_volume": "0.8",
        "default_youtube_volume": "0.8",
        "default_reverb_amount": "0.3",
        "reverb_plugin": None,  # Will be determined at runtime
        # Overlay settings
        "external_url": None,  # External URL for QR code (overrides auto-detect)
        "overlay_qr_position": "top-left",  # QR position: top-left, top-right, bottom-left, bottom-right
        "overlay_qr_size_percent": "10",  # QR size as percentage of video height (default 10%)
        # Interstitial settings
        "transition_duration_seconds": "5",  # Duration of transition screen between songs
    }

    # Keys that should be editable via the web UI (in display order)
    # Keys not in this list are internal/system config
    EDITABLE_KEYS = [
        "operator_pin",
        "youtube_api_key",
        "cache_directory",
        "video_max_resolution",
        "external_url",
        "overlay_qr_position",
        "overlay_qr_size_percent",
        "transition_duration_seconds",
        "default_mic_volume",
        "default_youtube_volume",
        "default_reverb_amount",
        "audio_input_device",
        "audio_output_device",
        "video_input_device",
    ]

    def __init__(self, database: Database):
        """
        Initialize ConfigManager.

        Args:
            database: Database instance
        """
        self.database = database
        self.repository = ConfigRepository(database)
        self.logger = logging.getLogger(__name__)
        # Merge platform-specific defaults
        platform_defaults = self._get_platform_defaults()
        self._merged_defaults = {**self.DEFAULTS, **platform_defaults}
        self.repository.initialize_defaults(self._merged_defaults)

    def get(self, key: str, default: Any = None) -> Optional[str]:
        """
        Get a configuration value.

        Args:
            key: Configuration key
            default: Default value if not found (uses merged defaults if None)

        Returns:
            Configuration value as string, or None if not found
        """
        if default is None:
            default = self._merged_defaults.get(key)

        entry = self.repository.get(key)
        if entry:
            return entry.value if entry.value else default
        return default

    def get_int(self, key: str, default: Optional[int] = None) -> Optional[int]:
        """Get configuration value as integer."""
        value = self.get(key)
        if value is None or value == "":
            return default
        try:
            return int(value)
        except ValueError:
            self.logger.warning("Invalid integer value for %s: %s", key, value)
            return default

    def get_float(self, key: str, default: Optional[float] = None) -> Optional[float]:
        """Get configuration value as float."""
        value = self.get(key)
        if value is None or value == "":
            return default
        try:
            return float(value)
        except ValueError:
            self.logger.warning("Invalid float value for %s: %s", key, value)
            return default

    def get_bool(self, key: str, default: Optional[bool] = None) -> Optional[bool]:
        """Get configuration value as boolean."""
        value = self.get(key)
        if value is None or value == "":
            return default
        return value.lower() in ("true", "1", "yes", "on")

    def set(self, key: str, value: Any) -> bool:
        """
        Set a configuration value.

        Args:
            key: Configuration key
            value: Value to set (will be converted to string)

        Returns:
            True if successful
        """
        return self.repository.set(key, str(value))

    def get_all(self) -> dict:
        """
        Get all configuration values.

        Returns:
            Dictionary of all configuration key-value pairs
        """
        entries = self.repository.get_all()
        config = {entry.key: entry.value for entry in entries}

        # Merge with defaults to ensure all keys are present
        result = self._merged_defaults.copy()
        result.update(config)
        return result

    def get_editable_keys(self) -> list:
        """
        Get list of config keys that should be editable via web UI.

        Returns:
            List of key names in display order
        """
        return self.EDITABLE_KEYS.copy()
