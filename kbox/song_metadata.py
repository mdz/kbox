"""
Song metadata extraction using LLM.

Extracts artist and song name from YouTube video titles and descriptions.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from .database import Database
    from .llm import LLMClient


class SongMetadataExtractor:
    """Extracts artist and song name from video metadata using LLM."""

    def __init__(
        self,
        database: "Database",
        llm_client: Optional["LLMClient"] = None,
    ):
        """
        Initialize SongMetadataExtractor.

        Args:
            database: For caching extracted metadata
            llm_client: LLM client for extraction (optional)
        """
        self.database = database
        self.logger = logging.getLogger(__name__)
        self._llm_client = llm_client

    def is_configured(self) -> bool:
        """Check if LLM extraction is properly configured."""
        if self._llm_client is None:
            return False
        return self._llm_client.is_configured()

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
        start = time.monotonic()
        try:
            artist, song_name = self._extract_via_llm(title, description, channel)
            elapsed = time.monotonic() - start
            if artist and song_name:
                # Cache the result
                self._cache_result(video_id, artist, song_name)
                self.logger.info(
                    "Extracted metadata for %s in %.1fs: '%s' by '%s'",
                    video_id,
                    elapsed,
                    song_name,
                    artist,
                )
                return (artist, song_name)
            self.logger.info(
                "LLM extraction returned no metadata for %s (%.1fs)", video_id, elapsed
            )
        except Exception as e:
            elapsed = time.monotonic() - start
            self.logger.warning(
                "LLM extraction failed for %s after %.1fs: %s", video_id, elapsed, e
            )

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

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a metadata extraction assistant. Extract the artist name "
                    "and song title from karaoke video information. Return valid JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        self.logger.debug("Calling LLM for metadata extraction")

        # is_configured() ensures _llm_client is set before we get here
        assert self._llm_client is not None

        response = self._llm_client.completion(
            messages=messages,
            temperature=0.0,  # Deterministic for extraction
            max_tokens=256,
        )
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
            "\n\nThe 'artist' should be the performer of the specific recording this "
            "karaoke video is based on — i.e., the artist whose version is being covered. "
            "Disambiguation rules:\n"
            "- The channel name is usually a karaoke provider (like 'Zoom Karaoke', "
            "'SingKing', 'KaraFun'), NOT the artist.\n"
            "- Do NOT return the songwriter/composer if they are different from the "
            "performer (e.g., for a Jimi Hendrix cover of 'All Along the Watchtower', "
            "the artist is Jimi Hendrix, not Bob Dylan).\n"
            "- If the title or description explicitly names an artist (e.g., "
            "'in the style of X', 'as performed by X', 'X - Song Title'), prefer that "
            "artist over the most famous version you know of.\n"
            "- Only fall back to the best-known recording artist when the title and "
            "description give no indication of which version is being covered."
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
            # A row can exist with artist/song_name still NULL if silence
            # analysis (a separate pipeline, keyed on the same video_id)
            # cached its own result first -- that's not an extraction cache
            # hit, so fall through to running the LLM.
            if row and row["artist"] is not None and row["song_name"] is not None:
                return (row["artist"], row["song_name"])
            return None
        finally:
            conn.close()

    def _cache_result(self, video_id: str, artist: str, song_name: str) -> None:
        """Cache extraction result in database.

        Uses an upsert that only touches its own columns, since silence
        analysis may independently write trailing-silence columns to the
        same row (before or after this call).
        """
        conn = self.database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO song_metadata_cache (video_id, artist, song_name, extracted_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(video_id) DO UPDATE SET
                    artist = excluded.artist,
                    song_name = excluded.song_name,
                    extracted_at = excluded.extracted_at
                """,
                (video_id, artist, song_name),
            )
            conn.commit()
            self.logger.debug("Cached metadata for %s", video_id)
        finally:
            conn.close()
