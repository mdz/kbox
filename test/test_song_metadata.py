"""
Unit tests for SongMetadataExtractor.

Tests the metadata extraction logic without making actual LLM calls.
Uses dependency injection for the LLM completion function.
"""

import json
import os
import tempfile
from typing import Any
from unittest.mock import MagicMock

import pytest

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.song_metadata import SongMetadataExtractor

# =============================================================================
# Fake LLM completion functions for testing
# =============================================================================


def make_fake_completion(artist: str, song_name: str):
    """
    Create a fake completion function that returns the given metadata.

    Args:
        artist: Artist name to return
        song_name: Song name to return
    """

    def fake_completion(**kwargs) -> Any:
        """Fake LLM completion that returns predictable responses."""
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps({"artist": artist, "song_name": song_name})
        return response

    return fake_completion


def make_markdown_completion(artist: str, song_name: str):
    """Create a fake completion that returns JSON wrapped in markdown code blocks."""

    def fake_completion(**kwargs) -> Any:
        response = MagicMock()
        response.choices = [MagicMock()]
        content = f'```json\n{{"artist": "{artist}", "song_name": "{song_name}"}}\n```'
        response.choices[0].message.content = content
        return response

    return fake_completion


def make_empty_completion():
    """Create a fake completion that returns empty content."""

    def fake_completion(**kwargs) -> Any:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = ""
        return response

    return fake_completion


def make_invalid_json_completion():
    """Create a fake completion that returns invalid JSON."""

    def fake_completion(**kwargs) -> Any:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "This is not JSON"
        return response

    return fake_completion


def make_partial_completion():
    """Create a fake completion that returns partial data (missing song_name)."""

    def fake_completion(**kwargs) -> Any:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps({"artist": "Journey"})
        return response

    return fake_completion


def make_error_completion(error_message: str = "API error"):
    """Create a fake completion that raises an exception."""

    def fake_completion(**kwargs) -> Any:
        raise Exception(error_message)

    return fake_completion


# =============================================================================
# Fixtures
# =============================================================================


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
    """Create a ConfigManager with test database."""
    return ConfigManager(temp_db)


@pytest.fixture
def configured_extractor(temp_db, config_manager):
    """Create a fully configured extractor with fake completion."""
    config_manager.set("llm_model", "gpt-4o-mini")
    config_manager.set("llm_api_key", "test-key")
    return SongMetadataExtractor(
        config_manager=config_manager,
        database=temp_db,
        completion_fn=make_fake_completion("Journey", "Don't Stop Believin'"),
    )


@pytest.fixture
def unconfigured_extractor(temp_db, config_manager):
    """Create an extractor without LLM configuration."""
    return SongMetadataExtractor(
        config_manager=config_manager,
        database=temp_db,
    )


# =============================================================================
# Test is_configured()
# =============================================================================


def test_is_configured_with_model_and_key(temp_db, config_manager):
    """Test is_configured returns True when model and API key are set."""
    config_manager.set("llm_model", "gpt-4o-mini")
    config_manager.set("llm_api_key", "test-key")
    extractor = SongMetadataExtractor(config_manager, temp_db)
    assert extractor.is_configured() is True


def test_is_configured_with_ollama_no_key(temp_db, config_manager):
    """Test is_configured returns True for Ollama models without API key."""
    config_manager.set("llm_model", "ollama/llama3")
    extractor = SongMetadataExtractor(config_manager, temp_db)
    assert extractor.is_configured() is True


def test_is_configured_without_model(temp_db, config_manager):
    """Test is_configured returns False when model is not set."""
    extractor = SongMetadataExtractor(config_manager, temp_db)
    assert extractor.is_configured() is False


def test_is_configured_without_api_key(temp_db, config_manager):
    """Test is_configured returns False for non-Ollama models without API key."""
    config_manager.set("llm_model", "gpt-4o-mini")
    extractor = SongMetadataExtractor(config_manager, temp_db)
    assert extractor.is_configured() is False


# =============================================================================
# Test extract() - Basic functionality
# =============================================================================


def test_extract_success(configured_extractor):
    """Test successful extraction returns artist and song name."""
    artist, song_name = configured_extractor.extract(
        video_id="youtube:abc123",
        title="Journey - Don't Stop Believin' (Karaoke Version)",
        channel="Zoom Karaoke",
    )
    assert artist == "Journey"
    assert song_name == "Don't Stop Believin'"


def test_extract_unconfigured_returns_none(unconfigured_extractor):
    """Test extraction returns None when LLM is not configured."""
    artist, song_name = unconfigured_extractor.extract(
        video_id="youtube:abc123",
        title="Journey - Don't Stop Believin'",
    )
    assert artist is None
    assert song_name is None


