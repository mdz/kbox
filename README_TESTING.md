# Running Tests

## Prerequisites

The integration tests require GStreamer and its Python bindings to be installed on your system.

**macOS:**
```bash
brew install pygobject3 gstreamer
```

**Linux/Raspberry Pi:**
```bash
sudo apt install python3-gi python3-gst-1.0 python3-pytest
```

## Running Tests

Use the test runner script which automatically uses system Python with GStreamer:

```bash
./test/run_tests.sh
```

Run specific tests:
```bash
./test/run_tests.sh test/test_streaming.py::test_init_creates_pipeline_in_ready_state -v
```

Run with verbose output:
```bash
./test/run_tests.sh -v
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

## CI/CD Integration

For GitHub Actions or other CI systems:

```yaml
- name: Install GStreamer
  run: |
    sudo apt-get update
    sudo apt-get install -y python3-gi python3-gst-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good

- name: Run Tests  
  run: ./test/run_tests.sh -v
```


