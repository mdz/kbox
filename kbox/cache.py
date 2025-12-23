"""
Cache management for kbox.

Provides LRU-based cache eviction for downloaded video files.
Source-agnostic design supports multiple video sources (YouTube, etc.).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from .config_manager import ConfigManager


# Supported video file extensions
VIDEO_EXTENSIONS = [".mp4", ".mkv", ".webm"]


class CacheManager:
    """Manages video cache with LRU eviction."""

    def __init__(self, config_manager: "ConfigManager"):
        """
        Initialize CacheManager.

        Args:
            config_manager: ConfigManager for runtime config access
        """
        self.logger = logging.getLogger(__name__)
        self.config_manager = config_manager
        self.logger.info("CacheManager initialized")

    @property
    def base_directory(self) -> Path:
        """Get base cache directory from config, creating it if needed."""
        cache_dir = self.config_manager.get("cache_directory")
        if cache_dir is None:
            cache_dir = str(Path.home() / ".kbox" / "cache")

        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)
        return cache_path

    def get_source_directory(self, source: str) -> Path:
        """
        Get cache directory for a specific source.

        Args:
            source: Source identifier (e.g., "youtube", "vimeo")

        Returns:
            Path to source-specific cache directory (created if needed)
        """
        source_path = self.base_directory / source
        source_path.mkdir(parents=True, exist_ok=True)
        return source_path

    def get_file_path(self, source: str, file_id: str, touch: bool = True) -> Optional[Path]:
        """
        Get path to a cached file if it exists.

        Args:
            source: Source identifier (e.g., "youtube")
            file_id: Source-specific file identifier (e.g., video ID)
            touch: If True, update file mtime for LRU tracking (default True)

        Returns:
            Path to cached file if exists, None otherwise
        """
        source_dir = self.get_source_directory(source)
        for ext in VIDEO_EXTENSIONS:
            path = source_dir / f"{file_id}{ext}"
            if path.exists():
                if touch:
                    self._touch_file(path)
                return path
        return None

    def is_cached(self, source: str, file_id: str) -> bool:
        """
        Check if a file is cached.

        Args:
            source: Source identifier
            file_id: Source-specific file identifier

        Returns:
            True if file is cached
        """
        # Don't touch file for simple existence check
        return self.get_file_path(source, file_id, touch=False) is not None

    def get_output_template(self, source: str, file_id: str) -> str:
        """
        Get output path template for downloading a file.

        Args:
            source: Source identifier
            file_id: Source-specific file identifier

        Returns:
            Path template with %(ext)s placeholder for downloaders
        """
        source_dir = self.get_source_directory(source)
        return str(source_dir / f"{file_id}.%(ext)s")

    # =========================================================================
    # Cache Cleanup
    # =========================================================================

    def _get_cache_files(self, source: Optional[str] = None) -> List[Tuple[Path, int, float]]:
        """
        Get list of all cached video files with their sizes and modification times.

        Args:
            source: If provided, only scan this source's directory.
                    If None, scan all source directories.

        Returns:
            List of (path, size_bytes, mtime) tuples, sorted by mtime (oldest first)
        """
        cache_files = []

        if source:
            # Scan specific source directory
            directories = [self.get_source_directory(source)]
        else:
            # Scan all subdirectories in base cache
            directories = [d for d in self.base_directory.iterdir() if d.is_dir()]

        for directory in directories:
            for ext in VIDEO_EXTENSIONS:
                for path in directory.glob(f"*{ext}"):
                    try:
                        stat = path.stat()
                        cache_files.append((path, stat.st_size, stat.st_mtime))
                    except OSError as e:
                        self.logger.warning("Failed to stat cache file %s: %s", path, e)

        # Sort by mtime, oldest first (for LRU eviction)
        cache_files.sort(key=lambda x: x[2])
        return cache_files

    def _touch_file(self, path: Path) -> None:
        """
        Update modification time on a file (for LRU tracking).

        Args:
            path: Path to the file to touch
        """
        try:
            path.touch(exist_ok=True)
        except OSError as e:
            self.logger.warning("Failed to touch cache file %s: %s", path, e)

    def _extract_source_and_id(self, path: Path) -> Optional[Tuple[str, str]]:
        """
        Extract source and file ID from a cache file path.

        Args:
            path: Path to cache file (e.g., /cache/youtube/abc123.mp4)

        Returns:
            Tuple of (source, file_id) or None if extraction fails
        """
        file_id = path.stem if path.stem else None
        source = path.parent.name if path.parent else None
        if file_id and source:
            return (source, file_id)
        return None

    def cleanup(self, protected: Optional[Set[Tuple[str, str]]] = None) -> int:
        """
        Clean up cache to stay within size limit, using LRU eviction.

        Files identified by (source, file_id) tuples in protected will not be evicted.
        Cleanup runs across ALL source directories.

        Args:
            protected: Set of (source, file_id) tuples that should not be deleted

        Returns:
            Number of files deleted
        """
        if protected is None:
            protected = set()

        # Get max cache size from config (in GB)
        max_size_gb = self.config_manager.get_int("cache_max_size_gb", 10)
        max_size_bytes = max_size_gb * 1024 * 1024 * 1024

        cache_files = self._get_cache_files()
        total_size = sum(size for _, size, _ in cache_files)

        if total_size <= max_size_bytes:
            self.logger.debug(
                "Cache size %.2f GB within limit %.2f GB",
                total_size / (1024**3),
                max_size_gb,
            )
            return 0

        self.logger.info(
            "Cache size %.2f GB exceeds limit %.2f GB, starting cleanup",
            total_size / (1024**3),
            max_size_gb,
        )

        deleted_count = 0

        # Evict oldest files until under limit
        for path, size, mtime in cache_files:
            if total_size <= max_size_bytes:
                break

            source_and_id = self._extract_source_and_id(path)
            if source_and_id and source_and_id in protected:
                self.logger.debug("Skipping protected file: %s/%s", *source_and_id)
                continue

            try:
                path.unlink()
                total_size -= size
                deleted_count += 1
                self.logger.info(
                    "Evicted cache file: %s (%.2f MB, age: %.1f hours)",
                    path.name,
                    size / (1024**2),
                    (time.time() - mtime) / 3600,
                )
            except OSError as e:
                self.logger.warning("Failed to delete cache file %s: %s", path, e)

        if deleted_count > 0:
            self.logger.info(
                "Cache cleanup complete: deleted %d files, new size %.2f GB",
                deleted_count,
                total_size / (1024**3),
            )

        return deleted_count

    def get_cache_stats(self) -> dict:
        """
        Get statistics about the cache.

        Returns:
            Dictionary with cache statistics
        """
        cache_files = self._get_cache_files()
        total_size = sum(size for _, size, _ in cache_files)
        max_size_gb = self.config_manager.get_int("cache_max_size_gb", 10)

        return {
            "file_count": len(cache_files),
            "total_size_bytes": total_size,
            "total_size_gb": total_size / (1024**3),
            "max_size_gb": max_size_gb,
            "usage_percent": (total_size / (max_size_gb * 1024**3) * 100) if max_size_gb > 0 else 0,
        }
