"""
Unit tests for SuggestionEngine.

Tests the suggestion engine logic without making actual LLM calls.
Uses dependency injection for the LLM client.
"""

import json
import logging
import os
import tempfile
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.history import HistoryManager
from kbox.llm import LLMClient
from kbox.models import SongMetadata, SongSettings
from kbox.queue import QueueManager
from kbox.suggestions import SuggestionEngine, SuggestionError
from kbox.user import UserManager

# =============================================================================
# Fake LLM completion functions for testing
# =============================================================================


def make_fake_completion(suggestions: List[Dict[str, str]]):
    """
    Create a fake completion function that returns the given suggestions.

    Args:
        suggestions: List of {"title": ..., "artist": ...} dicts to return
    """

    def fake_completion(**kwargs) -> Any:
        """Fake LLM completion that returns predictable responses."""
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps(suggestions)
        response.choices[0].finish_reason = "stop"
        return response

    return fake_completion


def make_empty_completion():
    """Create a fake completion that returns empty content."""

    def fake_completion(**kwargs) -> Any:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = ""
        response.choices[0].finish_reason = "stop"
        return response

    return fake_completion


def make_truncated_completion():
    """Create a fake completion that simulates token limit exceeded."""

    def fake_completion(**kwargs) -> Any:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = ""
        response.choices[0].finish_reason = "length"
        return response

    return fake_completion


def make_error_completion(error_message: str = "API error"):
    """Create a fake completion that raises an exception."""

    def fake_completion(**kwargs) -> Any:
        raise Exception(error_message)

    return fake_completion


def make_llm_client(
    config_manager: ConfigManager, completion_fn, supports_schema: bool = False
) -> LLMClient:
    """Create an LLMClient with a fake completion function for testing."""
    return LLMClient(
        config_manager,
        completion_fn=completion_fn,
        schema_support_fn=lambda model: supports_schema,
    )


# Two real replies from gpt-4o-mini at a party, each failing json.loads on a
# single slip in an otherwise good list.
CURLY_QUOTE_REPLY = """[
    {"title": "Mr. Brightside", "artist": "The Killers"},
    {"title": "King”, "artist": "Griffin"},
    {"title": "Dancing in the Dark", "artist": "Bruce Springsteen"}
]"""

TRAILING_COMMA_REPLY = """[
    {"title": "Mr. Brightside", "artist": "The Killers"},
    {"title": "King", "artist": "Griffin"},
    {"title": "Dancing in the Dark", "artist": "Bruce Springsteen"},
]"""


def make_scripted_completion(replies: List[str], calls: List[Dict[str, Any]]):
    """Fake completion returning each reply in turn, recording the kwargs of each call."""
    remaining = list(replies)

    def fake_completion(**kwargs) -> Any:
        calls.append(kwargs)
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = remaining.pop(0)
        response.choices[0].finish_reason = "stop"
        return response

    return fake_completion


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
def user_manager(temp_db):
    """Create a UserManager for testing."""
    return UserManager(temp_db)


@pytest.fixture
def history_manager(temp_db):
    """Create a HistoryManager for testing."""
    return HistoryManager(temp_db)


@pytest.fixture
def mock_video_library():
    """Create a mock VideoLibrary for testing."""
    mock = MagicMock()
    mock.request.return_value = None
    mock.get_path.return_value = None
    mock.is_available.return_value = False
    mock.manage_storage.return_value = 0
    # Default search returns empty
    mock.search.return_value = []
    return mock


@pytest.fixture
def queue_manager(temp_db, mock_video_library):
    """Create a QueueManager for testing."""
    qm = QueueManager(temp_db, video_library=mock_video_library)
    yield qm
    qm.stop_content_monitor()


@pytest.fixture
def test_user(user_manager):
    """Create a test user."""
    return user_manager.get_or_create_user("test-user-id", "Test User")


@pytest.fixture
def llm_client(config_manager):
    """Create an LLMClient for testing (without a completion function)."""
    return LLMClient(config_manager, schema_support_fn=lambda model: False)


