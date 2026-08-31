"""
Database package for kbox.

Handles SQLite database initialization, schema creation, and connection
management. Includes repository classes that encapsulate all SQL operations.
"""

from .codecs import _decode_metadata, _decode_settings, _encode_metadata, _encode_settings
from .config import ConfigRepository
from .events import EventRepository
from .favorites import FavoriteRepository
from .history import HistoryRepository
from .queue import QueueRepository
from .schema import Database
from .sessions import SessionRepository
from .users import UserRepository

__all__ = [
    "Database",
    "UserRepository",
    "ConfigRepository",
    "HistoryRepository",
    "FavoriteRepository",
    "QueueRepository",
    "SessionRepository",
    "EventRepository",
    "_decode_metadata",
    "_decode_settings",
    "_encode_metadata",
    "_encode_settings",
]
