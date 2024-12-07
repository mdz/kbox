# kbox

kbox is a simple server application to perform real-time, high-quality pitch
shifting of stereo audio input, intended for use in a karaoke environment.

It uses:

- [GStreamer](https://gstreamer.freedesktop.org/)
- [gst-python](https://gstreamer.freedesktop.org/bindings/python.html)
- [rubberband](https://breakfastquay.com/rubberband/)
- [python-rtmidi](https://github.com/SpotlightKid/python-rtmidi)
- [LADSPA](https://www.ladspa.org/)

kbox is controlled via a MIDI keyboard. Tapping middle C on the keyboard will
reset the pitch shift to zero. Any other note will shift the pitch up or down
by the interval between that note and middle C (C<sub>4</sub>). For example,
the E above middle C (E<sub>4</sub>) will shift up by two whole steps (4
semitones), and the B&flat; below middle C (B&flat;<sub>3</sub>) will shift
down by one whole step (2 semitones).

## Configuration

Currently, the configuration is hardcoded in `kbox/config.py`. The default
configuration uses the default ALSA sink for output, and expects an Akai MPK
mini Play mk3 as the MIDI input device. If you need to use a different audio
output, or a different MIDI device, you will need to edit this file.

## Easy setup (Docker)

1. Install docker
1. Install docker-compose
1. docker-compose build
1. docker-compose up

## Manual setup (Debian and derivatives)

```bash
apt install python3-gst-1.0 gstreamer1.0-alsa python3-mido python3-rtmidi rubberband-ladspa gstreamer1.0-plugins-bad
```

## Manual setup (MacOS)

- install gstreamer from homebrew (or was this a mistake?? /Library/Frameworks has GStreamer...)
- python bindings for gstreamer (how?)
- compile gstreamer LADSPA plugin from source (must match gstreamer version), install in $GST_PLUGIN_PATH
- compile rubberband LADSPA plugin from source, install in $HOME/.ladspa
- run with GST_PLUGIN_PATH set appropriately
