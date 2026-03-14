"""
Unit tests for ConfigManager.
"""

import os
import tempfile

import pytest

from kbox.config_manager import ConfigManager
from kbox.database import Database


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path=path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def config_manager(temp_db):
    """Create a ConfigManager instance for testing."""
    return ConfigManager(temp_db)


def test_get_default(config_manager):
    """Test getting default configuration values."""
    # Test default values
    assert config_manager.get("operator_pin") == "1234"
    assert config_manager.get("default_youtube_volume") == "0.8"
    assert config_manager.get("transition_duration_seconds") == "5"


def test_set_and_get(config_manager):
    """Test setting and getting configuration values."""
    config_manager.set("operator_pin", "5678")
    assert config_manager.get("operator_pin") == "5678"

    config_manager.set("test_key", "test_value")
    assert config_manager.get("test_key") == "test_value"


def test_get_int(config_manager):
    """Test getting integer configuration values."""
    config_manager.set("test_int", "42")
    assert config_manager.get_int("test_int") == 42
    assert config_manager.get_int("test_int", default=0) == 42

    # Test with default
    assert config_manager.get_int("nonexistent", default=10) == 10

    # Test with invalid value
    config_manager.set("invalid_int", "not_a_number")
    assert config_manager.get_int("invalid_int", default=0) == 0


def test_get_float(config_manager):
    """Test getting float configuration values."""
    config_manager.set("test_float", "3.14")
    assert config_manager.get_float("test_float") == 3.14

    # Test with default
    assert config_manager.get_float("nonexistent", default=1.0) == 1.0

    # Test with invalid value
    config_manager.set("invalid_float", "not_a_number")
    assert config_manager.get_float("invalid_float", default=0.0) == 0.0


def test_get_bool(config_manager):
    """Test getting boolean configuration values."""
    config_manager.set("test_bool", "true")
    assert config_manager.get_bool("test_bool") is True

    config_manager.set("test_bool", "false")
    assert config_manager.get_bool("test_bool") is False

    config_manager.set("test_bool", "1")
    assert config_manager.get_bool("test_bool") is True

    config_manager.set("test_bool", "0")
    assert config_manager.get_bool("test_bool") is False

    # Test with default
    assert config_manager.get_bool("nonexistent", default=True) is True


def test_get_all(config_manager):
    """Test getting all configuration values."""
    config_manager.set("operator_pin", "9999")
    config_manager.set("custom_key", "custom_value")

    all_config = config_manager.get_all()

    # Should include defaults
    assert "operator_pin" in all_config
    assert "default_youtube_volume" in all_config
    assert "transition_duration_seconds" in all_config

    # Should include custom values
    assert all_config["operator_pin"] == "9999"
    assert all_config["custom_key"] == "custom_value"


def test_config_persistence(temp_db):
    """Test that configuration persists across ConfigManager instances."""
    cm1 = ConfigManager(temp_db)
    cm1.set("operator_pin", "7777")
    cm1.set("test_key", "test_value")

    # Create new ConfigManager with same database
    cm2 = ConfigManager(temp_db)
    assert cm2.get("operator_pin") == "7777"
    assert cm2.get("test_key") == "test_value"


class TestConfigSchema:
    def test_get_config_schema_returns_all_schema_keys(self, config_manager):
        schema = config_manager.get_config_schema()
        assert "operator_pin" in schema
        assert "llm_model" in schema
        assert "audio_output_device" in schema
        assert "video_max_resolution" in schema

    def test_schema_entries_have_required_fields(self, config_manager):
        schema = config_manager.get_config_schema()
        for key, entry in schema.items():
            assert "group" in entry, f"{key} missing 'group'"
            assert "label" in entry, f"{key} missing 'label'"
            assert "control" in entry, f"{key} missing 'control'"

    def test_schema_resolves_audio_device_options(self, config_manager):
        # Simpler: just verify it doesn't crash and returns options list
        schema = config_manager.get_config_schema()
        audio_entry = schema["audio_output_device"]
        assert "options" in audio_entry
        assert isinstance(audio_entry["options"], list)

    def test_schema_does_not_contain_options_provider(self, config_manager):
        schema = config_manager.get_config_schema()
        for key, entry in schema.items():
            assert "options_provider" not in entry, f"{key} still has options_provider"


class TestConfigGroups:
    def test_get_config_groups_returns_expected_groups(self, config_manager):
        groups = config_manager.get_config_groups()
        assert "audio" in groups
        assert "video" in groups
        assert "overlays" in groups
        assert "suggestions" in groups
        assert "security" in groups
        assert "api" in groups
        assert "queue" in groups

    def test_groups_have_label_and_order(self, config_manager):
        groups = config_manager.get_config_groups()
        for group_id, group in groups.items():
            assert "label" in group, f"{group_id} missing 'label'"
            assert "order" in group, f"{group_id} missing 'order'"

    def test_get_config_groups_returns_copy(self, config_manager):
        groups1 = config_manager.get_config_groups()
        groups2 = config_manager.get_config_groups()
        assert groups1 is not groups2


class TestFullConfig:
    def test_get_full_config_has_required_keys(self, config_manager):
        full = config_manager.get_full_config()
        assert "values" in full
        assert "schema" in full
        assert "groups" in full

    def test_full_config_values_include_defaults(self, config_manager):
        full = config_manager.get_full_config()
        assert "operator_pin" in full["values"]
        assert full["values"]["operator_pin"] == "1234"

    def test_full_config_schema_matches_get_config_schema(self, config_manager):
        full = config_manager.get_full_config()
        schema = config_manager.get_config_schema()
        assert set(full["schema"].keys()) == set(schema.keys())


class TestPlatformDefaults:
    def test_darwin_defaults(self):
        from unittest.mock import patch

        with patch("kbox.config_manager.sys") as mock_sys:
            mock_sys.platform = "darwin"
            defaults = ConfigManager._get_platform_defaults()
            assert defaults["gstreamer_source"] == "osxaudiosrc"
            assert defaults["gstreamer_sink"] == "autoaudiosink"
            assert defaults["audio_output_device"] is None

    def test_linux_defaults(self):
        from unittest.mock import patch

        with patch("kbox.config_manager.sys") as mock_sys:
            mock_sys.platform = "linux"
            defaults = ConfigManager._get_platform_defaults()
            assert defaults["gstreamer_source"] == "alsasrc"
            assert defaults["gstreamer_sink"] == "alsasink"
            assert defaults["audio_output_device"] == "plughw:CARD=CODEC,DEV=0"

    def test_unknown_platform_defaults(self):
        from unittest.mock import patch

        with patch("kbox.config_manager.sys") as mock_sys:
            mock_sys.platform = "win32"
            defaults = ConfigManager._get_platform_defaults()
            assert defaults["gstreamer_source"] == "autoaudiosrc"
            assert defaults["gstreamer_sink"] == "autoaudiosink"
