# gst-signalsmith-pitch

A native GStreamer element (`signalsmithpitch`) for real-time stereo-capable
pitch shifting, backed by [signalsmith-stretch](https://github.com/Signalsmith-Audio/signalsmith-stretch)
(MIT licensed).

Written for [kbox](https://github.com/mdz/kbox) as an alternative to
rubberband via GStreamer's LADSPA wrapper. The LADSPA wrapper in
gst-plugins-bad never resets a wrapped plugin's internal state between uses
(no `FLUSH_STOP` handling), which leaks buffered audio across track
transitions; kbox works around this today by destroying and recreating the
LADSPA element on every song load. This element handles `FLUSH_STOP`/`EOS`
correctly (see `gst_signalsmith_pitch_sink_event` in
`src/gstsignalsmithpitch.cpp`), so no such workaround is needed.

This directory is self-contained (own license, own build, no kbox-specific
code) so it can be pulled out into its own repository later if it turns out
to be useful beyond kbox. For now it's bundled directly in the kbox tree to
avoid packaging/distribution overhead for an unproven, single-consumer
element.

## Building

Requires GStreamer development headers (`libgstreamer1.0-dev`,
`libgstreamer-plugins-base1.0-dev` on Debian) and a C++17 compiler. No
meson/ninja needed -- `build.sh` just invokes the compiler directly via
pkg-config.

```
./build.sh
```

Produces `build/libgstsignalsmithpitch.so`.

## Using it

Point `GST_PLUGIN_PATH` at the `build/` directory:

```
GST_PLUGIN_PATH=$(pwd)/build gst-inspect-1.0 signalsmithpitch
```

Element property: `semitones` (double, -24 to 24, default 0).

## Vendored code

`vendor/signalsmith-stretch` and `vendor/signalsmith-linear` are vendored
headers from Signalsmith Audio (MIT licensed, see their `LICENSE.txt` files).