@pytest.fixture
def suggestion_engine(
    config_manager, history_manager, queue_manager, mock_video_library, llm_client
):
    """Create a SuggestionEngine for testing."""
    return SuggestionEngine(
        config_manager=config_manager,
        history_manager=history_manager,
        queue_manager=queue_manager,
        video_library=mock_video_library,
        llm_client=llm_client,
    )


class TestIsConfigured:
    """Tests for is_configured() method."""

    def test_not_configured_without_model(self, suggestion_engine):
        """is_configured returns False when no model is set."""
        assert suggestion_engine.is_configured() is False

    def test_configured_with_model_and_api_key(self, suggestion_engine, config_manager):
        """is_configured returns True when model and API key are set."""
        config_manager.set("llm_model", "gpt-4o-mini")
        config_manager.set("llm_api_key", "sk-test-key")
        assert suggestion_engine.is_configured() is True

    def test_not_configured_without_api_key(self, suggestion_engine, config_manager):
        """is_configured returns False when model set but no API key (non-Ollama)."""
        config_manager.set("llm_model", "gpt-4o-mini")
        # No API key set
        assert suggestion_engine.is_configured() is False

    def test_ollama_configured_without_api_key(self, suggestion_engine, config_manager):
        """is_configured returns True for Ollama models without API key."""
        config_manager.set("llm_model", "ollama/llama3.2")
        # No API key needed for Ollama
        assert suggestion_engine.is_configured() is True


class TestBuildContext:
    """Tests for _build_context() method."""

    def test_empty_context_for_new_user(self, suggestion_engine):
        """New user with no history returns minimal context."""
        context = suggestion_engine._build_context("nonexistent-user")
        assert "user_history" not in context or context.get("user_history") == []

    def test_context_includes_user_history(self, suggestion_engine, history_manager, test_user):
        """Context includes user's song history."""
        # Record some performances
        history_manager.record_performance(
            user_id=test_user.id,
            user_name=test_user.display_name,
            video_id="youtube:vid1",
            metadata=SongMetadata(title="Bohemian Rhapsody", channel="Queen"),
            settings=SongSettings(pitch_semitones=0),
            played_duration_seconds=300,
            playback_end_position_seconds=300,
            completion_percentage=100.0,
        )
        history_manager.record_performance(
            user_id=test_user.id,
            user_name=test_user.display_name,
            video_id="youtube:vid2",
            metadata=SongMetadata(title="Don't Stop Believin'", channel="Journey"),
            settings=SongSettings(pitch_semitones=-2),
            played_duration_seconds=250,
            playback_end_position_seconds=250,
            completion_percentage=100.0,
        )

        context = suggestion_engine._build_context(test_user.id)

        assert "user_history" in context
        assert len(context["user_history"]) == 2
        # Most recent first
        assert context["user_history"][0]["title"] == "Don't Stop Believin'"
        assert context["user_history"][0]["artist"] == "Journey"
        assert context["user_history"][1]["title"] == "Bohemian Rhapsody"

    def test_context_deduplicates_history(self, suggestion_engine, history_manager, test_user):
        """Context deduplicates repeated songs in history."""
        # User sings the same song 3 times
        for _ in range(3):
            history_manager.record_performance(
                user_id=test_user.id,
                user_name=test_user.display_name,
                video_id="youtube:vid1",
                metadata=SongMetadata(title="Bohemian Rhapsody", channel="Queen"),
                settings=SongSettings(pitch_semitones=0),
                played_duration_seconds=300,
                playback_end_position_seconds=300,
                completion_percentage=100.0,
            )

        context = suggestion_engine._build_context(test_user.id)

        assert "user_history" in context
        # Should only appear once despite being sung 3 times
        assert len(context["user_history"]) == 1
        assert context["user_history"][0]["title"] == "Bohemian Rhapsody"

    def test_context_includes_queue(self, suggestion_engine, queue_manager, test_user):
        """Context includes current queue items."""
        queue_manager.add_song(
            user=test_user,
            video_id="youtube:queue1",
            title="Sweet Caroline",
            channel="Neil Diamond",
        )
        queue_manager.add_song(
            user=test_user,
            video_id="youtube:queue2",
            title="Living on a Prayer",
            channel="Bon Jovi",
        )

        context = suggestion_engine._build_context(test_user.id)

        assert "current_queue" in context
        assert len(context["current_queue"]) == 2

    def test_context_includes_theme(self, suggestion_engine, config_manager):
        """Context includes operator-configured theme."""
        config_manager.set("suggestion_theme", "80s rock anthems")

        context = suggestion_engine._build_context("any-user")

        assert context.get("theme") == "80s rock anthems"

    def test_context_excludes_empty_theme(self, suggestion_engine, config_manager):
        """Context does not include empty theme."""
        config_manager.set("suggestion_theme", "")

        context = suggestion_engine._build_context("any-user")

        assert "theme" not in context or context.get("theme") == ""

    def test_history_failure_warns_rather_than_degrading_quietly(
        self, suggestion_engine, monkeypatch, caplog
    ):
        """
        A failed history read warns and still returns usable context.

        These are local SQLite reads, so a failure is unexpected. It used to
        log at debug, which made a broken read indistinguishable from a user
        who has simply never sung before -- the model gets a prompt with no
        taste in it either way.
        """

        def boom(*args, **kwargs):
            raise RuntimeError("history table is locked")

        monkeypatch.setattr(suggestion_engine.history, "get_user_history", boom)

        with caplog.at_level(logging.WARNING, logger="kbox.suggestions"):
            context = suggestion_engine._build_context("any-user")

        assert "user_history" not in context
        assert "Could not read history" in caplog.text
        assert "history table is locked" in caplog.text

    def test_queue_failure_warns_rather_than_degrading_quietly(
        self, suggestion_engine, monkeypatch, caplog
    ):
        """A failed queue read warns and still returns usable context."""

        def boom(*args, **kwargs):
            raise RuntimeError("queue table is locked")

        monkeypatch.setattr(suggestion_engine.queue, "get_queue", boom)

        with caplog.at_level(logging.WARNING, logger="kbox.suggestions"):
            context = suggestion_engine._build_context("any-user")

        assert "current_queue" not in context
        assert "Could not read the current queue" in caplog.text
        assert "queue table is locked" in caplog.text


