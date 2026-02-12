"""
YouTube video source for kbox.

Handles YouTube search and video download via yt-dlp, with optional
YouTube Data API v3 for faster search when an API key is configured.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import yt_dlp
from googleapiclient.discovery import build

from .video_library import VideoSource

if TYPE_CHECKING:
    from .config_manager import ConfigManager


class YouTubeSource(VideoSource):
    """YouTube video source implementation."""

    def __init__(self, config_manager: "ConfigManager"):
        """
        Initialize YouTubeSource.

        Args:
            config_manager: ConfigManager for runtime config access
        """
        self.logger = logging.getLogger(__name__)
        self.config_manager = config_manager

        # Lazy-initialized YouTube API client
        self._youtube = None
        self._last_api_key: Optional[str] = None

        # Rate limiting for yt-dlp calls (shared across search/info/download)
        self._ytdlp_min_interval = 2.0  # seconds between yt-dlp calls
        self._ytdlp_last_call: float = 0.0
        self._ytdlp_lock = threading.Lock()

        self.logger.info("YouTubeSource initialized")

    @property
    def source_id(self) -> str:
        """Return the source identifier."""
        return "youtube"

    def _get_youtube_client(self):
        """
        Get or create YouTube API client.

        Returns None if API key is not configured.
        Reinitializes client if API key has changed (allowing runtime updates).
        """
        api_key = self.config_manager.get("youtube_api_key")

        if not api_key:
            self._youtube = None
            self._last_api_key = None
            return None

        # Reinitialize if API key changed
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

    def is_configured(self) -> bool:
        """Always configured -- yt-dlp search needs no credentials."""
        return True

    def _ytdlp_rate_limit(self) -> None:
        """Sleep if needed to enforce minimum interval between yt-dlp calls."""
        with self._ytdlp_lock:
            now = time.monotonic()
            elapsed = now - self._ytdlp_last_call
            if elapsed < self._ytdlp_min_interval:
                wait = self._ytdlp_min_interval - elapsed
                self.logger.debug("Rate limiting yt-dlp: sleeping %.1fs", wait)
                time.sleep(wait)
            self._ytdlp_last_call = time.monotonic()

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Search YouTube for videos, automatically appending "karaoke" to query.

        Uses yt-dlp by default, falls back to Data API if configured.

        Args:
            query: Search query (will have "karaoke" appended)
            max_results: Maximum number of results to return

        Returns:
            List of video dictionaries with keys: id, title, thumbnail, duration, etc.
        """
        try:
            results = self._search_ytdlp(query, max_results)
            if results:
                return results
        except Exception as e:
            self.logger.warning("yt-dlp search failed: %s", e)

        # Fallback to Data API if configured
        if self._get_youtube_client():
            try:
                return self._search_api(query, max_results)
            except Exception as e:
                self.logger.warning("YouTube API search also failed: %s", e)

        return []

    def _search_api(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Search YouTube using the Data API v3.

        Args:
            query: Search query (will have "karaoke" appended)
            max_results: Maximum number of results to return

        Returns:
            List of video dictionaries.

        Raises:
            HttpError: On API errors (caller handles fallback)
        """
        youtube = self._get_youtube_client()

        # Automatically append "karaoke" to search query
        search_query = f"{query} karaoke"
        self.logger.debug("Searching YouTube via API: %s", search_query)

        # Search for videos
        request = youtube.search().list(
            part="snippet",
            q=search_query,
            type="video",
            maxResults=max_results,
            order="relevance",
        )
        response = request.execute()

        # Extract video IDs
        video_ids = [item["id"]["videoId"] for item in response.get("items", [])]

        if not video_ids:
            self.logger.info("No videos found for query: %s", search_query)
            return []

        # Get detailed information including duration
        videos_request = youtube.videos().list(
            part="contentDetails,snippet", id=",".join(video_ids)
        )
        videos_response = videos_request.execute()

        # Format results
        results = []
        for item in videos_response.get("items", []):
            video_id = item["id"]
            snippet = item["snippet"]
            content_details = item.get("contentDetails", {})

            # Parse duration (ISO 8601 format)
            duration_seconds = self._parse_duration(content_details.get("duration", ""))

            results.append(
                {
                    "id": video_id,
                    "title": snippet.get("title", ""),
                    "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                    "channel": snippet.get("channelTitle", ""),
                    "duration_seconds": duration_seconds,
                    "description": snippet.get("description", "")[:200],  # Truncate
                }
            )

        self.logger.info("Found %s videos via API for query: %s", len(results), search_query)
        return results

    def _parse_duration(self, duration_str: str) -> Optional[int]:
        """
        Parse ISO 8601 duration string to seconds.

        Args:
            duration_str: ISO 8601 duration (e.g., "PT4M13S")

        Returns:
            Duration in seconds, or None if parsing fails
        """
        if not duration_str:
            return None

        # Must start with PT
        if not duration_str.startswith("PT"):
            return None

        try:
            # Remove PT prefix
            duration_str = duration_str[2:]  # Remove 'PT'

            # If empty after removing PT, invalid
            if not duration_str:
                return None

            hours = 0
            minutes = 0
            seconds = 0

            # Parse hours
            if "H" in duration_str:
                parts = duration_str.split("H", 1)
                hours = int(parts[0])
                duration_str = parts[1] if len(parts) > 1 else ""

            # Parse minutes
            if "M" in duration_str:
                parts = duration_str.split("M", 1)
                minutes = int(parts[0])
                duration_str = parts[1] if len(parts) > 1 else ""

            # Parse seconds
            if "S" in duration_str:
                # Extract everything before 'S' as seconds
                parts = duration_str.split("S", 1)
                if parts[0]:  # Only parse if there's actually a number
                    seconds = int(parts[0])
                duration_str = parts[1] if len(parts) > 1 else ""

            # Check if there's any remaining unparsed text (should be empty now)
            if duration_str.strip():
                # Invalid format - has text that wasn't parsed
                return None

            return hours * 3600 + minutes * 60 + seconds
        except (ValueError, AttributeError, IndexError) as e:
            self.logger.warning("Failed to parse duration %s: %s", duration_str, e)
            return None

    # =========================================================================
    # yt-dlp search (no API key needed)
    # =========================================================================

    def _search_ytdlp(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Search YouTube using yt-dlp (no API key needed).

        Args:
            query: Search query (will have "karaoke" appended)
            max_results: Maximum number of results to return

        Returns:
            List of video dictionaries with keys: id, title, thumbnail, etc.
        """
        search_query = f"{query} karaoke"
        self.logger.debug("Searching YouTube via yt-dlp: %s", search_query)

        ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": False}

        try:
            self._ytdlp_rate_limit()
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch{max_results}:{search_query}", download=False)

            results = []
            for entry in (info or {}).get("entries", []):
                results.append(
                    {
                        "id": entry["id"],
                        "title": entry.get("title", ""),
                        "thumbnail": entry.get("thumbnail", ""),
                        "channel": entry.get("channel", "") or entry.get("uploader", ""),
                        "duration_seconds": entry.get("duration"),
                        "description": (entry.get("description") or "")[:200],
                    }
                )

            self.logger.info("Found %s videos via yt-dlp for query: %s", len(results), search_query)
            return results

        except Exception as e:
            self.logger.error("yt-dlp search error: %s", e, exc_info=True)
            return []

    def _get_video_info_ytdlp(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Get video info using yt-dlp (no API key needed).

        Args:
            video_id: YouTube video ID

        Returns:
            Video dictionary with metadata, or None if not found
        """
        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {"quiet": True, "no_warnings": True}

        try:
            self._ytdlp_rate_limit()
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                return None

            return {
                "id": video_id,
                "title": info.get("title", ""),
                "thumbnail": info.get("thumbnail", ""),
                "channel": info.get("channel", "") or info.get("uploader", ""),
                "duration_seconds": info.get("duration"),
                "description": (info.get("description") or "")[:200],
            }

        except Exception as e:
            self.logger.error("yt-dlp video info error for %s: %s", video_id, e)
            return None

    # =========================================================================
    # Data API (optional, used when API key is configured)
    # =========================================================================

    def _get_video_info_api(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific video via Data API.

        Args:
            video_id: YouTube video ID

        Returns:
            Video dictionary with metadata, or None if not found

        Raises:
            HttpError: On API errors (caller handles fallback)
        """
        youtube = self._get_youtube_client()

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

    # =========================================================================
    # Public interface (orchestrates API vs yt-dlp)
    # =========================================================================

    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific video.

        Uses yt-dlp by default, falls back to Data API if configured.

        Args:
            video_id: YouTube video ID

        Returns:
            Video dictionary with metadata, or None if not found
        """
        try:
            result = self._get_video_info_ytdlp(video_id)
            if result:
                return result
        except Exception as e:
            self.logger.warning("yt-dlp video info failed for %s: %s", video_id, e)

        # Fallback to Data API if configured
        if self._get_youtube_client():
            try:
                return self._get_video_info_api(video_id)
            except Exception as e:
                self.logger.warning("YouTube API info also failed for %s: %s", video_id, e)

        return None

    def download(self, video_id: str, output_dir: Path) -> Path:
        """
        Download a video using yt-dlp (synchronous).

        Args:
            video_id: YouTube video ID
            output_dir: Directory to download into

        Returns:
            Path to the downloaded video file

        Raises:
            Exception: If download fails
        """
        # Ensure output directory exists
        output_dir.mkdir(parents=True, exist_ok=True)

        # Lower thread priority to avoid interfering with GStreamer playback
        try:
            os.nice(10)
        except (OSError, AttributeError):
            pass  # Windows doesn't support nice(), or permission denied

        try:
            output_template = str(output_dir / "video.%(ext)s")
            max_res = self.config_manager.get_int("video_max_resolution", 480)

            ydl_opts = {
                "format": f"bestvideo[height<={max_res}]+bestaudio/best",
                "outtmpl": output_template,
                "quiet": False,
                "no_warnings": False,
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android", "web"],
                    }
                },
                "retries": 3,
                "fragment_retries": 3,
                "cookiefile": None,
            }

            url = f"https://www.youtube.com/watch?v={video_id}"

            self._ytdlp_rate_limit()
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_path = Path(ydl.prepare_filename(info))

            if not downloaded_path.exists():
                raise FileNotFoundError(f"Downloaded file not found: {downloaded_path}")

            self.logger.info("Downloaded video %s to %s", video_id, downloaded_path)
            return downloaded_path

        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg or "Forbidden" in error_msg:
                error_msg = "YouTube blocked the download (403 Forbidden). Try updating yt-dlp."
            elif "Private video" in error_msg:
                error_msg = "Video is private or unavailable"
            elif "Video unavailable" in error_msg:
                error_msg = "Video is unavailable or has been removed"

            self.logger.error("Error downloading video %s: %s", video_id, error_msg)
            raise RuntimeError(error_msg) from e
