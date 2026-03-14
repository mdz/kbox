"""
YouTube Data API v3 client for kbox.

Provides search and metadata retrieval using the YouTube Data API.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from googleapiclient.discovery import build

if TYPE_CHECKING:
    from .config_manager import ConfigManager


class YouTubeAPI:
    """YouTube Data API v3 client for video search and metadata."""

    def __init__(self, config_manager: "ConfigManager"):
        self.logger = logging.getLogger(__name__)
        self.config_manager = config_manager

        self._youtube = None
        self._last_api_key: Optional[str] = None

        self.logger.info("YouTubeAPI initialized")

    def _get_client(self):
        """
        Get or create the API client.

        Returns None if API key is not configured.
        Reinitializes if the API key has changed (allowing runtime updates).
        """
        api_key = self.config_manager.get("youtube_api_key")

        if not api_key:
            self._youtube = None
            self._last_api_key = None
            return None

        if api_key != self._last_api_key:
            try:
                self._youtube = build("youtube", "v3", developerKey=api_key)
                self._last_api_key = api_key
                self.logger.info("YouTube API client initialized")
            except Exception as e:
                self.logger.error("Failed to initialize YouTube API client: %s", e)
                self._youtube = None
                self._last_api_key = None

        return self._youtube

    def is_available(self) -> bool:
        """True when an API key is configured and the client is ready."""
        return self._get_client() is not None

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Search for videos using the YouTube Data API.

        Appends "karaoke" to the query automatically.

        Args:
            query: Search query
            max_results: Maximum number of results

        Returns:
            List of video result dicts.

        Raises:
            RuntimeError: If API key is not configured.
            googleapiclient.errors.HttpError: On API errors.
        """
        youtube = self._get_client()
        if not youtube:
            raise RuntimeError("YouTube API key is not configured")

        search_query = f"{query} karaoke"
        self.logger.debug("Searching YouTube via API: %s", search_query)

        request = youtube.search().list(
            part="snippet",
            q=search_query,
            type="video",
            maxResults=max_results,
            order="relevance",
        )
        response = request.execute()

        video_ids = [item["id"]["videoId"] for item in response.get("items", [])]
        if not video_ids:
            self.logger.info("No videos found for query: %s", search_query)
            return []

        videos_request = youtube.videos().list(
            part="contentDetails,snippet", id=",".join(video_ids)
        )
        videos_response = videos_request.execute()

        results = []
        for item in videos_response.get("items", []):
            video_id = item["id"]
            snippet = item["snippet"]
            content_details = item.get("contentDetails", {})
            duration_seconds = self._parse_duration(content_details.get("duration", ""))

            results.append(
                {
                    "id": video_id,
                    "title": snippet.get("title", ""),
                    "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                    "channel": snippet.get("channelTitle", ""),
                    "duration_seconds": duration_seconds,
                    "description": snippet.get("description", "")[:200],
                }
            )

        self.logger.info("Found %s videos via API for query: %s", len(results), search_query)
        return results

    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Get metadata for a specific video.

        Args:
            video_id: YouTube video ID.

        Returns:
            Video metadata dict, or None if not found.

        Raises:
            RuntimeError: If API key is not configured.
            googleapiclient.errors.HttpError: On API errors.
        """
        youtube = self._get_client()
        if not youtube:
            raise RuntimeError("YouTube API key is not configured")

        request = youtube.videos().list(part="contentDetails,snippet", id=video_id)
        response = request.execute()

        if not response.get("items"):
            self.logger.warning("Video not found: %s", video_id)
            return None

        item = response["items"][0]
        snippet = item["snippet"]
        content_details = item.get("contentDetails", {})
        duration_seconds = self._parse_duration(content_details.get("duration", ""))

        return {
            "id": video_id,
            "title": snippet.get("title", ""),
            "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
            "channel": snippet.get("channelTitle", ""),
            "duration_seconds": duration_seconds,
            "description": snippet.get("description", ""),
        }

    @staticmethod
    def _parse_duration(duration_str: str) -> Optional[int]:
        """
        Parse an ISO 8601 duration string to seconds.

        Args:
            duration_str: e.g. "PT4M13S"

        Returns:
            Duration in seconds, or None if parsing fails.
        """
        if not duration_str or not duration_str.startswith("PT"):
            return None

        try:
            remainder = duration_str[2:]
            if not remainder:
                return None

            hours = minutes = seconds = 0

            if "H" in remainder:
                parts = remainder.split("H", 1)
                hours = int(parts[0])
                remainder = parts[1] if len(parts) > 1 else ""

            if "M" in remainder:
                parts = remainder.split("M", 1)
                minutes = int(parts[0])
                remainder = parts[1] if len(parts) > 1 else ""

            if "S" in remainder:
                parts = remainder.split("S", 1)
                if parts[0]:
                    seconds = int(parts[0])
                remainder = parts[1] if len(parts) > 1 else ""

            if remainder.strip():
                return None

            return hours * 3600 + minutes * 60 + seconds
        except (ValueError, AttributeError, IndexError):
            return None
