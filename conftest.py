"""
Pytest configuration for kbox tests.

Provides:
- @pytest.mark.gstreamer marker for tests requiring GStreamer
- @pytest.mark.e2e marker for Playwright browser tests
"""


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "gstreamer: marks tests as requiring GStreamer (run with -m gstreamer)"
    )
