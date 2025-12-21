#!/bin/bash
# Test runner that uses system Python with GStreamer
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEST_VENV="$PROJECT_DIR/.test-venv"

# Find Python with GStreamer
if [[ "$OSTYPE" == "darwin"* ]]; then
    PYTHON="/opt/homebrew/opt/python@3.13/bin/python3"
    if [ ! -x "$PYTHON" ]; then
        echo "Error: Python 3.13 not found at $PYTHON"
        exit 1
    fi
    if ! $PYTHON -c "import gi" 2>/dev/null; then
        echo "Error: GStreamer Python bindings not found"
        echo "Install with: brew install pygobject3 gstreamer"
        exit 1
    fi
else
    PYTHON="python3"
    if ! $PYTHON -c "import gi" 2>/dev/null; then
        echo "Error: GStreamer Python bindings not found"
        echo "Install with: sudo apt install python3-gi python3-gst-1.0"
        exit 1
    fi
fi

# Create test venv with system site packages if needed
if [ ! -d "$TEST_VENV" ]; then
    echo "Creating test venv with system site packages..."
    $PYTHON -m venv --system-site-packages "$TEST_VENV"
    "$TEST_VENV/bin/pip" install pytest
fi

# Run tests
export PYTHONPATH="$PROJECT_DIR:$PYTHONPATH"
exec "$TEST_VENV/bin/pytest" "$@"
