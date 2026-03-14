"""
YouTube video source for kbox.

Composes a YouTube Data API client with an optional fallback to implement the
VideoSearchSource interface.  The API is preferred for search and metadata when
available; the fallback (typically yt-dlp) is used when the API is unavailable
or fails.

This module has no knowledge of downloading or content provision.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .video_library import VideoSearchSource
from .youtube import YouTubeAPI


class YouTubeSource(VideoSearchSource):
    """VideoSearchSource backed by a YouTube API client with optional fallback."""

    def __init__(self, youtube_api: YouTubeAPI, fallback=None):
        self.logger = logging.getLogger(__name__)
        self._api = youtube_api
        self._fallback = fallback

    @property
    def source_id(self) -> str:
        return "youtube"

    def is_configured(self) -> bool:
        return True

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """Search using the API first, falling back if configured."""
        if self._api.is_available():
            try:
                return self._api.search(query, max_results)
            except Exception as e:
                self.logger.warning("YouTube API search failed: %s", e)

        if self._fallback is not None:
            try:
                return self._fallback.search(query, max_results)
            except Exception as e:
                self.logger.warning("Fallback search also failed: %s", e)

        return []

    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata using the API first, falling back if configured."""
        if self._api.is_available():
            try:
                result = self._api.get_video_info(video_id)
                if result:
                    return result
            except Exception as e:
                self.logger.warning("YouTube API info failed for %s: %s", video_id, e)

        if self._fallback is not None:
            try:
                return self._fallback.get_video_info(video_id)
            except Exception as e:
                self.logger.warning("Fallback info also failed for %s: %s", video_id, e)

        return None