class TestParseLlmResponse:
    """Tests for _parse_llm_response() method."""

    def test_parse_valid_json_array(self, suggestion_engine):
        """Parses valid JSON array of songs."""
        content = (
            '[{"title": "Song 1", "artist": "Artist 1"}, {"title": "Song 2", "artist": "Artist 2"}]'
        )

        result = suggestion_engine._parse_llm_response(content)

        assert len(result) == 2
        assert result[0]["title"] == "Song 1"
        assert result[0]["artist"] == "Artist 1"
        assert result[1]["title"] == "Song 2"
        assert result[1]["artist"] == "Artist 2"

    def test_parse_json_with_markdown_code_block(self, suggestion_engine):
        """Parses JSON wrapped in markdown code block."""
        content = """```json
[{"title": "Song 1", "artist": "Artist 1"}]
```"""

        result = suggestion_engine._parse_llm_response(content)

        assert len(result) == 1
        assert result[0]["title"] == "Song 1"

    def test_parse_json_with_plain_code_block(self, suggestion_engine):
        """Parses JSON wrapped in plain code block."""
        content = """```
[{"title": "Song 1", "artist": "Artist 1"}]
```"""

        result = suggestion_engine._parse_llm_response(content)

        assert len(result) == 1

    def test_parse_empty_content(self, suggestion_engine):
        """Returns empty list for empty content."""
        result = suggestion_engine._parse_llm_response("")
        assert result == []

    def test_parse_invalid_json(self, suggestion_engine):
        """Returns empty list for invalid JSON."""
        result = suggestion_engine._parse_llm_response("not valid json")
        assert result == []

    def test_parse_json_object_instead_of_array(self, suggestion_engine):
        """Returns empty list when JSON is object instead of array."""
        content = '{"title": "Song 1", "artist": "Artist 1"}'

        result = suggestion_engine._parse_llm_response(content)

        assert result == []

    def test_parse_filters_invalid_items(self, suggestion_engine):
        """Filters out items missing required fields."""
        content = """[
            {"title": "Valid Song", "artist": "Valid Artist"},
            {"title": "Missing Artist"},
            {"artist": "Missing Title"},
            {"other": "field"}
        ]"""

        result = suggestion_engine._parse_llm_response(content)

        assert len(result) == 1
        assert result[0]["title"] == "Valid Song"

    def test_parse_handles_extra_whitespace(self, suggestion_engine):
        """Handles JSON with extra whitespace."""
        content = """

        [{"title": "Song 1", "artist": "Artist 1"}]

        """

        result = suggestion_engine._parse_llm_response(content)

        assert len(result) == 1

    def test_parse_songs_object(self, suggestion_engine):
        """Accepts the {"songs": [...]} object the prompt and schema ask for."""
        content = '{"songs": [{"title": "Song 1", "artist": "Artist 1"}]}'

        result = suggestion_engine._parse_llm_response(content)

        assert result == [{"title": "Song 1", "artist": "Artist 1"}]

    def test_parse_curly_closing_quote(self, suggestion_engine):
        """A curly quote used as a string delimiter is straightened, keeping every song."""
        result = suggestion_engine._parse_llm_response(CURLY_QUOTE_REPLY)

        assert result == [
            {"title": "Mr. Brightside", "artist": "The Killers"},
            {"title": "King", "artist": "Griffin"},
            {"title": "Dancing in the Dark", "artist": "Bruce Springsteen"},
        ]

    def test_parse_trailing_comma(self, suggestion_engine):
        """A trailing comma before the closing bracket is dropped."""
        result = suggestion_engine._parse_llm_response(TRAILING_COMMA_REPLY)

        assert len(result) == 3
        assert result[2] == {"title": "Dancing in the Dark", "artist": "Bruce Springsteen"}

    def test_parse_leaves_curly_quotes_inside_titles(self, suggestion_engine):
        """Curly quotes inside a title survive the repair."""
        content = '[{"title": "Say “Hello” Again", "artist": "A"},]'

        result = suggestion_engine._parse_llm_response(content)

        assert result == [{"title": "Say “Hello” Again", "artist": "A"}]

    def test_parse_keeps_entries_that_parse(self, suggestion_engine):
        """One unrepairable entry does not cost the rest of the list."""
        content = """[
            {"title": "Good One", "artist": "A"},
            {"title": "Broken, "artist": "B"},
            {"title": "Good Two", "artist": "C"}
        ]"""

        result = suggestion_engine._parse_llm_response(content)

        assert result == [
            {"title": "Good One", "artist": "A"},
            {"title": "Good Two", "artist": "C"},
        ]


