"""
Video library for kbox.

Provides a unified interface for video search, retrieval, and storage management.
Combines the functionality of VideoManager and CacheManager into a single facade.

Videos are identified by opaque IDs like "youtube:abc123". The source is an
internal detail managed by the library.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from .config_manager import ConfigManager

# Supported video file extensions
VIDEO_EXTENSIONS = [".mp4", ".mkv", ".webm"]


class VideoSource(ABC):
    """
    Abstract base class for video sources.

    A VideoSource is a pure fetcher - it searches external services and downloads
    videos to a directory provided by the VideoLibrary. It has no knowledge of
    caching or storage management.
    """

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
            The 'id' field contains the source-specific ID (not the opaque library ID).
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
    def download(self, video_id: str, output_dir: Path) -> Path:
        """
        Download a video to the specified directory (synchronous).

        Args:
            video_id: Source-specific video identifier
            output_dir: Directory to download into (will be created if needed)

        Returns:
            Path to the downloaded video file

        Raises:
            Exception: If download fails
        """
        ...


class VideoLibrary:
    """
    Unified interface for video search, retrieval, and storage.

    Videos are identified by opaque IDs like "youtube:abc123". The library handles:
    - Routing searches to appropriate sources
    - Managing per-video storage directories
    - LRU-based storage cleanup
    """

    def __init__(self, config_manager: "ConfigManager"):
        """
        Initialize VideoLibrary.

        Sources must be registered separately via register_source().

        Args:
            config_manager: ConfigManager for runtime config access
        """
        self.logger = logging.getLogger(__name__)
        self.config_manager = config_manager
        self._sources: Dict[str, VideoSource] = {}
        self._download_semaphore = threading.Semaphore(1)  # Limit concurrent downloads

        self.logger.info("VideoLibrary initialized")

    def register_source(self, source: VideoSource) -> None:
        """
        Register a video source.

        Args:
            source: VideoSource instance to register
        """
        self._sources[source.source_id] = source
        self.logger.info("Registered video source: %s", source.source_id)

    # =========================================================================
    # Video ID Handling
    # =========================================================================

    def _make_video_id(self, source: str, source_id: str) -> str:
        """
        Create an opaque video ID from source and source-specific ID.

        Args:
            source: Source identifier (e.g., "youtube")
            source_id: Source-specific video ID

        Returns:
            Opaque video ID (e.g., "youtube:abc123")
        """
        return f"{source}:{source_id}"

    def _parse_video_id(self, video_id: str) -> Tuple[str, str]:
        """
        Parse an opaque video ID into source and source-specific ID.

        Args:
            video_id: Opaque video ID (e.g., "youtube:abc123")

        Returns:
            Tuple of (source, source_id)

        Raises:
            ValueError: If video_id format is invalid
        """
        if ":" not in video_id:
            raise ValueError(f"Invalid video ID format: {video_id}")
        source, source_id = video_id.split(":", 1)
        return source, source_id

    # =========================================================================
    # Storage Management
    # =========================================================================

    @property
    def _base_directory(self) -> Path:
        """Get base storage directory from config, creating it if needed."""
        cache_dir = self.config_manager.get("cache_directory")
        if cache_dir is None:
            cache_dir = str(Path.home() / ".kbox" / "library")

        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)
        return cache_path

    def _get_video_directory(self, video_id: str) -> Path:
        """
        Get the storage directory for a video.

        Args:
            video_id: Opaque video ID

        Returns:
            Path to video's storage directory (e.g., /library/youtube/abc123/)
        """
        source, source_id = self._parse_video_id(video_id)
        return self._base_directory / source / source_id

    def _find_video_file(self, video_dir: Path) -> Optional[Path]:
        """
        Find the video file in a video directory.

        Args:
            video_dir: Path to video's storage directory

        Returns:
            Path to video file if found, None otherwise
        """
        if not video_dir.exists():
            return None

        for ext in VIDEO_EXTENSIONS:
            # Check for video.ext pattern (new style)
            video_file = video_dir / f"video{ext}"
            if video_file.exists():
                return video_file
            # Also check for source_id.ext pattern (legacy/yt-dlp default)
            for path in video_dir.glob(f"*{ext}"):
                if path.is_file():
                    return path

        return None

    def _touch_video(self, video_id: str) -> None:
        """
        Update modification time on a video's directory (for LRU tracking).

        Args:
            video_id: Opaque video ID
        """
        video_dir = self._get_video_directory(video_id)
        if video_dir.exists():
            try:
                video_dir.touch(exist_ok=True)
            except OSError as e:
                self.logger.warning("Failed to touch video directory %s: %s", video_dir, e)

    # =========================================================================
    # Discovery
    # =========================================================================

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Search for videos across all configured sources.

        Args:
            query: Search query
            max_results: Maximum results per source

        Returns:
            List of video dictionaries with opaque 'id' field

        Raises:
            Exception: If all configured sources fail
        """
        results = []
        errors = []
        sources_tried = 0

        for source_id, source in self._sources.items():
            if not source.is_configured():
                self.logger.debug("Skipping unconfigured source: %s", source_id)
                continue

            sources_tried += 1
            try:
                source_results = source.search(query, max_results)
                # Convert source-specific IDs to opaque library IDs
                for result in source_results:
                    result["id"] = self._make_video_id(source_id, result["id"])
                    result["source"] = source_id  # Keep for UI if needed
                results.extend(source_results)
            except Exception as e:
                self.logger.error("Error searching %s: %s", source_id, e, exc_info=True)
                errors.append((source_id, e))

        # If all sources failed, propagate the error
        if sources_tried > 0 and len(errors) == sources_tried:
            # Re-raise the first error (or last, depending on preference)
            raise errors[0][1]

        return results

    def get_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Get video info by opaque ID.

        Args:
            video_id: Opaque video ID (e.g., "youtube:abc123")

        Returns:
            Video dictionary with metadata, or None if not found

        Raises:
            ValueError: If video_id format is invalid
        """
        source, source_id = self._parse_video_id(video_id)

        source_obj = self._sources.get(source)
        if not source_obj:
            self.logger.warning("Unknown source: %s", source)
            return None

        result = source_obj.get_video_info(source_id)
        if result:
            result["id"] = video_id
            result["source"] = source
        return result

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

    # =========================================================================
    # Availability
    # =========================================================================

    def request(
        self,
        video_id: str,
        callback: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
    ) -> Optional[str]:
        """
        Request a video be made available for playback.

        If the video is already downloaded, returns the path immediately.
        Otherwise, starts an async download and returns None.

        Args:
            video_id: Opaque video ID
            callback: Status callback function(status, path, error)

        Returns:
            Path to video file if already available, None if download started

        Raises:
            ValueError: If video_id format is invalid
        """
        # Check if already available
        path = self.get_path(video_id)
        if path:
            self._touch_video(video_id)
            if callback:
                callback("ready", str(path), None)
            return str(path)

        # Parse and validate
        source, source_id = self._parse_video_id(video_id)

        source_obj = self._sources.get(source)
        if not source_obj:
            self.logger.error("Unknown source: %s", source)
            if callback:
                callback("error", None, f"Unknown source: {source}")
            return None

        # Create video directory
        video_dir = self._get_video_directory(video_id)
        video_dir.mkdir(parents=True, exist_ok=True)

        # Start download in background thread
        def download_thread():
            self._download_semaphore.acquire()
            try:
                if callback:
                    callback("downloading", None, None)
                downloaded_path = source_obj.download(source_id, video_dir)
                self.logger.info("Downloaded %s to %s", video_id, downloaded_path)
                if callback:
                    callback("ready", str(downloaded_path), None)
            except Exception as e:
                self.logger.error("Download failed for %s: %s", video_id, e, exc_info=True)
                if callback:
                    callback("error", None, str(e))
            finally:
                self._download_semaphore.release()

        thread = threading.Thread(target=download_thread, daemon=True)
        thread.start()
        return None

    def is_available(self, video_id: str) -> bool:
        """
        Check if a video is available for playback.

        Args:
            video_id: Opaque video ID

        Returns:
            True if video is downloaded and ready
        """
        return self.get_path(video_id, touch=False) is not None

    def get_path(self, video_id: str, touch: bool = True) -> Optional[Path]:
        """
        Get path to video file if available.

        Args:
            video_id: Opaque video ID
            touch: If True, update directory mtime for LRU tracking

        Returns:
            Path to video file if exists, None otherwise

        Raises:
            ValueError: If video_id format is invalid
        """
        video_dir = self._get_video_directory(video_id)

        path = self._find_video_file(video_dir)
        if path and touch:
            self._touch_video(video_id)
        return path

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def manage_storage(self, keep: Optional[Set[str]] = None) -> int:
        """
        Clean up storage to stay within size limit, using LRU eviction.

        Videos in the 'keep' set will not be evicted.

        Args:
            keep: Set of opaque video IDs to protect from eviction

        Returns:
            Number of videos deleted
        """
        if keep is None:
            keep = set()

        # Get max size from config (in GB)
        max_size_gb = self.config_manager.get_int("cache_max_size_gb", 10)
        max_size_bytes = max_size_gb * 1024 * 1024 * 1024

        # Get all video directories with their sizes and mtimes
        video_dirs = self._get_all_video_dirs()
        total_size = sum(size for _, size, _ in video_dirs)

        if total_size <= max_size_bytes:
            self.logger.debug(
                "Storage size %.2f GB within limit %.2f GB",
                total_size / (1024**3),
                max_size_gb,
            )
            return 0

        self.logger.info(
            "Storage size %.2f GB exceeds limit %.2f GB, starting cleanup",
            total_size / (1024**3),
            max_size_gb,
        )

        deleted_count = 0

        # Evict oldest videos until under limit
        for video_id, size, mtime in video_dirs:
            if total_size <= max_size_bytes:
                break

            if video_id in keep:
                self.logger.debug("Skipping protected video: %s", video_id)
                continue

            try:
                video_dir = self._get_video_directory(video_id)
                # Delete all files in directory then the directory itself
                for file in video_dir.iterdir():
                    file.unlink()
                video_dir.rmdir()
                total_size -= size
                deleted_count += 1
                self.logger.info(
                    "Evicted video: %s (%.2f MB, age: %.1f hours)",
                    video_id,
                    size / (1024**2),
                    (time.time() - mtime) / 3600,
                )
            except OSError as e:
                self.logger.warning("Failed to delete video %s: %s", video_id, e)

        if deleted_count > 0:
            self.logger.info(
                "Storage cleanup complete: deleted %d videos, new size %.2f GB",
                deleted_count,
                total_size / (1024**3),
            )

        return deleted_count

    def _get_all_video_dirs(self) -> List[Tuple[str, int, float]]:
        """
        Get list of all video directories with their sizes and modification times.

        Returns:
            List of (video_id, size_bytes, mtime) tuples, sorted by mtime (oldest first)
        """
        video_dirs = []

        base = self._base_directory
        if not base.exists():
            return []

        # Iterate source directories
        for source_dir in base.iterdir():
            if not source_dir.is_dir():
                continue
            source = source_dir.name

            # Iterate video directories within source
            for video_dir in source_dir.iterdir():
                if not video_dir.is_dir():
                    continue
                source_id = video_dir.name
                video_id = self._make_video_id(source, source_id)

                try:
                    # Calculate total size of directory contents
                    total_size = sum(f.stat().st_size for f in video_dir.iterdir() if f.is_file())
                    mtime = video_dir.stat().st_mtime
                    video_dirs.append((video_id, total_size, mtime))
                except OSError as e:
                    self.logger.warning("Failed to stat video directory %s: %s", video_dir, e)

        # Sort by mtime, oldest first (for LRU eviction)
        video_dirs.sort(key=lambda x: x[2])
        return video_dirs

    def get_storage_stats(self) -> dict:
        """
        Get statistics about storage usage.

        Returns:
            Dictionary with storage statistics
        """
        video_dirs = self._get_all_video_dirs()
        total_size = sum(size for _, size, _ in video_dirs)
        max_size_gb = self.config_manager.get_int("cache_max_size_gb", 10)

        return {
            "video_count": len(video_dirs),
            "total_size_bytes": total_size,
            "total_size_gb": total_size / (1024**3),
            "max_size_gb": max_size_gb,
            "usage_percent": (total_size / (max_size_gb * 1024**3) * 100) if max_size_gb > 0 else 0,
        }
