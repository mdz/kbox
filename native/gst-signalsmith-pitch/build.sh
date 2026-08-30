#!/bin/bash
# Builds libgstsignalsmithpitch.so directly with the C++ compiler + pkg-config,
# no meson/ninja required. Native build only, for whatever machine runs it.
set -euo pipefail
cd "$(dirname "$0")"

PKG_MODULES="gstreamer-1.0 gstreamer-base-1.0 gstreamer-audio-1.0"

if ! pkg-config --exists $PKG_MODULES; then
    echo "Missing GStreamer development packages (need pkg-config modules: $PKG_MODULES)" >&2
    echo "e.g. on Debian: apt-get install libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev" >&2
    exit 1
fi

mkdir -p build

c++ -O2 -Wall -shared -std=c++17 -fPIC \
    -Ivendor -Ivendor/signalsmith-stretch \
    $(pkg-config --cflags $PKG_MODULES) \
    src/gstsignalsmithpitch.cpp \
    $(pkg-config --libs $PKG_MODULES) \
    -o build/libgstsignalsmithpitch.so

echo "Built build/libgstsignalsmithpitch.so"
