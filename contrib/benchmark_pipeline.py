#!/usr/bin/env python3
"""
Benchmark the cost of kbox's GStreamer processing stages.

kbox runs real-time audio and video on constrained hardware (a Raspberry Pi),
where a pipeline change can quietly cost more than the machine can sustain.
The headline number here is the maximum frame rate each configuration could
hold if it had a whole core to itself: if that drops below the source frame
rate, playback stutters no matter how much headroom the totals suggest.

Run it on the target hardware -- results from a development laptop say very
little about a Pi.

    # on the Pi, inside the container
    docker-compose exec kbox python3 contrib/benchmark_pipeline.py

    # on a macOS dev machine
    contrib/with-gstreamer.sh uv run python contrib/benchmark_pipeline.py

    # what a 4:3 source costs on a 1080p screen, at 25fps
    contrib/benchmark_pipeline.py --source 640x480 --screen 1920x1080 --fps 25

Measures CPU time (user+system) across all threads rather than wall clock, so
results stay meaningful when something else is running on the box. Each
variant is run several times and the lowest is reported.
"""

import argparse
import os
import resource
import sys

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

# Where kbox keeps the native pitch-shift plugin. It is not on GStreamer's
# default search path; kbox registers it from disk at startup, so anything
# wanting to benchmark it has to do the same.
DEFAULT_SIGNALSMITH_PATHS = (
    "/app/native/gst-signalsmith-pitch/build/libgstsignalsmithpitch.so",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "native",
        "gst-signalsmith-pitch",
        "build",
        "libgstsignalsmithpitch.so",
    ),
)

AUDIO_RATE = 44100
AUDIO_SAMPLES_PER_BUFFER = 1024


def parse_size(text):
    try:
        width, height = text.lower().split("x")
        return int(width), int(height)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected WxH, got {text!r}")


def cpu_seconds():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def run_pipeline(description, repeats):
    """Run a pipeline to EOS and return the lowest CPU time across repeats."""
    samples = []
    for _ in range(repeats):
        try:
            pipeline = Gst.parse_launch(description)
        except Exception as e:
            return None, str(e)

        started = cpu_seconds()
        pipeline.set_state(Gst.State.PLAYING)
        message = pipeline.get_bus().timed_pop_filtered(
            120 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
        )
        used = cpu_seconds() - started
        pipeline.set_state(Gst.State.NULL)

        if message is None:
            return None, "timed out"
        if message.type == Gst.MessageType.ERROR:
            err, _ = message.parse_error()
            return None, str(err)
        samples.append(used)

    return min(samples), None


def report_video(name, cpu, error, frames, fps):
    if cpu is None:
        print(f"  {name:<44} unavailable ({error})")
        return
    ms_per_frame = cpu / frames * 1000
    core_share = ms_per_frame * fps / 10  # percent of one core at fps
    sustainable = 1000 / ms_per_frame if ms_per_frame else float("inf")
    warning = "  <-- below source rate" if sustainable < fps else ""
    print(
        f"  {name:<44} {ms_per_frame:6.2f} ms/frame  "
        f"{core_share:6.1f}% core  max {sustainable:5.1f} fps{warning}"
    )


def benchmark_video(args):
    src_w, src_h = args.source
    scr_w, scr_h = args.screen
    frames, fps = args.frames, args.fps

    source = (
        f"videotestsrc num-buffers={frames} pattern=smpte "
        f"! video/x-raw,width={src_w},height={src_h},framerate={fps}/1"
    )
    screen_caps = f"video/x-raw,width={scr_w},height={scr_h},pixel-aspect-ratio=1/1"

    def overlays(height):
        # Matches kbox's default: QR at 15% of frame height.
        size = max(48, int(height * 0.15))
        qr = args.qr_image
        pixbuf = (
            f"gdkpixbufoverlay location={qr} overlay-width={size} overlay-height={size} ! "
            if qr and os.path.exists(qr)
            else ""
        )
        return f"{pixbuf}textoverlay text=benchmark"

    print(
        f"\nVIDEO  {src_w}x{src_h} source -> {scr_w}x{scr_h} screen, "
        f"{frames} frames x{args.repeats}, decoding excluded"
    )
    print("  (max fps = what one core could sustain; below source rate means stutter)\n")

    variants = [
        ("convert only, no scale or overlays", f"{source} ! videoconvert"),
        (
            "overlays at source res, no upscale",
            f"{source} ! videoconvert ! {overlays(src_h)} ! videoscale",
        ),
        (
            "upscale to screen, overlays at screen res",
            f"{source} ! videoconvert ! videoscale add-borders=true ! {screen_caps} "
            f"! {overlays(scr_h)}",
        ),
    ]

    # videoscale's resampling method dominates the upscale cost, so show what
    # each one buys. 0=nearest 1=bilinear(default) 2=4-tap 3=lanczos.
    for method, label in ((0, "nearest"), (1, "bilinear/default"), (2, "4-tap"), (3, "lanczos")):
        variants.append(
            (
                f"upscale, videoscale method={method} ({label})",
                f"{source} ! videoconvert ! videoscale add-borders=true method={method} "
                f"! {screen_caps} ! {overlays(scr_h)}",
            )
        )

    for name, description in variants:
        cpu, error = run_pipeline(f"{description} ! fakesink sync=false", args.repeats)
        report_video(name, cpu, error, frames, fps)


