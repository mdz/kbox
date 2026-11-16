"""Filesystem locations used by kbox."""

import os
from pathlib import Path

DATA_DIR_ENV = "KBOX_DATA_DIR"


def get_data_dir() -> Path:
    """Return kbox's data directory, creating it if needed.

    Defaults to ``~/.kbox``. Set ``KBOX_DATA_DIR`` (or pass ``--data-dir`` to
    ``python -m kbox.main``) to use a different directory, e.g. an isolated
    scratch instance for development that leaves the real database alone.

    The result is always absolute: a relative override is resolved against the
    current directory. Everything under it (the database, the library, the
    paths stored for downloaded files) inherits that, and GStreamer cannot
    open a relative ``file://`` URI.
    """
    override = os.environ.get(DATA_DIR_ENV)
    path = Path(override).expanduser() if override else Path.home() / ".kbox"
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