class TestSearchSuggestions:
    """Tests for _search_suggestions() method."""

    def test_search_returns_youtube_results(self, suggestion_engine, mock_video_library):
        """Searches YouTube for each suggestion and returns results."""
        mock_video_library.search.return_value = [
            {"id": "youtube:result1", "title": "Song 1 Karaoke", "thumbnail": "..."},
        ]

        suggestions = [
            {"title": "Song 1", "artist": "Artist 1"},
            {"title": "Song 2", "artist": "Artist 2"},
        ]

        results = suggestion_engine._search_suggestions(suggestions, max_results=8)

        # Should have called search for each suggestion
        assert mock_video_library.search.call_count == 2

    def test_search_deduplicates_results(self, suggestion_engine, mock_video_library):
        """Deduplicates results when same video appears multiple times."""
        # Same video returned for both searches
        mock_video_library.search.return_value = [
            {"id": "youtube:same-video", "title": "Same Video", "thumbnail": "..."},
        ]

        suggestions = [
            {"title": "Song 1", "artist": "Artist 1"},
            {"title": "Song 2", "artist": "Artist 2"},
        ]

        results = suggestion_engine._search_suggestions(suggestions, max_results=8)

        # Should only include the video once
        assert len(results) == 1
        assert results[0]["id"] == "youtube:same-video"

    def test_search_respects_max_results(self, suggestion_engine, mock_video_library):
        """Stops searching once max_results is reached."""
        call_count = 0

        def mock_search(query, max_results=2):
            nonlocal call_count
            call_count += 1
            return [{"id": f"youtube:vid{call_count}", "title": f"Video {call_count}"}]

        mock_video_library.search.side_effect = mock_search

        suggestions = [{"title": f"Song {i}", "artist": f"Artist {i}"} for i in range(10)]

        results = suggestion_engine._search_suggestions(suggestions, max_results=3)

        assert len(results) == 3
        # Should stop after finding 3 results
        assert call_count == 3

    def test_search_failure_warns(self, suggestion_engine, mock_video_library, caplog):
        """A failed lookup warns and names the query that failed."""
        mock_video_library.search.side_effect = [
            RuntimeError("quota exceeded"),
            [{"id": "youtube:ok", "title": "Song 2 Karaoke"}],
        ]

        suggestions = [
            {"title": "Song 1", "artist": "Artist 1"},
            {"title": "Song 2", "artist": "Artist 2"},
        ]

        with caplog.at_level(logging.WARNING, logger="kbox.suggestions"):
            results = suggestion_engine._search_suggestions(suggestions, max_results=8)

        assert [video["id"] for video in results] == ["youtube:ok"]
        assert "Song 1 Artist 1" in caplog.text
        assert "quota exceeded" in caplog.text
        # One failure out of two is not a broken backend.
        assert "Every suggestion lookup failed" not in caplog.text

    def test_every_search_failing_is_reported_as_a_backend_problem(
        self, suggestion_engine, mock_video_library, caplog
    ):
        """
        All lookups failing logs an error naming the backend.

        get_suggestions() turns an empty result into "Could not find karaoke
        videos for the suggested songs", which blames the model. When every
        lookup raised, that message is wrong and only the log can say so.
        """
        mock_video_library.search.side_effect = RuntimeError("quota exceeded")

        suggestions = [
            {"title": "Song 1", "artist": "Artist 1"},
            {"title": "Song 2", "artist": "Artist 2"},
        ]

        with caplog.at_level(logging.WARNING, logger="kbox.suggestions"):
            results = suggestion_engine._search_suggestions(suggestions, max_results=8)

        assert results == []
        assert "Every suggestion lookup failed (2 of 2)" in caplog.text
        assert "search backend is the likely cause" in caplog.text


