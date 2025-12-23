"""
Video source abstraction for kbox.

Provides a source-agnostic interface for video search, retrieval, and caching.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from .cache import CacheManager
    from .config_manager import ConfigManager


class VideoSource(ABC):
    """Abstract base class for video sources."""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Unique identifier for this source (e.g., 'youtube')."""
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if this source is properly configured and ready to use."""
        ...

    @abstractmethod
    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Search for videos.

        Args:
            query: Search query
            max_results: Maximum number of results to return

        Returns:
            List of video dictionaries with keys: id, title, thumbnail, duration_seconds, etc.
        """
        ...

    @abstractmethod
    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific video.

        Args:
            video_id: Source-specific video identifier

        Returns:
            Video dictionary with metadata, or None if not found
        """
        ...

    @abstractmethod
    def download(
        self,
        video_id: str,
        queue_item_id: int,
        status_callback: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
    ) -> Optional[str]:
        """
        Download a video.

        Args:
            video_id: Source-specific video identifier
            queue_item_id: Queue item ID (for callback tracking)
            status_callback: Callback function(status, path, error) for status updates

        Returns:
            Path to downloaded file if already cached, None if download started async
        """
        ...

    @abstractmethod
    def get_cached_path(self, video_id: str, touch: bool = True) -> Optional[Path]:
        """
        Get path to a cached video file if it exists.

        Args:
            video_id: Source-specific video identifier
            touch: If True, update file mtime for LRU tracking

        Returns:
            Path to video file if exists, None otherwise
        """
        ...

    @abstractmethod
    def is_cached(self, video_id: str) -> bool:
        """
        Check if a video is cached.

        Args:
            video_id: Source-specific video identifier

        Returns:
            True if video is cached
        """
        ...


class VideoManager:
    """
    Facade for managing multiple video sources.

    Provides unified search, download, and cache access across all registered sources.
    Automatically instantiates and registers all known video sources.
    """

    def __init__(self, config_manager: "ConfigManager", cache_manager: "CacheManager"):
        """
        Initialize VideoManager and register all video sources.

        Args:
            config_manager: ConfigManager for runtime config access
            cache_manager: CacheManager for cache operations
        """
        self.logger = logging.getLogger(__name__)
        self.config_manager = config_manager
        self.cache_manager = cache_manager
        self._sources: Dict[str, VideoSource] = {}

        # Register all available sources
        self._register_sources()

        self.logger.info("VideoManager initialized")

    def _register_sources(self) -> None:
        """Instantiate and register all available video sources."""
        # Import here to avoid circular imports
        from .youtube import YouTubeSource

        # Register YouTube source
        youtube_source = YouTubeSource(self.config_manager, self.cache_manager)
        self._register_source(youtube_source)

        # Future sources can be added here:
        # from .vimeo import VimeoSource
        # self._register_source(VimeoSource(self.config_manager, self.cache_manager))

    def _register_source(self, source: VideoSource) -> None:
        """
        Register a video source.

        Args:
            source: VideoSource instance to register
        """
        self._sources[source.source_id] = source
        self.logger.info("Registered video source: %s", source.source_id)

    def get_source(self, source_id: str) -> Optional[VideoSource]:
        """
        Get a registered source by ID.

        Args:
            source_id: Source identifier

        Returns:
            VideoSource instance or None if not found
        """
        return self._sources.get(source_id)

    def is_source_configured(self, source_id: str) -> bool:
        """
        Check if a source is registered and configured.

        Args:
            source_id: Source identifier

        Returns:
            True if source is registered and configured
        """
        source = self._sources.get(source_id)
        return source is not None and source.is_configured()

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Search for videos across all configured sources.

        Args:
            query: Search query
            max_results: Maximum results per source

        Returns:
            List of video dictionaries, each tagged with 'source' field
        """
        results = []

        for source_id, source in self._sources.items():
            if not source.is_configured():
                self.logger.debug("Skipping unconfigured source: %s", source_id)
                continue

            try:
                source_results = source.search(query, max_results)
                # Tag each result with its source
                for result in source_results:
                    result["source"] = source_id
                results.extend(source_results)
            except Exception as e:
                self.logger.error("Error searching %s: %s", source_id, e, exc_info=True)

        return results

    def get_video_info(self, source: str, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Get video info from a specific source.

        Args:
            source: Source identifier
            video_id: Source-specific video identifier

        Returns:
            Video dictionary with metadata, or None if not found
        """
        source_obj = self._sources.get(source)
        if not source_obj:
            self.logger.warning("Unknown source: %s", source)
            return None

        result = source_obj.get_video_info(video_id)
        if result:
            result["source"] = source
        return result

    def download(
        self,
        source: str,
        video_id: str,
        queue_item_id: int,
        status_callback: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
    ) -> Optional[str]:
        """
        Download a video from a specific source.

        Args:
            source: Source identifier
            video_id: Source-specific video identifier
            queue_item_id: Queue item ID (for callback tracking)
            status_callback: Callback function(status, path, error) for status updates

        Returns:
            Path to downloaded file if already cached, None if download started async
        """
        source_obj = self._sources.get(source)
        if not source_obj:
            self.logger.error("Unknown source: %s", source)
            if status_callback:
                status_callback("error", None, f"Unknown source: {source}")
            return None

        return source_obj.download(video_id, queue_item_id, status_callback)

    def get_cached_path(self, source: str, video_id: str, touch: bool = True) -> Optional[Path]:
        """
        Get path to a cached video file.

        Args:
            source: Source identifier
            video_id: Source-specific video identifier
            touch: If True, update file mtime for LRU tracking

        Returns:
            Path to video file if exists, None otherwise
        """
        source_obj = self._sources.get(source)
        if not source_obj:
            self.logger.warning("Unknown source: %s", source)
            return None

        return source_obj.get_cached_path(video_id, touch=touch)

    def is_cached(self, source: str, video_id: str) -> bool:
        """
        Check if a video is cached.

        Args:
            source: Source identifier
            video_id: Source-specific video identifier

        Returns:
            True if video is cached
        """
        source_obj = self._sources.get(source)
        if not source_obj:
            return False

        return source_obj.is_cached(video_id)

    def cleanup_cache(self, protected: Optional[set] = None) -> int:
        """
        Clean up cache to stay within size limit, using LRU eviction.

        Args:
            protected: Set of (source, source_id) tuples that should not be deleted

        Returns:
            Number of files deleted
        """
        return self.cache_manager.cleanup(protected)
