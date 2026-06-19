"""
E2E test fixtures: live uvicorn server + mobile browser contexts.

Architecture:
  Playwright browser ──HTTP──▶ uvicorn (background thread)
                                  │
                                  ▼
                            create_app(...)   ← real FastAPI app
                                  │
      real: QueueManager, UserManager, HistoryManager, ConfigManager, temp SQLite DB
      mocked: StreamingController, PlaybackController, VideoLibrary, SuggestionEngine

Fixture scope is function (fresh server + DB per test). Flakiness from the
content-monitor thread is prevented by build_test_app setting has_provider=False
on the video_library mock, which makes _process_pending_content() a no-op.
"""

import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

# test/ is on sys.path when pytest runs from repo root with no __init__.py in test/
sys.path.insert(0, str(Path(__file__).parent.parent))
from support.app_factory import build_test_app  # noqa: E402

MOBILE_DEVICES = ["Pixel 5", "iPhone 13"]


@pytest.fixture
def live_app(tmp_path):
    """Spin up a real uvicorn server with a fresh temp DB and return its base URL."""
    assert Path.cwd().name == "kbox" or (Path.cwd() / "kbox").is_dir(), (
        "pytest must run from repo root so static/template paths resolve correctly"
    )

    cache = tmp_path / "cache"
    cache.mkdir()
    app, db, queue = build_test_app(str(tmp_path / "test.db"), str(cache))

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Poll for startup, then read the OS-assigned port from the bound socket.
    for _ in range(200):
        if server.started and server.servers:
            break
        time.sleep(0.02)
    else:
        raise RuntimeError("uvicorn did not start within 4 seconds")

    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    httpx.get(base + "/", timeout=2.0)

    yield base

    # Teardown order: stop DB-touching worker first, then server, then DB.
    queue.stop_content_monitor()
    server.should_exit = True
    thread.join(timeout=5)
    db.close()


@pytest.fixture(params=MOBILE_DEVICES, ids=["mobile-chrome", "mobile-safari"])
def mobile_page(request):
    """Parametrized fixture yielding a Playwright page at phone dimensions.

    Runs each test on Chromium (Pixel 5) and WebKit (iPhone 13) so both
    engines are covered automatically.
    """
    device_name = request.param
    with sync_playwright() as p:
        device = p.devices[device_name]
        engine = p.webkit if "iPhone" in device_name else p.chromium
        browser = engine.launch()
        context = browser.new_context(**device)
        page = context.new_page()
        yield page
        context.close()
        browser.close()