def benchmark_audio(args):
    seconds = args.audio_seconds
    buffers = int(seconds * AUDIO_RATE / AUDIO_SAMPLES_PER_BUFFER)
    actual = buffers * AUDIO_SAMPLES_PER_BUFFER / AUDIO_RATE

    if Gst.ElementFactory.find("signalsmithpitch") is None:
        for path in (
            (args.signalsmith_plugin,) if args.signalsmith_plugin else DEFAULT_SIGNALSMITH_PATHS
        ):
            if path and os.path.exists(path):
                try:
                    Gst.Plugin.load_file(path)
                    break
                except Exception:
                    pass

    source = f"audiotestsrc num-buffers={buffers} ! audio/x-raw,rate={AUDIO_RATE},channels=2"

    print(f"\nAUDIO  {actual:.0f}s of {AUDIO_RATE / 1000:.1f}kHz stereo, x{args.repeats}\n")

    variants = [
        ("convert only, no pitch element", "audioconvert"),
        ("identity (pitch unavailable fallback)", "audioconvert ! identity ! audioconvert"),
        (
            "signalsmithpitch, 0 semitones",
            "audioconvert ! signalsmithpitch semitones=0 ! audioconvert",
        ),
        (
            "signalsmithpitch, +3 semitones",
            "audioconvert ! signalsmithpitch semitones=3 ! audioconvert",
        ),
        (
            "rubberband LADSPA, 0 semitones",
            "audioconvert ! ladspa-ladspa-rubberband-so-rubberband-pitchshifter-stereo ! audioconvert",
        ),
    ]

    for name, chain in variants:
        cpu, error = run_pipeline(f"{source} ! {chain} ! fakesink sync=false", args.repeats)
        if cpu is None:
            print(f"  {name:<44} unavailable ({error})")
            continue
        core_share = cpu / actual * 100
        print(f"  {name:<44} {core_share:6.2f}% of a core in real time")


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark kbox's GStreamer processing stages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run on the target hardware; laptop numbers do not predict a Pi.",
    )
    parser.add_argument(
        "--source",
        type=parse_size,
        default="640x360",
        help="source video size, WxH (default: 640x360)",
    )
    parser.add_argument(
        "--screen",
        type=parse_size,
        default="1920x1080",
        help="display size, WxH (default: 1920x1080)",
    )
    parser.add_argument("--fps", type=int, default=30, help="source frame rate (default: 30)")
    parser.add_argument(
        "--frames", type=int, default=300, help="frames per video run (default: 300)"
    )
    parser.add_argument(
        "--audio-seconds", type=float, default=60.0, help="seconds of audio per run (default: 60)"
    )
    parser.add_argument(
        "--repeats", type=int, default=3, help="runs per variant, lowest wins (default: 3)"
    )
    parser.add_argument(
        "--qr-image",
        default="/tmp/kbox_qr_code.png",
        help="image for the QR overlay stage; skipped if absent",
    )
    parser.add_argument("--signalsmith-plugin", help="path to libgstsignalsmithpitch.so")
    parser.add_argument("--only", choices=("video", "audio"), help="run just one section")
    args = parser.parse_args()

    for name in ("source", "screen"):
        value = getattr(args, name)
        if isinstance(value, str):
            setattr(args, name, parse_size(value))

    Gst.init(None)
    print(f"GStreamer {Gst.version_string()}")

    if args.only != "audio":
        benchmark_video(args)
    if args.only != "video":
        benchmark_audio(args)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
