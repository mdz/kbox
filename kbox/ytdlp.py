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

from .video_library import VideoProvider, karaoke_search_query

if TYPE_CHECKING:
    from .config_manager import ConfigManager


# Most of yt-dlp's YouTube errors pass through YouTube's own wording
# (playabilityStatus.reason), which changes without a yt-dlp release, so
# match short lowercase fragments rather than whole sentences. The message
# also carries the video ID, so a bare "403" would match IDs too.
# First match wins; order the specific cases before the general ones.
_DOWNLOAD_ERRORS: List[tuple[tuple[str, ...], str]] = [
    (
        ("http error 403", "forbidden"),
        "YouTube blocked the download (403 Forbidden). Try updating yt-dlp.",
    ),
    (("not a bot",), "YouTube asked for a bot check. Try again in a few minutes."),
    (
        ("try again later",),
        "YouTube is limiting downloads right now. Try again in a few minutes.",
    ),
    (("confirm your age", "age-restricted"), "Video is age-restricted"),
    (("in your country",), "Video is not available in this country"),
    (("private video",), "Video is private"),
    (
        ("requested format is not available", "only images are available"),
        "YouTube didn't offer a playable version this time. Try again.",
    ),
    (("unavailable", "has been removed"), "Video is unavailable or has been removed"),
]


def _friendly_download_error(message: str) -> str:
    """Turn a yt-dlp download error into a short message for the queue."""
    lowered = message.lower()
    for needles, friendly in _DOWNLOAD_ERRORS:
        if any(needle in lowered for needle in needles):
            return friendly
    return message


def download_format(max_height: int) -> str:
    """yt-dlp format selector for a song download, capped at ``max_height``.

    Prefers H.264 with AAC in mp4, which the Pi decodes in software with
    openh264dec and faad. Left to rank formats itself, yt-dlp prefers AV1,
    and the Docker image's only AV1 decoder (libaom's av1dec) is the slowest
    one in it, so AV1 is kept out of the fallbacks too.
    """
    return (
        f"bestvideo[height<={max_height}][vcodec^=avc1]+bestaudio[acodec^=mp4a]"
        f"/bestvideo[height<={max_height}][vcodec^=avc1]+bestaudio"
        f"/best[height<={max_height}][vcodec^=avc1]"
        f"/bestvideo[height<={max_height}][vcodec!^=av01]+bestaudio"
        f"/best"
    )


class _YtdlpLogAdapter:
    """Routes yt-dlp's internal messages through a stdlib logger instead of stdout."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def debug(self, msg: str) -> None:
        # yt-dlp prefixes its own verbose/internal messages with "[debug] ";
        # everything else (progress, status) is user-facing info.
        if msg.startswith("[debug] "):
            self._logger.debug(msg)
        else:
            self._logger.info(msg)

    def info(self, msg: str) -> None:
        self._logger.info(msg)

    def warning(self, msg: str) -> None:
        self._logger.warning(msg)

    def error(self, msg: str) -> None:
        self._logger.error(msg)


class YtDlpClient(VideoProvider):
    """Client for YouTube operations via yt-dlp."""

    def __init__(self, config_manager: "ConfigManager"):
        self.logger = logging.getLogger(__name__)
        self.config_manager = config_manager
        self._ydl_logger = _YtdlpLogAdapter(self.logger)

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
        Search YouTube, normalizing the query so it carries exactly one
        "karaoke" token (see karaoke_search_query).

        Uses extract_flat for fast results (~1-2s) at the cost of missing
        some fields like full description.

        Args:
            query: Search query
            max_results: Maximum number of results

        Returns:
            List of video result dicts.
        """
        search_query = karaoke_search_query(query)
        self.logger.debug("Searching YouTube via yt-dlp: %s", search_query)

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "logger": self._ydl_logger,
        }

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
                        # Flat search entries can carry null for any of these,
                        # so fall through to the next and never emit None.
                        "channel": entry.get("channel")
                        or entry.get("uploader")
                        or entry.get("uploader_id")
                        or "",
                        "duration_seconds": (
                            int(entry["duration"]) if entry.get("duration") else None
                        ),
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
        ydl_opts = {"quiet": True, "no_warnings": True, "logger": self._ydl_logger}

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

            # No player_client override: yt-dlp's default clients are the ones it
            # keeps working. The old android,web pin got only progressive 360p, and
            # failed with "Only images are available" whenever YouTube stripped
            # android's URLs, because the web client offers nothing playable.
            ydl_opts = {
                "format": download_format(max_res),
                "outtmpl": output_template,
                # With a logger set, yt-dlp routes all output (including progress)
                # through it regardless of quiet, so quiet just avoids a raw-print
                # fallback; warnings still need no_warnings=False to surface.
                "quiet": True,
                "no_warnings": False,
                "logger": self._ydl_logger,
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
            self.logger.error("Error downloading video %s: %s", video_id, e)
            raise RuntimeError(_friendly_download_error(str(e))) from e