def test_extract_with_description(temp_db, config_manager):
    """Test extraction uses description when provided."""
    config_manager.set("llm_model", "gpt-4o-mini")
    config_manager.set("llm_api_key", "test-key")

    captured_kwargs = {}

    def capturing_completion(**kwargs):
        captured_kwargs.update(kwargs)
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps(
            {"artist": "Queen", "song_name": "Bohemian Rhapsody"}
        )
        return response

    extractor = SongMetadataExtractor(config_manager, temp_db, capturing_completion)
    extractor.extract(
        video_id="youtube:xyz789",
        title="Karaoke - Bohemian Rhapsody",
        description="Originally performed by Queen",
        channel="KaraFun",
    )

    # Verify description was included in prompt
    prompt = captured_kwargs["messages"][1]["content"]
    assert "Originally performed by Queen" in prompt


# =============================================================================
# Test extract() - Caching
# =============================================================================


def test_extract_caches_result(temp_db, config_manager):
    """Test that successful extraction results are cached."""
    config_manager.set("llm_model", "gpt-4o-mini")
    config_manager.set("llm_api_key", "test-key")

    call_count = 0

    def counting_completion(**kwargs):
        nonlocal call_count
        call_count += 1
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps(
            {"artist": "ABBA", "song_name": "Dancing Queen"}
        )
        return response

    extractor = SongMetadataExtractor(config_manager, temp_db, counting_completion)

    # First call should hit LLM
    artist1, song1 = extractor.extract("youtube:abba1", "ABBA - Dancing Queen")
    assert call_count == 1
    assert artist1 == "ABBA"
    assert song1 == "Dancing Queen"

    # Second call should use cache
    artist2, song2 = extractor.extract("youtube:abba1", "ABBA - Dancing Queen")
    assert call_count == 1  # No additional LLM call
    assert artist2 == "ABBA"
    assert song2 == "Dancing Queen"


def test_extract_cache_persists_across_instances(temp_db, config_manager):
    """Test that cache persists in database across extractor instances."""
    config_manager.set("llm_model", "gpt-4o-mini")
    config_manager.set("llm_api_key", "test-key")

    # First instance extracts and caches
    extractor1 = SongMetadataExtractor(config_manager, temp_db, make_fake_completion("U2", "One"))
    artist1, song1 = extractor1.extract("youtube:u2one", "U2 - One")
    assert artist1 == "U2"
    assert song1 == "One"

    # Second instance should find it in cache (different fake completion proves cache hit)
    extractor2 = SongMetadataExtractor(
        config_manager, temp_db, make_fake_completion("Wrong", "Wrong")
    )
    artist2, song2 = extractor2.extract("youtube:u2one", "U2 - One")
    assert artist2 == "U2"  # From cache, not the "Wrong" completion
    assert song2 == "One"


def test_extract_different_videos_not_cached(temp_db, config_manager):
    """Test that different video IDs are not confused in cache."""
    config_manager.set("llm_model", "gpt-4o-mini")
    config_manager.set("llm_api_key", "test-key")

    call_count = 0

    def counting_completion(**kwargs):
        nonlocal call_count
        call_count += 1
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps(
            {"artist": f"Artist{call_count}", "song_name": f"Song{call_count}"}
        )
        return response

    extractor = SongMetadataExtractor(config_manager, temp_db, counting_completion)

    # Different video IDs should each call LLM
    extractor.extract("youtube:video1", "Title 1")
    extractor.extract("youtube:video2", "Title 2")
    assert call_count == 2


# =============================================================================
# Test extract() - Error handling
# =============================================================================


def test_extract_empty_response_returns_none(temp_db, config_manager):
    """Test that empty LLM response returns None."""
    config_manager.set("llm_model", "gpt-4o-mini")
    config_manager.set("llm_api_key", "test-key")

    extractor = SongMetadataExtractor(config_manager, temp_db, make_empty_completion())
    artist, song_name = extractor.extract("youtube:empty", "Some Title")
    assert artist is None
    assert song_name is None


def test_extract_invalid_json_returns_none(temp_db, config_manager):
    """Test that invalid JSON response returns None."""
    config_manager.set("llm_model", "gpt-4o-mini")
    config_manager.set("llm_api_key", "test-key")

    extractor = SongMetadataExtractor(config_manager, temp_db, make_invalid_json_completion())
    artist, song_name = extractor.extract("youtube:invalid", "Some Title")
    assert artist is None
    assert song_name is None


def test_extract_partial_response_returns_none(temp_db, config_manager):
    """Test that partial response (missing fields) returns None."""
    config_manager.set("llm_model", "gpt-4o-mini")
    config_manager.set("llm_api_key", "test-key")

    extractor = SongMetadataExtractor(config_manager, temp_db, make_partial_completion())
    artist, song_name = extractor.extract("youtube:partial", "Some Title")
    assert artist is None
    assert song_name is None


def test_extract_llm_error_returns_none(temp_db, config_manager):
    """Test that LLM errors are caught and return None."""
    config_manager.set("llm_model", "gpt-4o-mini")
    config_manager.set("llm_api_key", "test-key")

    extractor = SongMetadataExtractor(config_manager, temp_db, make_error_completion())
    artist, song_name = extractor.extract("youtube:error", "Some Title")
    assert artist is None
    assert song_name is None


