"""
Song metadata extraction using LLM.

Extracts artist and song name from YouTube video titles and descriptions.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

if TYPE_CHECKING:
    from .config_manager import ConfigManager
    from .database import Database

# Type alias for LLM completion functions (litellm.completion signature)
CompletionFn = Callable[..., Any]


class SongMetadataExtractor:
    """Extracts artist and song name from video metadata using LLM."""

    def __init__(
        self,
        config_manager: "ConfigManager",
        database: "Database",
        completion_fn: Optional[CompletionFn] = None,
    ):
        """
        Initialize SongMetadataExtractor.

        Args:
            config_manager: For accessing LLM configuration
            database: For caching extracted metadata
            completion_fn: LLM completion function (defaults to litellm.completion)
        """
        self.config = config_manager
        self.database = database
        self.logger = logging.getLogger(__name__)
        self._completion_fn = completion_fn

    def is_configured(self) -> bool:
        """Check if LLM extraction is properly configured."""
        model = self.config.get("llm_model")
        if not model:
            return False

        # For non-Ollama models, we need an API key
        if not model.startswith("ollama/"):
            api_key = self.config.get("llm_api_key")
            if not api_key:
                return False

        return True

    def extract(
        self,
        video_id: str,
        title: str,
        description: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract artist and song name from video metadata.

        Args:
            video_id: Opaque video ID for caching (e.g., "youtube:abc123")
            title: Video title
            description: Video description (optional, helps with extraction)
            channel: YouTube channel name (optional, helps identify karaoke channels)

        Returns:
            Tuple of (artist, song_name), both None if extraction failed
        """
        # Check cache first
        cached = self._get_cached(video_id)
        if cached is not None:
            self.logger.debug("Cache hit for %s: %s - %s", video_id, cached[0], cached[1])
            return cached

        # If LLM not configured, return None
        if not self.is_configured():
            self.logger.debug("LLM not configured, skipping extraction")
            return (None, None)

        # Extract via LLM
        try:
            artist, song_name = self._extract_via_llm(title, description, channel)
            if artist and song_name:
                # Cache the result
                self._cache_result(video_id, artist, song_name)
                self.logger.info(
                    "Extracted metadata for %s: '%s' by '%s'", video_id, song_name, artist
                )
                return (artist, song_name)
        except Exception as e:
            self.logger.warning("LLM extraction failed for %s: %s", video_id, e)

        return (None, None)

    def _extract_via_llm(
        self,
        title: str,
        description: Optional[str],
        channel: Optional[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        """Call LLM to extract artist and song name."""
        # Build the prompt
        prompt = self._build_prompt(title, description, channel)

        # Get LLM config
        model = self.config.get("llm_model")
        api_key = self.config.get("llm_api_key")
        base_url = self.config.get("llm_base_url")

        # Get completion function (use litellm if not injected)
        completion_fn = self._completion_fn
        if completion_fn is None:
            import litellm

            # Configure litellm
            if api_key:
                if model.startswith("claude") or model.startswith("anthropic"):
                    litellm.anthropic_key = api_key
                elif model.startswith("gemini"):
                    litellm.gemini_key = api_key
                else:
                    litellm.openai_key = api_key

            litellm.drop_params = True
            completion_fn = litellm.completion

        # Build completion kwargs
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a metadata extraction assistant. Extract the artist name "
                        "and song title from karaoke video information. Return valid JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,  # Deterministic for extraction
            "max_tokens": 256,
        }

        if base_url:
            kwargs["api_base"] = base_url

        self.logger.debug("Calling LLM for metadata extraction: model=%s", model)

        response = completion_fn(**kwargs)
        content = response.choices[0].message.content

        if not content:
            self.logger.warning("LLM returned empty content for extraction")
            return (None, None)

        return self._parse_llm_response(content)

    def _build_prompt(
        self,
        title: str,
        description: Optional[str],
        channel: Optional[str],
    ) -> str:
        """Build the extraction prompt."""
        parts = [
            "Extract the artist name and song title from this karaoke video information.",
            f'\nVideo title: "{title}"',
        ]

        if channel:
            parts.append(f'\nChannel: "{channel}"')

        if description:
            # Truncate long descriptions
            desc = description[:300] if len(description) > 300 else description
            parts.append(f'\nDescription: "{desc}"')

        parts.append(
            "\n\nNote: The channel name is usually a karaoke provider (like 'Zoom Karaoke', "
            "'SingKing', 'KaraFun'), NOT the artist. Extract the actual performing artist "
            "and song title."
        )

        parts.append(
            "\n\nReturn ONLY a JSON object in this exact format:\n"
            '{"artist": "Artist Name", "song_name": "Song Title"}\n\n'
            "No explanation, just the JSON."
        )

        return "".join(parts)

    def _parse_llm_response(self, content: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse the LLM response to extract artist and song name."""
        content = content.strip()

        # Handle markdown code blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(content)
            artist = data.get("artist")
            song_name = data.get("song_name")

            if artist and song_name:
                return (str(artist).strip(), str(song_name).strip())
        except json.JSONDecodeError as e:
            self.logger.warning("Failed to parse LLM response as JSON: %s", e)

        return (None, None)

    def _get_cached(self, video_id: str) -> Optional[Tuple[str, str]]:
        """Get cached extraction result from database."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT artist, song_name FROM song_metadata_cache
                WHERE video_id = ?
                """,
                (video_id,),
            )
            row = cursor.fetchone()
            if row:
                return (row["artist"], row["song_name"])
            return None
        finally:
            conn.close()

    def _cache_result(self, video_id: str, artist: str, song_name: str) -> None:
        """Cache extraction result in database."""
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO song_metadata_cache (video_id, artist, song_name)
                VALUES (?, ?, ?)
                """,
                (video_id, artist, song_name),
            )
            conn.commit()
            self.logger.debug("Cached metadata for %s", video_id)
        finally:
            conn.close()
