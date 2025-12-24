"""
YouTube video source for kbox.

Handles YouTube search via Data API v3 and video download via yt-dlp.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

import yt_dlp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .video_source import VideoSource

if TYPE_CHECKING:
    from .cache import CacheManager
    from .config_manager import ConfigManager


class YouTubeSource(VideoSource):
    """YouTube video source implementation."""

    def __init__(
        self,
        config_manager: "ConfigManager",
        cache_manager: "CacheManager",
    ):
        """
        Initialize YouTubeSource.

        Args:
            config_manager: ConfigManager for runtime config access
            cache_manager: CacheManager for cache operations
        """
        self.logger = logging.getLogger(__name__)
        self.config_manager = config_manager
        self.cache_manager = cache_manager

        # Lazy-initialized YouTube API client
        self._youtube = None
        self._last_api_key: Optional[str] = None

        # Semaphore to limit concurrent downloads to 1 (avoid abusing YouTube)
        self._download_semaphore = threading.Semaphore(1)

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
        """Check if YouTube API key is configured and valid."""
        return self._get_youtube_client() is not None

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Search YouTube for videos, automatically appending "karaoke" to query.

        Args:
            query: Search query (will have "karaoke" appended)
            max_results: Maximum number of results to return

        Returns:
            List of video dictionaries with keys: id, title, thumbnail, duration, etc.
            Returns empty list if API key is not configured.
        """
        youtube = self._get_youtube_client()
        if not youtube:
            self.logger.warning("YouTube API key not configured, search unavailable")
            return []

        # Automatically append "karaoke" to search query
        search_query = f"{query} karaoke"
        self.logger.debug("Searching YouTube: %s", search_query)

        try:
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
                        "thumbnail": snippet.get("thumbnails", {})
                        .get("default", {})
                        .get("url", ""),
                        "channel": snippet.get("channelTitle", ""),
                        "duration_seconds": duration_seconds,
                        "description": snippet.get("description", "")[:200],  # Truncate
                    }
                )

            self.logger.info("Found %s videos for query: %s", len(results), search_query)
            return results

        except HttpError as e:
            self.logger.error("YouTube API error: %s", e)
            return []
        except Exception as e:
            self.logger.error("Error searching YouTube: %s", e, exc_info=True)
            return []

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

    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific video.

        Args:
            video_id: YouTube video ID

        Returns:
            Video dictionary with metadata, or None if not found/API not configured
        """
        youtube = self._get_youtube_client()
        if not youtube:
            self.logger.warning("YouTube API key not configured, video info unavailable")
            return None

        try:
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
        except HttpError as e:
            self.logger.error("YouTube API error getting video info: %s", e)
            return None
        except Exception as e:
            self.logger.error("Error getting video info: %s", e, exc_info=True)
            return None

    def get_cached_path(self, video_id: str, touch: bool = True) -> Optional[Path]:
        """
        Get the path to a cached video file if it exists.

        Args:
            video_id: YouTube video ID
            touch: If True, update file mtime for LRU tracking (default True)

        Returns:
            Path to video file if exists, None otherwise
        """
        return self.cache_manager.get_file_path(self.source_id, video_id, touch=touch)

    def is_cached(self, video_id: str) -> bool:
        """
        Check if a video is already cached.

        Args:
            video_id: YouTube video ID

        Returns:
            True if video is cached
        """
        return self.cache_manager.is_cached(self.source_id, video_id)

    def download(
        self,
        video_id: str,
        queue_item_id: int,
        status_callback: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
    ) -> Optional[str]:
        """
        Download a video using yt-dlp.

        Args:
            video_id: YouTube video ID
            queue_item_id: Queue item ID (for callback)
            status_callback: Callback function(status, path, error) for status updates

        Returns:
            Path to downloaded file if already cached, None if download started async
        """
        # Check if already downloaded
        existing_path = self.get_cached_path(video_id)
        if existing_path:
            self.logger.info("Video %s already cached at %s", video_id, existing_path)
            if status_callback:
                status_callback("ready", str(existing_path), None)
            return str(existing_path)

        # Download in background thread
        def download_thread():
            # Lower thread priority to avoid interfering with GStreamer playback
            try:
                os.nice(10)  # Increase niceness = lower scheduling priority
            except (OSError, AttributeError):
                pass  # Windows doesn't support nice(), or permission denied

            # Acquire semaphore to limit concurrent downloads to 1
            self._download_semaphore.acquire()
            try:
                if status_callback:
                    status_callback("downloading", None, None)

                # Configure yt-dlp options
                output_template = self.cache_manager.get_output_template(self.source_id, video_id)

                # Get max resolution from config (allows runtime changes)
                max_res = self.config_manager.get_int("video_max_resolution", 480)

                ydl_opts = {
                    "format": f"bestvideo[height<={max_res}]+bestaudio/best",
                    "outtmpl": output_template,
                    "quiet": False,
                    "no_warnings": False,
                    # Try multiple clients for better compatibility
                    "extractor_args": {
                        "youtube": {
                            "player_client": ["android", "web"],
                        }
                    },
                    # Retry on errors
                    "retries": 3,
                    "fragment_retries": 3,
                    # Use cookies if available (for YouTube Premium)
                    "cookiefile": None,  # Can be set via config later
                }

                url = f"https://www.youtube.com/watch?v={video_id}"

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                # Find the downloaded file
                downloaded_path = self.get_cached_path(video_id)

                if downloaded_path and downloaded_path.exists():
                    self.logger.info("Downloaded video %s to %s", video_id, downloaded_path)
                    if status_callback:
                        status_callback("ready", str(downloaded_path), None)
                else:
                    raise FileNotFoundError("Downloaded file not found")

            except Exception as e:
                error_msg = str(e)
                # Provide more helpful error messages
                if "403" in error_msg or "Forbidden" in error_msg:
                    error_msg = "YouTube blocked the download (403 Forbidden). This may be due to age restrictions, region blocking, or YouTube policy changes. Try updating yt-dlp: pip install --upgrade yt-dlp"
                elif "Private video" in error_msg:
                    error_msg = "Video is private or unavailable"
                elif "Video unavailable" in error_msg:
                    error_msg = "Video is unavailable or has been removed"

                self.logger.error(
                    "Error downloading video %s: %s", video_id, error_msg, exc_info=True
                )
                if status_callback:
                    status_callback("error", None, error_msg)
            finally:
                # Always release semaphore when download completes or fails
                self._download_semaphore.release()

        # Start download in background
        thread = threading.Thread(target=download_thread, daemon=True)
        thread.start()

        return None  # Download is async, path will be available via callback
