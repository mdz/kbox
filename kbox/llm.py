"""
LLM client for kbox.

Provides a unified interface for LLM completions using LiteLLM,
handling API key configuration and provider-specific setup.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from .config_manager import ConfigManager

# Type alias for LLM completion functions (litellm.completion signature)
CompletionFn = Callable[..., Any]


class LLMClient:
    """Client for LLM completions using LiteLLM."""

    def __init__(
        self,
        config_manager: "ConfigManager",
        completion_fn: Optional[CompletionFn] = None,
    ):
        """
        Initialize LLMClient.

        Args:
            config_manager: For accessing LLM configuration (model, api_key, base_url)
            completion_fn: LLM completion function (defaults to litellm.completion)
        """
        self.config = config_manager
        self.logger = logging.getLogger(__name__)
        self._completion_fn = completion_fn

    def is_configured(self) -> bool:
        """Check if LLM is properly configured."""
        model = self.config.get("llm_model")
        if not model:
            return False

        # For non-Ollama models, we need an API key
        if not model.startswith("ollama/"):
            api_key = self.config.get("llm_api_key")
            if not api_key:
                return False

        return True

    def completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        **kwargs: Any,
    ) -> Any:
        """
        Call the LLM for a completion.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            temperature: Sampling temperature (0.0 = deterministic, higher = more random)
            max_tokens: Maximum tokens in the response
            **kwargs: Additional arguments passed to litellm.completion
                      (e.g., reasoning_effort for reasoning models)

        Returns:
            LiteLLM completion response object

        Raises:
            Exception: If LLM call fails

        Note on "Pydantic serializer warnings: ... Expected `Message`" / "Expected
        `StreamingChoices`" logged around every call: this is emitted by LiteLLM's own
        background success-handler thread (litellm.utils.py, "Wrapper: Completed Call,
        calling success_handler"), not by our code. LiteLLM's internal logging path
        re-validates the response against a `Union[Choices, StreamingChoices]` /
        `Message` schema that's shaped for streaming responses, so a normal
        non-streaming `ModelResponse` trips a serializer mismatch there even though it
        round-trips fine. The actual response object returned to callers here (and used
        via `response.choices[0].message.content` in song_metadata.py) is unaffected —
        confirmed by successful extraction on the same call. Known upstream LiteLLM
        quirk (seen on 1.80.15); do not treat this warning as evidence of an app bug.
        """
        # Get LLM config
        model = self.config.get("llm_model")
        api_key = self.config.get("llm_api_key")
        base_url = self.config.get("llm_base_url")

        # Get completion function (use litellm if not injected)
        completion_fn = self._completion_fn
        if completion_fn is None:
            import litellm

            # Configure litellm API keys based on model prefix
            if api_key:
                if model.startswith("claude") or model.startswith("anthropic"):
                    litellm.anthropic_key = api_key
                elif model.startswith("gemini"):
                    litellm.gemini_key = api_key
                else:
                    litellm.openai_key = api_key

            # Drop unsupported params (e.g., reasoning_effort for non-reasoning models)
            litellm.drop_params = True
            completion_fn = litellm.completion

        # Build completion kwargs
        completion_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }

        if base_url:
            completion_kwargs["api_base"] = base_url

        self.logger.debug("Calling LLM: model=%s, temperature=%s", model, temperature)

        return completion_fn(**completion_kwargs)
