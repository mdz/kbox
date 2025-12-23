# Running Tests

## Quick Start

```bash
# Run all non-GStreamer tests (works everywhere)
uv run pytest -m "not gstreamer"

# Run all tests (requires GStreamer)
uv run pytest

# Run pre-commit hooks manually
uv run pre-commit run --all-files
```

## Pre-commit Hooks

Pre-commit hooks are configured to run automatically on each commit:
- **ruff check**: Linting with auto-fix
- **ruff format**: Code formatting
- **mypy**: Type checking
- **pytest**: Run tests (non-GStreamer only)

Install hooks (one-time setup):
```bash
uv run pre-commit install
```

## Prerequisites

The integration tests require GStreamer and its Python bindings to be installed on your system.

**macOS:**
```bash
brew install pygobject3 gstreamer glib
```

**Linux/Raspberry Pi:**
```bash
sudo apt install python3-gi python3-gst-1.0
```

## Running Tests

Use uv to run tests:

```bash
uv run pytest
```

Run specific tests:
```bash
uv run pytest test/test_streaming.py::test_init_creates_pipeline_in_ready_state -v
```

Run with verbose output:
```bash
uv run pytest -v
```

Skip GStreamer tests (useful on systems without GStreamer):
```bash
uv run pytest -m "not gstreamer"
```

## What the Tests Verify

The integration tests verify **real GStreamer pipeline stability** including:

- Pipeline initialization and state transitions (READY → PLAYING → PAUSED)
- Persistent playbin architecture with custom sink bins
- Pitch shift element integration and persistence across songs
- Stress testing with rapid start/stop cycles
- Position tracking and seeking
- End-of-stream (EOS) callback handling
- Error handling and recovery
- Pipeline cleanup

## Why These Tests Are Critical

These tests use **real GStreamer** (not mocks) to catch:
- State transition bugs that only appear with actual hardware/sinks
- Memory leaks in pipeline lifecycle
- Race conditions in async state changes
- GStreamer element incompatibilities
- Platform-specific issues (macOS vs Linux)

**Tests will FAIL if GStreamer is not available** - this is intentional to ensure pipeline stability is actually being verified.

## Docker Testing

Run tests inside Docker (includes GStreamer):
```bash
docker compose run --rm kbox pytest
```

Build and verify Docker image:
```bash
docker compose build
docker compose up
```

## CI/CD Integration

For GitHub Actions or other CI systems:

```yaml
- name: Install GStreamer
  run: |
    sudo apt-get update
    sudo apt-get install -y python3-gi python3-gst-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good

- name: Install uv
  uses: astral-sh/setup-uv@v4

- name: Run Tests  
  run: uv run pytest -v
```