class TestGetSuggestions:
    """Tests for get_suggestions() method."""

    def test_raises_error_when_not_configured(self, suggestion_engine):
        """Raises SuggestionError when AI not configured."""
        with pytest.raises(SuggestionError) as exc_info:
            suggestion_engine.get_suggestions("user-id")

        assert "not configured" in str(exc_info.value)


class TestBuildPrompt:
    """Tests for _build_prompt() method."""

    def test_prompt_includes_user_history(self, suggestion_engine):
        """Prompt includes user history when available."""
        context = {
            "user_history": [
                {"title": "Bohemian Rhapsody", "artist": "Queen"},
                {"title": "Don't Stop Believin'", "artist": "Journey"},
            ]
        }

        prompt = suggestion_engine._build_prompt(context, count=8)

        assert "Bohemian Rhapsody" in prompt
        assert "Queen" in prompt
        assert "Don't Stop Believin'" in prompt
        assert "Journey" in prompt
        assert "vocal range" in prompt.lower()

    def test_prompt_includes_queue(self, suggestion_engine):
        """Prompt includes current queue when available."""
        context = {
            "current_queue": [
                {"title": "Sweet Caroline", "artist": "Neil Diamond", "user": "Alice"},
            ]
        }

        prompt = suggestion_engine._build_prompt(context, count=8)

        assert "Sweet Caroline" in prompt
        assert "Neil Diamond" in prompt

    def test_prompt_includes_theme(self, suggestion_engine):
        """Prompt includes theme when available."""
        context = {"theme": "80s rock anthems"}

        prompt = suggestion_engine._build_prompt(context, count=8)

        assert "80s rock anthems" in prompt

    def test_prompt_requests_json_format(self, suggestion_engine):
        """Prompt requests JSON array format."""
        context = {}

        prompt = suggestion_engine._build_prompt(context, count=8)

        assert "JSON" in prompt
        assert "title" in prompt
        assert "artist" in prompt

    def test_prompt_avoids_cliches(self, suggestion_engine):
        """Prompt instructs to avoid overplayed songs."""
        context = {}

        prompt = suggestion_engine._build_prompt(context, count=8)

        assert "cliché" in prompt.lower() or "overplayed" in prompt.lower()

    def test_prompt_requests_correct_count(self, suggestion_engine):
        """Prompt requests the specified number of songs."""
        context = {}

        prompt = suggestion_engine._build_prompt(context, count=5)

        assert "5" in prompt


