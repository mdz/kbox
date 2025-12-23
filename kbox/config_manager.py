"""
Configuration management using database storage.

Provides access to configuration values with defaults and type conversion.
The CONFIG_SCHEMA provides rich metadata for building user-friendly configuration UIs.
"""

import logging
import sys
from typing import Any, Dict, List, Optional

from .database import ConfigRepository, Database

# Configuration groups define the logical sections in the config UI
CONFIG_GROUPS = {
    "audio": {"label": "Audio Setup", "order": 1},
    "video": {"label": "Video & Display", "order": 2},
    "overlays": {"label": "Video Overlays", "order": 3},
    "security": {"label": "Security", "order": 4},
    "api": {"label": "API & Storage", "order": 5},
}

# Schema defining metadata for each editable configuration key
# This drives the configuration UI - the frontend reads this to render appropriate controls
CONFIG_SCHEMA = {
    # Audio Setup
    "audio_output_device": {
        "group": "audio",
        "label": "Audio Output Device",
        "description": "Select where kbox sends backing track audio. Choose your USB audio interface or sound card.",
        "control": "select",
        "options_provider": "get_audio_devices",  # Dynamic options from platform.py
        "allow_custom": True,  # Allow manual entry for advanced users
    },
    "default_youtube_volume": {
        "group": "audio",
        "label": "Default Music Volume",
        "description": "Starting volume for backing tracks when a song begins.",
        "control": "slider",
        "min": 0,
        "max": 1,
        "step": 0.05,
        "display_format": "percent",
    },
    # Video & Display
    "video_max_resolution": {
        "group": "video",
        "label": "Video Quality",
        "description": "Maximum resolution for downloaded videos. Higher quality uses more storage and bandwidth.",
        "control": "select",
        "options": [
            {"value": "480", "label": "480p (Standard)"},
            {"value": "720", "label": "720p (HD)"},
            {"value": "1080", "label": "1080p (Full HD)"},
        ],
    },
    # Video Overlays
    "overlay_qr_position": {
        "group": "overlays",
        "label": "QR Code Position",
        "description": "Where to display the QR code that links to the web interface.",
        "control": "position_picker",
        "options": [
            {"value": "top-left", "label": "Top Left"},
            {"value": "top-right", "label": "Top Right"},
            {"value": "bottom-left", "label": "Bottom Left"},
            {"value": "bottom-right", "label": "Bottom Right"},
        ],
    },
    "overlay_qr_size_percent": {
        "group": "overlays",
        "label": "QR Code Size",
        "description": "Size of the QR code as a percentage of video height.",
        "control": "slider",
        "min": 5,
        "max": 25,
        "step": 1,
        "display_format": "percent_int",
    },
    "external_url": {
        "group": "overlays",
        "label": "External URL",
        "description": "Override the auto-detected URL shown in the QR code. Leave empty to auto-detect.",
        "control": "text",
        "placeholder": "http://your-server:8000",
    },
    "transition_duration_seconds": {
        "group": "overlays",
        "label": "Transition Duration",
        "description": "How long to show the 'Up Next' screen between songs.",
        "control": "slider",
        "min": 0,
        "max": 10,
        "step": 1,
        "display_format": "seconds",
    },
    # Security
    "operator_pin": {
        "group": "security",
        "label": "Operator PIN",
        "description": "PIN code required to access operator controls (playback, queue management).",
        "control": "password",
    },
    # API & Storage
    "youtube_api_key": {
        "group": "api",
        "label": "YouTube API Key",
        "description": "Your YouTube Data API v3 key for searching videos. Get one from Google Cloud Console.",
        "control": "password",
    },
    "cache_directory": {
        "group": "api",
        "label": "Cache Directory",
        "description": "Where to store downloaded videos. Leave empty for default (~/.kbox/cache).",
        "control": "text",
        "placeholder": "~/.kbox/cache",
    },
}


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
                "audio_output_device": None,
            }
        elif sys.platform == "linux":
            return {
                "gstreamer_source": "alsasrc",
                "gstreamer_sink": "alsasink",
                "rubberband_plugin": "ladspa-ladspa-rubberband-so-rubberband-r3-pitchshifter-stereo",
                "audio_output_device": "plughw:CARD=CODEC,DEV=0",
            }
        else:
            # Fallback for other platforms
            return {
                "gstreamer_source": "autoaudiosrc",
                "gstreamer_sink": "autoaudiosink",
                "rubberband_plugin": None,
                "audio_output_device": None,
            }

    # Default configuration values (merged with platform-specific)
    DEFAULTS = {
        "audio_output_device": None,  # Overridden by platform defaults
        "gstreamer_source": None,  # Overridden by platform defaults
        "gstreamer_sink": None,  # Overridden by platform defaults
        "rubberband_plugin": None,  # Overridden by platform defaults
        "youtube_api_key": None,
        "cache_directory": None,  # Will default to ~/.kbox/cache
        "video_max_resolution": "480",  # Max video height for downloads (480, 720, 1080, etc.)
        "operator_pin": "1234",
        "default_youtube_volume": "0.8",
        "reverb_plugin": None,  # Will be determined at runtime
        # Overlay settings
        "external_url": None,  # External URL for QR code (overrides auto-detect)
        "overlay_qr_position": "top-left",  # QR position: top-left, top-right, bottom-left, bottom-right
        "overlay_qr_size_percent": "10",  # QR size as percentage of video height (default 10%)
        # Interstitial settings
        "transition_duration_seconds": "5",  # Duration of transition screen between songs
    }

    # Editable keys are derived from CONFIG_SCHEMA
    # Keys not in CONFIG_SCHEMA are internal/system config (not shown in UI)

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

    def get_config_schema(self) -> Dict[str, dict]:
        """
        Get the configuration schema with resolved dynamic options.

        Returns:
            Dictionary mapping config keys to their schema definitions,
            with any dynamic options (options_provider) resolved to actual values.
        """
        schema = {}

        for key, key_def in CONFIG_SCHEMA.items():
            # Copy the schema definition (key_def is a dict)
            key_schema: Dict[str, Any] = dict(key_def)  # type: ignore[call-overload]

            # Resolve dynamic options if there's an options_provider
            if "options_provider" in key_schema:
                provider = key_schema.pop("options_provider")
                key_schema["options"] = self._resolve_options(provider)

            schema[key] = key_schema

        return schema

    def _resolve_options(self, provider: str) -> List[dict]:
        """
        Resolve dynamic options from a provider function.

        Args:
            provider: Name of the provider function (e.g., 'get_audio_devices')

        Returns:
            List of option dictionaries with 'value' and 'label' keys
        """
        if provider == "get_audio_devices":
            from .platform import list_audio_output_devices

            return list_audio_output_devices()

        # Unknown provider - return empty list
        self.logger.warning("Unknown options provider: %s", provider)
        return []

    def get_config_groups(self) -> Dict[str, dict]:
        """
        Get the configuration group definitions.

        Returns:
            Dictionary mapping group IDs to their display metadata.
        """
        return CONFIG_GROUPS.copy()

    def get_full_config(self) -> dict:
        """
        Get complete configuration data for the UI.

        Returns:
            Dictionary with 'values', 'schema', and 'groups' keys.
        """
        return {
            "values": self.get_all(),
            "schema": self.get_config_schema(),
            "groups": self.get_config_groups(),
        }
