"""
AI-powered song suggestion engine for kbox.

Uses LiteLLM to generate personalized karaoke song recommendations based on
user history, current queue, and operator-configured theme.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from .config_manager import ConfigManager
    from .history import HistoryManager
    from .queue import QueueManager
    from .video_library import VideoLibrary

# Type alias for LLM completion functions (litellm.completion signature)
CompletionFn = Callable[..., Any]


class SuggestionError(Exception):
    """Raised when song suggestions cannot be generated."""

    pass


class SuggestionEngine:
    """Generates AI-powered song suggestions for karaoke guests."""

    def __init__(
        self,
        config_manager: "ConfigManager",
        history_manager: "HistoryManager",
        queue_manager: "QueueManager",
        video_library: "VideoLibrary",
        completion_fn: Optional[CompletionFn] = None,
    ):
        """
        Initialize SuggestionEngine.

        Args:
            config_manager: For accessing LLM and theme configuration
            history_manager: For retrieving user's song history
            queue_manager: For getting current queue context
            video_library: For searching songs on YouTube
            completion_fn: LLM completion function (defaults to litellm.completion)
        """
        self.config = config_manager
        self.history = history_manager
        self.queue = queue_manager
        self.video_library = video_library
        self.logger = logging.getLogger(__name__)
        self._completion_fn = completion_fn

    def is_configured(self) -> bool:
        """Check if AI suggestions are properly configured."""
        model = self.config.get("llm_model")
        if not model:
            return False

        # For non-Ollama models, we need an API key
        if not model.startswith("ollama/"):
            api_key = self.config.get("llm_api_key")
            if not api_key:
                return False

        return True

    def get_suggestions(
        self,
        user_id: str,
        max_results: int = 8,
    ) -> List[Dict[str, Any]]:
        """
        Get AI-powered song suggestions for a user.

        Args:
            user_id: The user to generate suggestions for
            max_results: Maximum number of suggestions to return

        Returns:
            List of video dictionaries (same format as search results)
        """
        if not self.is_configured():
            raise SuggestionError("AI suggestions not configured")

        # Build context for the LLM
        context = self._build_context(user_id)

        # Generate suggestions via LLM
        suggestions = self._generate_suggestions(context, max_results)

        if not suggestions:
            raise SuggestionError("AI returned no suggestions. Try adjusting the temperature.")

        # Search YouTube for each suggestion
        results = self._search_suggestions(suggestions, max_results)

        if not results:
            raise SuggestionError("Could not find karaoke videos for the suggested songs.")

        return results

    def _build_context(self, user_id: str) -> Dict[str, Any]:
        """Build context dict for the LLM prompt."""
        context: Dict[str, Any] = {}

        # Helper to get artist/title from metadata, preferring extracted values
        def get_song_info(metadata):
            # Use extracted values if available, otherwise fall back to title/channel
            title = metadata.song_name or metadata.title
            artist = metadata.artist or metadata.channel or "Unknown"
            return {"title": title, "artist": artist}

        # User's recent history (deduplicated by video_id)
        try:
            history = self.history.get_user_history(user_id, limit=50)
            if history:
                seen_videos: set[str] = set()
                unique_songs = []
                for record in history:
                    if record.video_id not in seen_videos:
                        seen_videos.add(record.video_id)
                        unique_songs.append(get_song_info(record.metadata))
                context["user_history"] = unique_songs
        except Exception as e:
            self.logger.debug("Could not get user history: %s", e)

        # Current queue
        try:
            queue = self.queue.get_queue()
            unplayed = [item for item in queue if item.played_at is None]
            if unplayed:
                context["current_queue"] = [
                    {
                        **get_song_info(item.metadata),
                        "user": item.user_name,
                    }
                    for item in unplayed[:10]  # Limit to 10 for prompt size
                ]
        except Exception as e:
            self.logger.debug("Could not get queue: %s", e)

        # Operator theme
        theme = self.config.get("suggestion_theme")
        if theme:
            context["theme"] = theme

        return context

    def _generate_suggestions(
        self,
        context: Dict[str, Any],
        count: int,
    ) -> List[Dict[str, str]]:
        """
        Call the LLM to generate song suggestions.

        Returns list of {"title": ..., "artist": ...} dicts.
        """
        # Build the prompt
        prompt = self._build_prompt(context, count)

        # Get LLM config
        model = self.config.get("llm_model")
        api_key = self.config.get("llm_api_key")
        base_url = self.config.get("llm_base_url")
        temperature = self.config.get_float("llm_temperature", 0.9)

        # Get completion function (use litellm if not injected)
        completion_fn = self._completion_fn
        if completion_fn is None:
            import litellm

            # Configure litellm
            if api_key:
                # Set the appropriate API key based on model prefix
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
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a karaoke song recommender with deep knowledge of music "
                        "across all genres and eras. You help singers discover songs that "
                        "suit their voice and taste - not just top-40 hits everyone knows. "
                        "You understand vocal ranges, song keys, and what makes a song "
                        "fun to perform at karaoke. Always return valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": 16384,
            # For reasoning models (GPT-5, o1, o3), use low effort - this is a simple task
            "reasoning_effort": "low",
        }

        if base_url:
            kwargs["api_base"] = base_url

        self.logger.debug("Calling LLM for suggestions: model=%s", model)

        try:
            response = completion_fn(**kwargs)
            message = response.choices[0].message
            content = message.content

            # Log the raw response for debugging
            finish_reason = response.choices[0].finish_reason
            if not content:
                self.logger.warning(
                    "LLM returned empty content. Model: %s, finish_reason: %s, message: %s",
                    model,
                    finish_reason,
                    message,
                )
                if finish_reason == "length":
                    raise SuggestionError(
                        "AI response was cut off (token limit). Try a different model."
                    )
                raise SuggestionError("AI model returned empty response. Try a different model.")

            self.logger.debug("LLM response content: %s", content[:200] if content else None)

            # Parse JSON from response
            return self._parse_llm_response(content)

        except SuggestionError:
            raise
        except Exception as e:
            self.logger.error("LLM call failed: %s", e, exc_info=True)
            raise SuggestionError(f"AI request failed: {e}")

    def _build_prompt(self, context: Dict[str, Any], count: int) -> str:
        """Build the prompt for the LLM."""
        parts = [f"Suggest {count} karaoke songs for this singer."]

        if context.get("user_history"):
            history_str = ", ".join(
                f'"{s["title"]}" by {s["artist"]}' for s in context["user_history"][:8]
            )
            parts.append(
                f"\n\nThe singer has previously performed these songs: {history_str}"
                "\n\nAnalyze their song choices to understand:"
                "\n- Their likely vocal range and style"
                "\n- Genres and eras they gravitate toward"
                "\n- The emotional tone they prefer (upbeat, ballads, powerful, etc.)"
            )

        if context.get("current_queue"):
            queue_str = ", ".join(
                f'"{s["title"]}" by {s["artist"]}' for s in context["current_queue"][:5]
            )
            parts.append(f"\n\nThe current karaoke session includes: {queue_str}")

        if context.get("theme"):
            parts.append(f'\n\nThe party theme is: "{context["theme"]}"')

        parts.append(
            "\n\nSuggest songs that:"
            "\n- Match this singer's apparent vocal range and style"
            "\n- Fit their musical taste based on their history"
            "\n- Are enjoyable to perform (good for showing off, crowd participation, etc.)"
            "\n- Are NOT overplayed karaoke clichés that everyone has heard a million times"
            "\n- Are still well-known enough that karaoke versions exist on YouTube"
            "\n- Are different from songs already in the queue"
        )

        if context.get("theme"):
            parts.append(f'\n- Fit the "{context["theme"]}" theme')

        parts.append(
            "\n\nThink beyond the obvious top-40 hits. Consider deep cuts, album tracks, "
            "songs from similar artists, or lesser-known songs from well-known artists."
        )

        parts.append(
            f"\n\nReturn ONLY a JSON array of {count} songs in this exact format:"
            '\n[{"title": "Song Name", "artist": "Artist Name"}, ...]'
            "\n\nNo explanation, just the JSON array."
        )

        return "".join(parts)

    def _parse_llm_response(self, content: str) -> List[Dict[str, str]]:
        """Parse the LLM response to extract song suggestions."""
        if not content:
            return []

        # Try to extract JSON from the response
        content = content.strip()

        # Handle markdown code blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(content)
            if isinstance(data, list):
                # Validate structure
                suggestions = []
                for item in data:
                    if isinstance(item, dict) and "title" in item and "artist" in item:
                        suggestions.append(
                            {"title": str(item["title"]), "artist": str(item["artist"])}
                        )
                if suggestions:
                    return suggestions
                else:
                    self.logger.warning(
                        "LLM response was valid JSON but contained no valid suggestions: %s",
                        content[:500],
                    )
            else:
                self.logger.warning("LLM response was not a JSON array: %s", content[:500])
        except json.JSONDecodeError as e:
            self.logger.warning(
                "Failed to parse LLM response as JSON: %s. Content: %s", e, content[:500]
            )

        return []

    def _search_suggestions(
        self,
        suggestions: List[Dict[str, str]],
        max_results: int,
    ) -> List[Dict[str, Any]]:
        """Search YouTube for each suggested song."""
        results: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        for suggestion in suggestions:
            if len(results) >= max_results:
                break

            query = f"{suggestion['title']} {suggestion['artist']}"
            try:
                search_results = self.video_library.search(query, max_results=2)
                for video in search_results:
                    if video["id"] not in seen_ids:
                        seen_ids.add(video["id"])
                        results.append(video)
                        break  # Only take the first unique result per suggestion
            except Exception as e:
                self.logger.debug("Search failed for %s: %s", query, e)

        return results