# =============================================================================
# Integration tests using injected fake LLM
# =============================================================================


class TestGenerateSuggestionsIntegration:
    """Integration tests for _generate_suggestions using fake LLM."""

    def test_generate_returns_parsed_suggestions(
        self, config_manager, history_manager, queue_manager, mock_video_library
    ):
        """_generate_suggestions returns parsed suggestions from LLM."""
        fake_suggestions = [
            {"title": "Take On Me", "artist": "a-ha"},
            {"title": "Sweet Dreams", "artist": "Eurythmics"},
        ]

        llm_client = make_llm_client(config_manager, make_fake_completion(fake_suggestions))
        engine = SuggestionEngine(
            config_manager=config_manager,
            history_manager=history_manager,
            queue_manager=queue_manager,
            video_library=mock_video_library,
            llm_client=llm_client,
        )
        config_manager.set("llm_model", "gpt-4o-mini")
        config_manager.set("llm_api_key", "test-key")

        result = engine._generate_suggestions({}, count=2)

        assert len(result) == 2
        assert result[0]["title"] == "Take On Me"
        assert result[0]["artist"] == "a-ha"
        assert result[1]["title"] == "Sweet Dreams"
        assert result[1]["artist"] == "Eurythmics"

    def test_generate_handles_empty_response(
        self, config_manager, history_manager, queue_manager, mock_video_library
    ):
        """_generate_suggestions raises error on empty LLM response."""
        llm_client = make_llm_client(config_manager, make_empty_completion())
        engine = SuggestionEngine(
            config_manager=config_manager,
            history_manager=history_manager,
            queue_manager=queue_manager,
            video_library=mock_video_library,
            llm_client=llm_client,
        )
        config_manager.set("llm_model", "gpt-4o-mini")
        config_manager.set("llm_api_key", "test-key")

        with pytest.raises(SuggestionError) as exc_info:
            engine._generate_suggestions({}, count=5)

        assert "empty response" in str(exc_info.value).lower()

    def test_generate_handles_token_limit(
        self, config_manager, history_manager, queue_manager, mock_video_library
    ):
        """_generate_suggestions raises error when response truncated."""
        llm_client = make_llm_client(config_manager, make_truncated_completion())
        engine = SuggestionEngine(
            config_manager=config_manager,
            history_manager=history_manager,
            queue_manager=queue_manager,
            video_library=mock_video_library,
            llm_client=llm_client,
        )
        config_manager.set("llm_model", "gpt-4o-mini")
        config_manager.set("llm_api_key", "test-key")

        with pytest.raises(SuggestionError) as exc_info:
            engine._generate_suggestions({}, count=5)

        assert "token limit" in str(exc_info.value).lower()

    def test_generate_handles_api_error(
        self, config_manager, history_manager, queue_manager, mock_video_library
    ):
        """_generate_suggestions raises SuggestionError on API failure."""
        llm_client = make_llm_client(config_manager, make_error_completion("Connection refused"))
        engine = SuggestionEngine(
            config_manager=config_manager,
            history_manager=history_manager,
            queue_manager=queue_manager,
            video_library=mock_video_library,
            llm_client=llm_client,
        )
        config_manager.set("llm_model", "gpt-4o-mini")
        config_manager.set("llm_api_key", "test-key")

        with pytest.raises(SuggestionError) as exc_info:
            engine._generate_suggestions({}, count=5)

        assert "Connection refused" in str(exc_info.value)