def test_extract_markdown_wrapped_json(temp_db, config_manager):
    """Test that JSON wrapped in markdown code blocks is parsed correctly."""
    config_manager.set("llm_model", "gpt-4o-mini")
    config_manager.set("llm_api_key", "test-key")

    extractor = SongMetadataExtractor(
        config_manager, temp_db, make_markdown_completion("The Beatles", "Yesterday")
    )
    artist, song_name = extractor.extract("youtube:beatles", "Yesterday - Karaoke")
    assert artist == "The Beatles"
    assert song_name == "Yesterday"


# =============================================================================
# Test _build_prompt()
# =============================================================================


def test_build_prompt_includes_title(configured_extractor):
    """Test that prompt includes the video title."""
    prompt = configured_extractor._build_prompt(
        title="Journey - Don't Stop Believin'",
        description=None,
        channel=None,
    )
    assert "Journey - Don't Stop Believin'" in prompt


def test_build_prompt_includes_channel(configured_extractor):
    """Test that prompt includes the channel name."""
    prompt = configured_extractor._build_prompt(
        title="Some Song",
        description=None,
        channel="Zoom Karaoke",
    )
    assert "Zoom Karaoke" in prompt


def test_build_prompt_includes_description(configured_extractor):
    """Test that prompt includes the description."""
    prompt = configured_extractor._build_prompt(
        title="Some Song",
        description="Originally by Artist Name",
        channel=None,
    )
    assert "Originally by Artist Name" in prompt


def test_build_prompt_truncates_long_description(configured_extractor):
    """Test that very long descriptions are truncated."""
    long_description = "A" * 500
    prompt = configured_extractor._build_prompt(
        title="Some Song",
        description=long_description,
        channel=None,
    )
    # Should be truncated to 300 chars
    assert "A" * 300 in prompt
    assert "A" * 301 not in prompt


def test_build_prompt_warns_about_karaoke_channels(configured_extractor):
    """Test that prompt includes guidance about karaoke channels."""
    prompt = configured_extractor._build_prompt(
        title="Some Song",
        description=None,
        channel="SingKing",
    )
    assert "karaoke" in prompt.lower()
    assert "NOT the artist" in prompt


# =============================================================================
# Test _parse_llm_response()
# =============================================================================


def test_parse_llm_response_valid_json(configured_extractor):
    """Test parsing valid JSON response."""
    content = '{"artist": "Queen", "song_name": "We Will Rock You"}'
    artist, song_name = configured_extractor._parse_llm_response(content)
    assert artist == "Queen"
    assert song_name == "We Will Rock You"


def test_parse_llm_response_with_whitespace(configured_extractor):
    """Test parsing JSON with surrounding whitespace."""
    content = '  \n{"artist": "Queen", "song_name": "We Will Rock You"}\n  '
    artist, song_name = configured_extractor._parse_llm_response(content)
    assert artist == "Queen"
    assert song_name == "We Will Rock You"


def test_parse_llm_response_markdown_json_block(configured_extractor):
    """Test parsing JSON wrapped in ```json code block."""
    content = '```json\n{"artist": "Queen", "song_name": "We Will Rock You"}\n```'
    artist, song_name = configured_extractor._parse_llm_response(content)
    assert artist == "Queen"
    assert song_name == "We Will Rock You"


def test_parse_llm_response_markdown_plain_block(configured_extractor):
    """Test parsing JSON wrapped in plain ``` code block."""
    content = '```\n{"artist": "Queen", "song_name": "We Will Rock You"}\n```'
    artist, song_name = configured_extractor._parse_llm_response(content)
    assert artist == "Queen"
    assert song_name == "We Will Rock You"


def test_parse_llm_response_invalid_json(configured_extractor):
    """Test parsing invalid JSON returns None."""
    content = "This is not valid JSON"
    artist, song_name = configured_extractor._parse_llm_response(content)
    assert artist is None
    assert song_name is None


def test_parse_llm_response_missing_artist(configured_extractor):
    """Test parsing JSON missing artist returns None."""
    content = '{"song_name": "We Will Rock You"}'
    artist, song_name = configured_extractor._parse_llm_response(content)
    assert artist is None
    assert song_name is None


def test_parse_llm_response_missing_song_name(configured_extractor):
    """Test parsing JSON missing song_name returns None."""
    content = '{"artist": "Queen"}'
    artist, song_name = configured_extractor._parse_llm_response(content)
    assert artist is None
    assert song_name is None


def test_parse_llm_response_strips_values(configured_extractor):
    """Test that artist and song_name values are stripped."""
    content = '{"artist": "  Queen  ", "song_name": "  We Will Rock You  "}'
    artist, song_name = configured_extractor._parse_llm_response(content)
    assert artist == "Queen"
    assert song_name == "We Will Rock You"
