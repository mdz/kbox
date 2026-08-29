#!/bin/bash
# Wrapper script to run kbox with proper environment variables on macOS

# Get Homebrew prefixes
GLIB_PREFIX=$(brew --prefix glib 2>/dev/null)
GSTREAMER_PREFIX=$(brew --prefix gstreamer 2>/dev/null)

if [ -z "$GLIB_PREFIX" ] || [ -z "$GSTREAMER_PREFIX" ]; then
    echo "Error: GStreamer or GLib not found via Homebrew"
    echo "Please install: brew install gstreamer glib"
    exit 1
fi

# Set library paths
export DYLD_LIBRARY_PATH="$GLIB_PREFIX/lib:$GSTREAMER_PREFIX/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"

# Set typelib path for GObject Introspection
export GI_TYPELIB_PATH="$GSTREAMER_PREFIX/share/gir-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"

# Set GStreamer plugin path (include user's custom plugins directory)
export GST_PLUGIN_PATH="$HOME/.gstreamer-1.0:$GSTREAMER_PREFIX/lib/gstreamer-1.0${GST_PLUGIN_PATH:+:$GST_PLUGIN_PATH}"

# Set LADSPA plugin path (for pitch shifting)
export LADSPA_PATH="$HOME/.ladspa${LADSPA_PATH:+:$LADSPA_PATH}"

# Disable problematic Python plugin that causes segfaults on macOS
export GST_PLUGIN_SYSTEM_PATH_1_0="$GSTREAMER_PREFIX/lib/gstreamer-1.0"
export GST_REGISTRY_FORK="no"

# Disable segfault trap for GStreamer (known issue with Python plugin on macOS)
export GST_DEBUG_NO_COLOR=1

# Run kbox
exec uv run python -m kbox.main "$@"
