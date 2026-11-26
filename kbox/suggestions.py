"""
AI-powered song suggestion engine for kbox.

Uses LLMClient to generate personalized karaoke song recommendations based on
user history, current queue, and operator-configured theme.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .config_manager import ConfigManager
    from .history import HistoryManager
    from .llm import LLMClient
    from .queue import QueueManager
    from .video_library import VideoLibrary


# How many times to ask the model before telling the guest it didn't work.
PARSE_ATTEMPTS = 2

# Structured-output schema, for models that support it. OpenAI's strict mode
# requires an object at the top level, hence the "songs" wrapper.
SUGGESTIONS_RESPONSE_FORMAT: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "song_suggestions",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "songs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "artist": {"type": "string"},
                        },
                        "required": ["title", "artist"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["songs"],
            "additionalProperties": False,
        },
    },
}

# A curly double quote standing where JSON needs a straight one: right after
# an opening bracket, comma or colon, or right before a closing one, a comma
# or a colon. Curly quotes inside a title ("Say “Hello”") are left alone.
_CURLY_QUOTE_OPENING = re.compile(r"([\[{,:]\s*)[\u201c\u201d]")
_CURLY_QUOTE_CLOSING = re.compile(r"[\u201c\u201d](\s*[,:}\]])")
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
# A flat {...} with no nested braces: one song entry.
_FLAT_OBJECT = re.compile(r"\{[^{}]*\}")


def _repair_json(content: str) -> str:
    """
    Fix the slips models make in otherwise good JSON.

    Only used once strict parsing has already failed, so the patterns can be
    loose: at worst they turn one unparseable string into another.
    """
    content = _CURLY_QUOTE_OPENING.sub(r'\1"', content)
    content = _CURLY_QUOTE_CLOSING.sub(r'"\1', content)
    return _TRAILING_COMMA.sub(r"\1", content)


def _salvage_objects(content: str) -> List[Any]:
    """Parse each flat ``{...}`` in the reply on its own, keeping those that parse."""
    salvaged = []
    for match in _FLAT_OBJECT.finditer(content):
        try:
            salvaged.append(json.loads(_repair_json(match.group(0))))
        except json.JSONDecodeError:
            continue
    return salvaged


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
        llm_client: Optional["LLMClient"] = None,
    ):
        """
        Initialize SuggestionEngine.

        Args:
            config_manager: For accessing theme configuration
            history_manager: For retrieving user's song history
            queue_manager: For getting current queue context
            video_library: For searching songs on YouTube
            llm_client: LLM client for generating suggestions (optional for backward compat)
        """
        self.config = config_manager
        self.history = history_manager
        self.queue = queue_manager
        self.video_library = video_library
        self.logger = logging.getLogger(__name__)
        self._llm_client = llm_client

    def is_configured(self) -> bool:
        """Check if AI suggestions are properly configured."""
        if self._llm_client is None:
            return False
        return self._llm_client.is_configured()

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

        # Generate suggestions via LLM (raises rather than returning none)
        suggestions = self._generate_suggestions(context, max_results)

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
        except Exception:
            # Local SQLite reads, so a failure here is not an expected
            # degrade. Suggestions still get generated, just with nothing
            # about this user in the prompt -- which looks to an operator
            # like the model ignoring their taste rather than like a bug.
            self.logger.warning(
                "Could not read history for user %s; suggestions will be "
                "generated without it and will be correspondingly generic",
                user_id,
                exc_info=True,
            )

        # Current queue (all songs - played and upcoming - for full context)
        try:
            queue = self.queue.get_queue()
            if queue:
                context["current_queue"] = [
                    {
                        **get_song_info(item.metadata),
                        "user": item.user_name,
                    }
                    for item in queue[:20]  # Limit for prompt size
                ]
        except Exception:
            self.logger.warning(
                "Could not read the current queue; suggestions will be generated "
                "without it and may repeat songs that are already queued",
                exc_info=True,
            )

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

        temperature = self.config.get_float("llm_temperature", 0.9)

        messages = [
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
        ]

        self.logger.debug("Calling LLM for suggestions")

        # is_configured() ensures _llm_client is set before we get here
        assert self._llm_client is not None

        extra: Dict[str, Any] = {}
        if self._llm_client.supports_response_schema():
            # Have the provider hold the reply to the schema, so it cannot come
            # back with a stray curly quote or trailing comma. Models without
            # it rely on the prompt and the tolerant parser below.
            extra["response_format"] = SUGGESTIONS_RESPONSE_FORMAT

        # A reply that cannot be parsed is usually a one-off slip by the
        # model, so it is worth one more try before giving up on the guest.
        for attempt in range(1, PARSE_ATTEMPTS + 1):
            content = self._request_suggestions(messages, temperature, extra)
            suggestions = self._parse_llm_response(content)
            if suggestions:
                return suggestions
            self.logger.warning(
                "No usable suggestions in LLM reply (attempt %d of %d)", attempt, PARSE_ATTEMPTS
            )

        raise SuggestionError(
            "Couldn't come up with suggestions this time. Try again, or search for a song."
        )

    def _request_suggestions(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        extra: Dict[str, Any],
    ) -> str:
        """Make one LLM call and return its text, raising SuggestionError on failure."""
        assert self._llm_client is not None

        try:
            response = self._llm_client.completion(
                messages=messages,
                temperature=temperature,
                max_tokens=16384,
                # For reasoning models (GPT-5, o1, o3), use low effort - this is a simple task
                reasoning_effort="low",
                **extra,
            )
            message = response.choices[0].message
            content = message.content

            # Log the raw response for debugging
            finish_reason = response.choices[0].finish_reason
            if not content:
                self.logger.warning(
                    "LLM returned empty content. finish_reason: %s, message: %s",
                    finish_reason,
                    message,
                )
                if finish_reason == "length":
                    raise SuggestionError(
                        "AI response was cut off (token limit). Try a different model."
                    )
                raise SuggestionError("AI model returned empty response. Try a different model.")

            self.logger.debug("LLM response content: %s", content[:200])
            return str(content)

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
            f"\n\nReturn ONLY a JSON object listing {count} songs in this exact format:"
            '\n{"songs": [{"title": "Song Name", "artist": "Artist Name"}, ...]}'
            "\n\nNo explanation, just the JSON."
        )

        return "".join(parts)

    def _parse_llm_response(self, content: str) -> List[Dict[str, str]]:
        """
        Parse the LLM response to extract song suggestions.

        Accepts the ``{"songs": [...]}`` object the prompt and schema ask for,
        or a bare array. A reply that is not valid JSON gets the repairs in
        :func:`_repair_json`; if it still will not parse, whichever song
        objects in it do parse are kept rather than losing the whole list to
        one bad entry.
        """
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
        except json.JSONDecodeError as e:
            try:
                data = json.loads(_repair_json(content))
                self.logger.info("Repaired malformed JSON in LLM response (%s)", e)
            except json.JSONDecodeError:
                data = _salvage_objects(content)
                self.logger.warning(
                    "Failed to parse LLM response as JSON: %s. Kept %d entries. Content: %s",
                    e,
                    len(data),
                    content[:500],
                )

        if isinstance(data, dict) and isinstance(data.get("songs"), list):
            data = data["songs"]

        if not isinstance(data, list):
            self.logger.warning("LLM response was not a list of songs: %s", content[:500])
            return []

        suggestions = []
        for item in data:
            if isinstance(item, dict) and "title" in item and "artist" in item:
                suggestions.append({"title": str(item["title"]), "artist": str(item["artist"])})
        if not suggestions:
            self.logger.warning(
                "LLM response contained no valid suggestions: %s",
                content[:500],
            )
        return suggestions

    def _search_suggestions(
        self,
        suggestions: List[Dict[str, str]],
        max_results: int,
    ) -> List[Dict[str, Any]]:
        """Search YouTube for each suggested song."""
        results: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        attempted = 0
        failed = 0

        for suggestion in suggestions:
            if len(results) >= max_results:
                break

            query = f"{suggestion['title']} {suggestion['artist']}"
            attempted += 1
            try:
                search_results = self.video_library.search(query, max_results=2)
                for video in search_results:
                    if video["id"] not in seen_ids:
                        seen_ids.add(video["id"])
                        results.append(video)
                        break  # Only take the first unique result per suggestion
            except Exception as e:
                # No traceback per query: a failing backend fails every one of
                # these, and the exception message is the useful part.
                failed += 1
                self.logger.warning("Search failed for %r: %s", query, e)

        if failed and failed == attempted:
            # get_suggestions() is about to raise "Could not find karaoke
            # videos for the suggested songs", which blames the model for
            # what is actually a broken search backend. That message cannot
            # tell the difference; this line can.
            self.logger.error(
                "Every suggestion lookup failed (%d of %d); the suggestions "
                "themselves may be fine and the search backend is the likely cause",
                failed,
                attempted,
            )

        return results
