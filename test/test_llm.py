"""
Tests for the LLM client module.

Covers configuration checks, completion kwarg passthrough,
and litellm API key routing for different providers.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from kbox.llm import LLMClient


@pytest.fixture
def mock_config():
    """Create a mock ConfigManager with sensible defaults."""
    config = Mock()
    config.get = Mock(return_value=None)
    return config


def _config_with(**values):
    """Build a mock ConfigManager that returns specific keys."""
    config = Mock()

    def get_side_effect(key, default=None):
        return values.get(key, default)

    config.get = Mock(side_effect=get_side_effect)
    return config


# ============================================================================
# is_configured Tests
# ============================================================================


class TestIsConfigured:
    def test_model_and_api_key(self):
        config = _config_with(llm_model="gpt-4o-mini", llm_api_key="sk-abc123")
        client = LLMClient(config)
        assert client.is_configured() is True

    def test_ollama_model_no_key_needed(self):
        config = _config_with(llm_model="ollama/llama3.2")
        client = LLMClient(config)
        assert client.is_configured() is True

    def test_no_model_returns_false(self):
        config = _config_with()
        client = LLMClient(config)
        assert client.is_configured() is False

    def test_empty_model_returns_false(self):
        config = _config_with(llm_model="")
        client = LLMClient(config)
        assert client.is_configured() is False

    def test_non_ollama_model_without_key_returns_false(self):
        config = _config_with(llm_model="gpt-4o-mini")
        client = LLMClient(config)
        assert client.is_configured() is False

    def test_non_ollama_model_with_empty_key_returns_false(self):
        config = _config_with(llm_model="gpt-4o-mini", llm_api_key="")
        client = LLMClient(config)
        assert client.is_configured() is False

    def test_anthropic_model_with_key(self):
        config = _config_with(llm_model="claude-3-haiku-20240307", llm_api_key="sk-ant-xxx")
        client = LLMClient(config)
        assert client.is_configured() is True

    def test_gemini_model_with_key(self):
        config = _config_with(llm_model="gemini/gemini-1.5-flash", llm_api_key="AIza...")
        client = LLMClient(config)
        assert client.is_configured() is True


# ============================================================================
# completion() with Injected Function Tests
# ============================================================================


class TestCompletionInjected:
    def test_passes_basic_kwargs(self):
        fake_fn = Mock(return_value="response")
        config = _config_with(llm_model="gpt-4o-mini", llm_api_key="sk-123")
        client = LLMClient(config, completion_fn=fake_fn)

        messages = [{"role": "user", "content": "Hello"}]
        result = client.completion(messages, temperature=0.7, max_tokens=100)

        assert result == "response"
        fake_fn.assert_called_once()
        call_kwargs = fake_fn.call_args[1]
        assert call_kwargs["model"] == "gpt-4o-mini"
        assert call_kwargs["messages"] == messages
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_tokens"] == 100

    def test_includes_api_base_when_base_url_set(self):
        fake_fn = Mock(return_value="response")
        config = _config_with(
            llm_model="ollama/llama3.2",
            llm_base_url="http://localhost:11434",
        )
        client = LLMClient(config, completion_fn=fake_fn)

        client.completion([{"role": "user", "content": "Hi"}], temperature=0.5, max_tokens=50)

        call_kwargs = fake_fn.call_args[1]
        assert call_kwargs["api_base"] == "http://localhost:11434"

    def test_no_api_base_when_base_url_empty(self):
        fake_fn = Mock(return_value="response")
        config = _config_with(llm_model="gpt-4o-mini", llm_api_key="sk-123", llm_base_url="")
        client = LLMClient(config, completion_fn=fake_fn)

        client.completion([{"role": "user", "content": "Hi"}], temperature=0.5, max_tokens=50)

        call_kwargs = fake_fn.call_args[1]
        assert "api_base" not in call_kwargs

    def test_no_api_base_when_base_url_none(self):
        fake_fn = Mock(return_value="response")
        config = _config_with(llm_model="gpt-4o-mini", llm_api_key="sk-123")
        client = LLMClient(config, completion_fn=fake_fn)

        client.completion([{"role": "user", "content": "Hi"}], temperature=0.5, max_tokens=50)

        call_kwargs = fake_fn.call_args[1]
        assert "api_base" not in call_kwargs

    def test_extra_kwargs_passed_through(self):
        fake_fn = Mock(return_value="response")
        config = _config_with(llm_model="o1-mini", llm_api_key="sk-123")
        client = LLMClient(config, completion_fn=fake_fn)

        client.completion(
            [{"role": "user", "content": "Hi"}],
            temperature=0.0,
            max_tokens=200,
            reasoning_effort="medium",
        )

        call_kwargs = fake_fn.call_args[1]
        assert call_kwargs["reasoning_effort"] == "medium"

    def test_completion_propagates_exception(self):
        fake_fn = Mock(side_effect=RuntimeError("API down"))
        config = _config_with(llm_model="gpt-4o-mini", llm_api_key="sk-123")
        client = LLMClient(config, completion_fn=fake_fn)

        with pytest.raises(RuntimeError, match="API down"):
            client.completion([{"role": "user", "content": "Hi"}], temperature=0.5, max_tokens=50)


# ============================================================================
# completion() without Injected Function (litellm routing) Tests
# ============================================================================


class TestCompletionLitellmRouting:
    """Test that when no completion_fn is injected, litellm is imported and configured."""

    def test_openai_key_routing(self):
        config = _config_with(llm_model="gpt-4o-mini", llm_api_key="sk-openai-key")
        client = LLMClient(config)

        mock_litellm = MagicMock()
        mock_litellm.completion = Mock(return_value="ok")

        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            client.completion([{"role": "user", "content": "Hi"}], temperature=0.5, max_tokens=50)

            assert mock_litellm.openai_key == "sk-openai-key"
            assert mock_litellm.drop_params is True
            mock_litellm.completion.assert_called_once()

    def test_anthropic_key_routing_claude_prefix(self):
        config = _config_with(llm_model="claude-3-haiku-20240307", llm_api_key="sk-ant-key")
        client = LLMClient(config)

        mock_litellm = MagicMock()
        mock_litellm.completion = Mock(return_value="ok")

        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            client.completion([{"role": "user", "content": "Hi"}], temperature=0.5, max_tokens=50)

            assert mock_litellm.anthropic_key == "sk-ant-key"

    def test_anthropic_key_routing_anthropic_prefix(self):
        config = _config_with(llm_model="anthropic/claude-3-opus", llm_api_key="sk-ant-key")
        client = LLMClient(config)

        mock_litellm = MagicMock()
        mock_litellm.completion = Mock(return_value="ok")

        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            client.completion([{"role": "user", "content": "Hi"}], temperature=0.5, max_tokens=50)

            assert mock_litellm.anthropic_key == "sk-ant-key"

    def test_gemini_key_routing(self):
        config = _config_with(llm_model="gemini/gemini-1.5-flash", llm_api_key="AIza-gemini")
        client = LLMClient(config)

        mock_litellm = MagicMock()
        mock_litellm.completion = Mock(return_value="ok")

        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            client.completion([{"role": "user", "content": "Hi"}], temperature=0.5, max_tokens=50)

            assert mock_litellm.gemini_key == "AIza-gemini"

    def test_no_key_routing_when_no_api_key(self):
        config = _config_with(llm_model="ollama/llama3.2")
        client = LLMClient(config)

        mock_litellm = MagicMock()
        mock_litellm.completion = Mock(return_value="ok")
        # Reset the attribute trackers so we can check they weren't set
        del mock_litellm.openai_key
        del mock_litellm.anthropic_key
        del mock_litellm.gemini_key

        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            client.completion([{"role": "user", "content": "Hi"}], temperature=0.5, max_tokens=50)

            # Keys should NOT have been set (api_key is None)
            assert not hasattr(mock_litellm, "openai_key")
            assert not hasattr(mock_litellm, "anthropic_key")
            assert not hasattr(mock_litellm, "gemini_key")

    def test_litellm_completion_receives_correct_kwargs(self):
        config = _config_with(
            llm_model="gpt-4o-mini",
            llm_api_key="sk-123",
            llm_base_url="https://custom.api.com",
        )
        client = LLMClient(config)

        mock_litellm = MagicMock()
        mock_litellm.completion = Mock(return_value="ok")

        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            messages = [{"role": "user", "content": "test"}]
            client.completion(messages, temperature=0.3, max_tokens=150)

            call_kwargs = mock_litellm.completion.call_args[1]
            assert call_kwargs["model"] == "gpt-4o-mini"
            assert call_kwargs["messages"] == messages
            assert call_kwargs["temperature"] == 0.3
            assert call_kwargs["max_tokens"] == 150
            assert call_kwargs["api_base"] == "https://custom.api.com"
