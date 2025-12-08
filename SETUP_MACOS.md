# macOS Setup Guide

## Prerequisites

1. **Install Homebrew** (if not already installed):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. **Install GStreamer and dependencies**:
   ```bash
   brew install gstreamer glib pygobject3
   ```

3. **Install Python dependencies**:
   ```bash
   uv pip install pygobject pycairo
   ```

## Running kbox

Due to macOS's library path restrictions, you need to set environment variables for GStreamer to work properly. We provide a wrapper script that does this automatically:

```bash
./run_kbox.sh
```

### Manual Setup (Alternative)

If you prefer to run manually, set these environment variables:

```bash
export DYLD_LIBRARY_PATH="$(brew --prefix glib)/lib:$(brew --prefix gstreamer)/lib:$DYLD_LIBRARY_PATH"
export GI_TYPELIB_PATH="$(brew --prefix gstreamer)/share/gir-1.0:$GI_TYPELIB_PATH"
export GST_PLUGIN_PATH="$(brew --prefix gstreamer)/lib/gstreamer-1.0:$GST_PLUGIN_PATH"

uv run python -m kbox.main
```

## Troubleshooting

### "No module named 'gi'"
- Install pygobject: `uv pip install pygobject`

### "Failed to load shared library 'libglib-2.0.0.dylib'"
- Make sure you're using the wrapper script `./run_kbox.sh`
- Or manually set `DYLD_LIBRARY_PATH` as shown above

### "Could not locate g_option_error_quark"
- This is a library path issue. Use the wrapper script or set environment variables manually.

## Note on Python Version

The project uses Python 3.9.6 by default (from Xcode). For better compatibility, consider using Python 3.10+ via Homebrew:

```bash
brew install python@3.12
uv python install 3.12
```

Then use: `uv run --python 3.12 python -m kbox.main`

