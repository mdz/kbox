#!/bin/bash
# Builds libgstsignalsmithpitch.so directly with the C++ compiler + pkg-config,
# no meson/ninja required. Native build only, for whatever machine runs it.
#
# Usage: build.sh [--if-stale]
#
# --if-stale skips the build when build/ already holds a .so made from the
# current sources (tracked by a checksum of build.sh, src/ and vendor/ written
# next to it). It checks before looking for a compiler, so a machine that has
# an up-to-date .so but no GStreamer dev packages -- the Docker runtime image
# -- gets a no-op rather than an error.
set -euo pipefail
cd "$(dirname "$0")"

OUTPUT=build/libgstsignalsmithpitch.so
STAMP=build/inputs.cksum

inputs_checksum() {
    find build.sh src vendor -type f | LC_ALL=C sort | xargs cat | cksum
}

if [ "${1:-}" = "--if-stale" ] && [ -f "$OUTPUT" ] && [ -f "$STAMP" ] &&
    [ "$(cat "$STAMP")" = "$(inputs_checksum)" ]; then
    exit 0
fi

PKG_MODULES="gstreamer-1.0 gstreamer-base-1.0 gstreamer-audio-1.0"

if ! pkg-config --exists $PKG_MODULES; then
    echo "Missing GStreamer development packages (need pkg-config modules: $PKG_MODULES)" >&2
    echo "  Debian/Ubuntu: apt-get install libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev" >&2
    echo "  macOS:         brew install gstreamer pkgconf" >&2
    exit 1
fi

mkdir -p build

# Compile to a temporary name and rename, so an interrupted build never
# leaves a truncated .so (or a .so with a stamp that doesn't match it).
c++ -O2 -Wall -shared -std=c++17 -fPIC \
    -Ivendor -Ivendor/signalsmith-stretch \
    $(pkg-config --cflags $PKG_MODULES) \
    src/gstsignalsmithpitch.cpp \
    $(pkg-config --libs $PKG_MODULES) \
    -o "$OUTPUT.tmp"
mv "$OUTPUT.tmp" "$OUTPUT"
inputs_checksum > "$STAMP"

echo "Built $OUTPUT"