class TestMalformedReplies:
    """The model's reply is not always valid JSON; the guest should still get songs."""

    @pytest.fixture
    def configured(self, config_manager):
        config_manager.set("llm_model", "gpt-4o-mini")
        config_manager.set("llm_api_key", "test-key")
        return config_manager

    def make_engine(self, config_manager, history_manager, queue_manager, video_library, client):
        return SuggestionEngine(
            config_manager=config_manager,
            history_manager=history_manager,
            queue_manager=queue_manager,
            video_library=video_library,
            llm_client=client,
        )

    @pytest.mark.parametrize("reply", [CURLY_QUOTE_REPLY, TRAILING_COMMA_REPLY])
    def test_party_replies_produce_suggestions(
        self, configured, history_manager, queue_manager, mock_video_library, reply
    ):
        """Both replies that 503'd at a party now give a full list, first time."""
        calls: List[Dict[str, Any]] = []
        client = make_llm_client(configured, make_scripted_completion([reply], calls))
        engine = self.make_engine(
            configured, history_manager, queue_manager, mock_video_library, client
        )

        result = engine._generate_suggestions({}, count=3)

        assert [s["title"] for s in result] == ["Mr. Brightside", "King", "Dancing in the Dark"]
        assert len(calls) == 1

    def test_unparseable_reply_is_retried_once(
        self, configured, history_manager, queue_manager, mock_video_library
    ):
        calls: List[Dict[str, Any]] = []
        replies = ["Sorry, I can't help with that.", '[{"title": "T", "artist": "A"}]']
        client = make_llm_client(configured, make_scripted_completion(replies, calls))
        engine = self.make_engine(
            configured, history_manager, queue_manager, mock_video_library, client
        )

        result = engine._generate_suggestions({}, count=1)

        assert result == [{"title": "T", "artist": "A"}]
        assert len(calls) == 2

    def test_gives_a_friendly_message_after_retry_fails(
        self, configured, history_manager, queue_manager, mock_video_library
    ):
        calls: List[Dict[str, Any]] = []
        replies = ["not json", "still not json"]
        client = make_llm_client(configured, make_scripted_completion(replies, calls))
        engine = self.make_engine(
            configured, history_manager, queue_manager, mock_video_library, client
        )

        with pytest.raises(SuggestionError) as exc_info:
            engine._generate_suggestions({}, count=1)

        assert len(calls) == 2
        assert "try again" in str(exc_info.value).lower()
        assert "temperature" not in str(exc_info.value).lower()

    def test_requests_schema_when_model_supports_it(
        self, configured, history_manager, queue_manager, mock_video_library
    ):
        calls: List[Dict[str, Any]] = []
        reply = '{"songs": [{"title": "T", "artist": "A"}]}'
        client = make_llm_client(
            configured, make_scripted_completion([reply], calls), supports_schema=True
        )
        engine = self.make_engine(
            configured, history_manager, queue_manager, mock_video_library, client
        )

        result = engine._generate_suggestions({}, count=1)

        assert result == [{"title": "T", "artist": "A"}]
        response_format = calls[0]["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["schema"]["required"] == ["songs"]

    def test_no_response_format_when_model_lacks_schema_support(
        self, configured, history_manager, queue_manager, mock_video_library
    ):
        calls: List[Dict[str, Any]] = []
        reply = '{"songs": [{"title": "T", "artist": "A"}]}'
        client = make_llm_client(configured, make_scripted_completion([reply], calls))
        engine = self.make_engine(
            configured, history_manager, queue_manager, mock_video_library, client
        )

        engine._generate_suggestions({}, count=1)

        assert "response_format" not in calls[0]


class TestGetSuggestionsIntegration:
    """Integration tests for full get_suggestions flow."""

    def test_full_suggestion_flow(
        self, config_manager, history_manager, queue_manager, mock_video_library
    ):
        """Test complete flow from LLM response to YouTube search results."""
        fake_suggestions = [
            {"title": "Take On Me", "artist": "a-ha"},
            {"title": "Sweet Dreams", "artist": "Eurythmics"},
        ]

        llm_client = make_llm_client(config_manager, make_fake_completion(fake_suggestions))
        engine = SuggestionEngine(
            config_manager=config_manager,
            history_manager=history_manager,
            queue_manager=queue_manager,
            video_library=mock_video_library,
            llm_client=llm_client,
        )
        config_manager.set("llm_model", "gpt-4o-mini")
        config_manager.set("llm_api_key", "test-key")

        # Mock video library to return search results
        mock_video_library.search.side_effect = lambda query, max_results=2: [
            {
                "id": f"youtube:{hash(query) % 10000}",
                "title": f"{query} (Karaoke)",
                "channel": "Karaoke Channel",
                "thumbnail": "https://example.com/thumb.jpg",
            }
        ]

        results = engine.get_suggestions("user-123", max_results=5)

        assert len(results) == 2
        assert "Take On Me" in results[0]["title"]
        assert mock_video_library.search.call_count == 2

    def test_suggestion_flow_with_user_history(
        self,
        config_manager,
        history_manager,
        queue_manager,
        mock_video_library,
        test_user,
    ):
        """Test that user history is passed to LLM via prompt."""
        # Record some history
        history_manager.record_performance(
            user_id=test_user.id,
            user_name=test_user.display_name,
            video_id="youtube:vid1",
            metadata=SongMetadata(title="Bohemian Rhapsody", channel="Queen"),
            settings=SongSettings(pitch_semitones=0),
            played_duration_seconds=300,
            playback_end_position_seconds=300,
            completion_percentage=100.0,
        )

        captured_kwargs = {}

        def capturing_completion(**kwargs):
            captured_kwargs.update(kwargs)
            response = MagicMock()
            response.choices = [MagicMock()]
            response.choices[0].message.content = json.dumps([{"title": "Test", "artist": "Test"}])
            response.choices[0].finish_reason = "stop"
            return response

        llm_client = make_llm_client(config_manager, capturing_completion)
        engine = SuggestionEngine(
            config_manager=config_manager,
            history_manager=history_manager,
            queue_manager=queue_manager,
            video_library=mock_video_library,
            llm_client=llm_client,
        )
        config_manager.set("llm_model", "gpt-4o-mini")
        config_manager.set("llm_api_key", "test-key")

        mock_video_library.search.return_value = [
            {"id": "youtube:test", "title": "Test", "channel": "Test"}
        ]

        engine.get_suggestions(test_user.id, max_results=5)

        # Verify user history was included in the prompt
        user_message = captured_kwargs["messages"][1]["content"]
        assert "Bohemian Rhapsody" in user_message
        assert "Queen" in user_message

    def test_raises_error_when_no_youtube_results(
        self, config_manager, history_manager, queue_manager, mock_video_library
    ):
        """Raises SuggestionError when YouTube search returns no results."""
        llm_client = make_llm_client(
            config_manager,
            make_fake_completion([{"title": "Obscure Song", "artist": "Unknown Band"}]),
        )
        engine = SuggestionEngine(
            config_manager=config_manager,
            history_manager=history_manager,
            queue_manager=queue_manager,
            video_library=mock_video_library,
            llm_client=llm_client,
        )
        config_manager.set("llm_model", "gpt-4o-mini")
        config_manager.set("llm_api_key", "test-key")

        # Video library returns empty results
        mock_video_library.search.return_value = []

        with pytest.raises(SuggestionError) as exc_info:
            engine.get_suggestions("user-123", max_results=5)

        assert "could not find" in str(exc_info.value).lower()

    def test_completion_receives_correct_model_config(
        self, config_manager, history_manager, queue_manager, mock_video_library
    ):
        """Verifies completion function receives correct config parameters."""
        captured_kwargs = {}

        def capturing_completion(**kwargs):
            captured_kwargs.update(kwargs)
            response = MagicMock()
            response.choices = [MagicMock()]
            response.choices[0].message.content = json.dumps([{"title": "Test", "artist": "Test"}])
            response.choices[0].finish_reason = "stop"
            return response

        llm_client = make_llm_client(config_manager, capturing_completion)
        engine = SuggestionEngine(
            config_manager=config_manager,
            history_manager=history_manager,
            queue_manager=queue_manager,
            video_library=mock_video_library,
            llm_client=llm_client,
        )
        config_manager.set("llm_model", "claude-3-haiku")
        config_manager.set("llm_api_key", "test-key")
        config_manager.set("llm_temperature", "0.7")

        mock_video_library.search.return_value = [
            {"id": "youtube:test", "title": "Test", "channel": "Test"}
        ]

        engine.get_suggestions("user-123", max_results=5)

        assert captured_kwargs["model"] == "claude-3-haiku"
        assert captured_kwargs["temperature"] == 0.7
        assert "messages" in captured_kwargs
        assert len(captured_kwargs["messages"]) == 2  # system + user
