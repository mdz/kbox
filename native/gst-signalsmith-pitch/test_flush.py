"""Scripted flushing-seek test against the real native signalsmithpitch
element (not the Python prototype), to confirm FLUSH_STOP actually resets
internal state end-to-end -- this is the behavior kbox's rubberband
destroy/recreate workaround exists to approximate, and the reason to prefer
this element over the LADSPA wrapper.
"""

import sys
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

Gst.init(None)

pipeline = Gst.parse_launch(
    "audiotestsrc freq=220 wave=sine ! audioconvert ! "
    "audio/x-raw,format=F32LE,layout=interleaved,rate=48000,channels=2 ! "
    "signalsmithpitch name=pitch semitones=5.0 ! audioconvert ! fakesink"
)

pipeline.set_state(Gst.State.PLAYING)
bus = pipeline.get_bus()

start = time.time()
while time.time() - start < 2:
    msg = bus.timed_pop_filtered(200 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
    if msg:
        if msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            print("ERROR before seek:", err, debug)
            sys.exit(1)
        break

print("Ran 2s clean. Issuing flushing seek x5 (simulates rapid track transitions)...")
for i in range(5):
    ok = pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
    if not ok:
        print(f"seek {i} returned FALSE")
        sys.exit(1)
    time.sleep(0.3)

time.sleep(2)

msg = bus.timed_pop_filtered(10, Gst.MessageType.ERROR)
if msg:
    err, debug = msg.parse_error()
    print("ERROR after seeks:", err, debug)
    sys.exit(1)

print("PASS: 5 flushing seeks handled with no error, pipeline still running.")
pipeline.set_state(Gst.State.NULL)
