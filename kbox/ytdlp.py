"""
yt-dlp client for YouTube video search, metadata retrieval, and content provision.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import yt_dlp

from .video_library import VideoProvider

if TYPE_CHECKING:
    from .config_manager import ConfigManager


class YtDlpClient(VideoProvider):
    """Client for YouTube operations via yt-dlp."""

    def __init__(self, config_manager: "ConfigManager"):
        self.logger = logging.getLogger(__name__)
        self.config_manager = config_manager

        self._min_interval = 2.0  # seconds between calls
        self._last_call: float = 0.0
        self._lock = threading.Lock()

        self.logger.info("YtDlpClient initialized")

    def _rate_limit(self) -> None:
        """Sleep if needed to enforce minimum interval between calls."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                self.logger.debug("Rate limiting yt-dlp: sleeping %.1fs", wait)
                time.sleep(wait)
            self._last_call = time.monotonic()

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Search YouTube, automatically appending "karaoke" to the query.

        Uses extract_flat for fast results (~1-2s) at the cost of missing
        some fields like full description.

        Args:
            query: Search query
            max_results: Maximum number of results

        Returns:
            List of video result dicts.
        """
        search_query = f"{query} karaoke"
        self.logger.debug("Searching YouTube via yt-dlp: %s", search_query)

        ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}

        try:
            self._rate_limit()
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch{max_results}:{search_query}", download=False)

            results = []
            for entry in (info or {}).get("entries", []):
                thumbnail = entry.get("thumbnail", "") or entry.get("thumbnails", [{}])[-1].get(
                    "url", ""
                )
                results.append(
                    {
                        "id": entry.get("id", entry.get("url", "")),
                        "title": entry.get("title", ""),
                        "thumbnail": thumbnail,
                        "channel": entry.get("channel", "") or entry.get("uploader", ""),
                        "duration_seconds": entry.get("duration"),
                        "description": (entry.get("description") or "")[:200],
                    }
                )

            self.logger.info(
                "Found %s videos via yt-dlp for query: %s",
                len(results),
                search_query,
            )
            return results

        except Exception as e:
            self.logger.error("yt-dlp search error: %s", e, exc_info=True)
            return []

    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Get metadata for a specific video.

        Args:
            video_id: YouTube video ID.

        Returns:
            Video metadata dict, or None on failure.
        """
        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {"quiet": True, "no_warnings": True}

        try:
            self._rate_limit()
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

    def provide(self, video_id: str, output_dir: Path) -> Path:
        """
        Make a video available as a local file (synchronous).

        Args:
            video_id: YouTube video ID.
            output_dir: Directory to place the file in.

        Returns:
            Path to the video file.

        Raises:
            RuntimeError: If the video cannot be fetched.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            os.nice(10)
        except (OSError, AttributeError):
            pass

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

            self._rate_limit()
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
