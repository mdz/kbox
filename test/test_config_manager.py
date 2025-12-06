"""
Unit tests for ConfigManager.
"""

import pytest
import sqlite3
import tempfile
import os
from kbox.database import Database
from kbox.config_manager import ConfigManager


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
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
    assert config_manager.get('operator_pin') == '1234'
    assert config_manager.get('midi_input_name') == 'MPK mini Play mk3'
    assert config_manager.get('default_mic_volume') == '0.8'


def test_set_and_get(config_manager):
    """Test setting and getting configuration values."""
    config_manager.set('operator_pin', '5678')
    assert config_manager.get('operator_pin') == '5678'
    
    config_manager.set('test_key', 'test_value')
    assert config_manager.get('test_key') == 'test_value'


def test_get_int(config_manager):
    """Test getting integer configuration values."""
    config_manager.set('test_int', '42')
    assert config_manager.get_int('test_int') == 42
    assert config_manager.get_int('test_int', default=0) == 42
    
    # Test with default
    assert config_manager.get_int('nonexistent', default=10) == 10
    
    # Test with invalid value
    config_manager.set('invalid_int', 'not_a_number')
    assert config_manager.get_int('invalid_int', default=0) == 0


def test_get_float(config_manager):
    """Test getting float configuration values."""
    config_manager.set('test_float', '3.14')
    assert config_manager.get_float('test_float') == 3.14
    
    # Test with default
    assert config_manager.get_float('nonexistent', default=1.0) == 1.0
    
    # Test with invalid value
    config_manager.set('invalid_float', 'not_a_number')
    assert config_manager.get_float('invalid_float', default=0.0) == 0.0


def test_get_bool(config_manager):
    """Test getting boolean configuration values."""
    config_manager.set('test_bool', 'true')
    assert config_manager.get_bool('test_bool') is True
    
    config_manager.set('test_bool', 'false')
    assert config_manager.get_bool('test_bool') is False
    
    config_manager.set('test_bool', '1')
    assert config_manager.get_bool('test_bool') is True
    
    config_manager.set('test_bool', '0')
    assert config_manager.get_bool('test_bool') is False
    
    # Test with default
    assert config_manager.get_bool('nonexistent', default=True) is True


def test_get_all(config_manager):
    """Test getting all configuration values."""
    config_manager.set('operator_pin', '9999')
    config_manager.set('custom_key', 'custom_value')
    
    all_config = config_manager.get_all()
    
    # Should include defaults
    assert 'operator_pin' in all_config
    assert 'midi_input_name' in all_config
    assert 'default_mic_volume' in all_config
    
    # Should include custom values
    assert all_config['operator_pin'] == '9999'
    assert all_config['custom_key'] == 'custom_value'


def test_config_persistence(temp_db):
    """Test that configuration persists across ConfigManager instances."""
    cm1 = ConfigManager(temp_db)
    cm1.set('operator_pin', '7777')
    cm1.set('test_key', 'test_value')
    
    # Create new ConfigManager with same database
    cm2 = ConfigManager(temp_db)
    assert cm2.get('operator_pin') == '7777'
    assert cm2.get('test_key') == 'test_value'


