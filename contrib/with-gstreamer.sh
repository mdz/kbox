#!/bin/bash
# Run a command with GStreamer set up against the Homebrew install, on macOS.
#
#   contrib/with-gstreamer.sh uv run python -m kbox.main   # run the app
#   contrib/with-gstreamer.sh uv run pytest -m gstreamer   # run pipeline tests
#
# macOS is not a deployment platform for kbox -- it is where development
# happens. This exists so the GStreamer-dependent parts can be exercised here
# rather than only in Docker or on a Pi.
#
# Why pin to Homebrew: a Mac can easily end up with two GStreamer installs,
# Homebrew's and the official GStreamer.framework from the binary installer
# (/Library/Frameworks/GStreamer.framework). Loading libraries from one and
# plugins from the other tends to fail in confusing ways rather than cleanly,
# so this points everything at Homebrew's copy and does not inherit a plugin
# registry that might belong to the other one.
#
# Python needs PyGObject to reach any of this. It is a dev dependency marked
# for macOS only, since Linux and the Docker image use the system python3-gi
# package instead. If `import gi` fails, run: uv sync --group dev

set -e

if [ $# -eq 0 ]; then
    echo "Usage: $(basename "$0") <command> [args...]" >&2
    echo "  e.g. $(basename "$0") uv run pytest -m gstreamer" >&2
    exit 64
fi

GLIB_PREFIX=$(brew --prefix glib 2>/dev/null)
GSTREAMER_PREFIX=$(brew --prefix gstreamer 2>/dev/null)

if [ -z "$GLIB_PREFIX" ] || [ -z "$GSTREAMER_PREFIX" ]; then
    echo "Error: GStreamer or GLib not found via Homebrew" >&2
    echo "Please install: brew install gstreamer glib gobject-introspection" >&2
    exit 1
fi

# Libraries and GObject introspection data, from Homebrew.
export DYLD_LIBRARY_PATH="$GLIB_PREFIX/lib:$GSTREAMER_PREFIX/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export GI_TYPELIB_PATH="$GSTREAMER_PREFIX/share/gir-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"

# Plugins. GST_PLUGIN_SYSTEM_PATH_1_0 is set rather than appended, so a
# GStreamer.framework path already in the environment cannot pull plugins from
# the other install into this process.
export GST_PLUGIN_SYSTEM_PATH_1_0="$GSTREAMER_PREFIX/lib/gstreamer-1.0"
export GST_PLUGIN_PATH="$HOME/.gstreamer-1.0:$GSTREAMER_PREFIX/lib/gstreamer-1.0${GST_PLUGIN_PATH:+:$GST_PLUGIN_PATH}"

# LADSPA plugins, for the rubberband pitch shifter.
export LADSPA_PATH="$HOME/.ladspa${LADSPA_PATH:+:$LADSPA_PATH}"

# Forking to scan the plugin registry is unreliable on macOS.
export GST_REGISTRY_FORK="no"

export GST_DEBUG_NO_COLOR=1

exec "$@"
