"""
Benchmark kbox's GStreamer processing stages.

kbox runs real-time audio and video on constrained hardware (a Raspberry Pi),
where a pipeline change can quietly cost more than the machine can sustain.
The headline number for video is the maximum frame rate each configuration
could hold if it had a whole core to itself: if that drops below the source
frame rate, playback stutters no matter how much headroom the totals suggest.

Elements are built by calling the real StreamingController methods
(_create_audio_sink_bin, _create_pitch_shift_or_identity,
_create_qr_overlay_element, _create_videoscale_element, ...) rather than a
separately hand-written pipeline description, so this can't quietly drift
from what the app actually runs the way a from-scratch reimplementation can.
A prior version of this benchmark hardcoded the QR overlay at 15% of frame
height; the real default (config_manager "overlay_qr_size_percent") is 10% --
exactly the kind of drift this avoids.

Run on the target hardware -- results from a development laptop say very
little about a Pi:

    # on the Pi, inside the container
    docker-compose exec kbox python3 -m pytest test/test_pipeline_benchmark.py -m benchmark -s

    # on a macOS dev machine
    contrib/with-gstreamer.sh uv run pytest test/test_pipeline_benchmark.py -m benchmark -s

Measures CPU time (user+system) across all threads rather than wall clock, so
results stay meaningful when something else is running on the box. Each
variant is run several times and the lowest is reported.

These tests carry both the `gstreamer` and `benchmark` markers, so they are
excluded from the default test run (see pyproject.toml) and only run when
explicitly requested with `-m benchmark`. That keeps them out of routine CI
(their numbers mean nothing off real hardware) while still catching the
benchmark code itself bit-rotting, since `-m gstreamer` runs (e.g. in CI on
real GStreamer-capable hardware) exercise them too.
"""

import resource
import tempfile
from contextlib import contextmanager
from unittest.mock import create_autospec

import pytest

# All tests in this module require GStreamer, and are only meaningful as a
# deliberate benchmark run -- see module docstring.
pytestmark = [pytest.mark.gstreamer, pytest.mark.benchmark]

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.overlay import generate_qr_code
from kbox.streaming import StreamingController, _get_gst, _render_caps_string

AUDIO_RATE = 44100
AUDIO_SAMPLES_PER_BUFFER = 1024

# Defaults mirror a typical kbox deployment. Real hardware runs should
# override these -- see the module docstring for how.
SOURCE_SIZE = (640, 360)
SCREEN_SIZE = (1920, 1080)
FPS = 30
FRAMES = 300
AUDIO_SECONDS = 60.0
REPEATS = 3


def cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


@contextmanager
def make_controller(**config_overrides):
    """A real StreamingController, with fakesinks, so the elements it builds
    are exactly what the app builds -- not a reimplementation of them."""
    db = create_autospec(Database, instance=True)
    config_manager = ConfigManager(db)
    config_manager.set(
        "rubberband_plugin", "ladspa-ladspa-rubberband-so-rubberband-r3-pitchshifter-stereo"
    )
    config_manager.set("audio_output_device", None)
    for key, value in config_overrides.items():
        config_manager.set(key, value)

    controller = StreamingController(config_manager, None, use_fakesinks=True)
    try:
        yield controller
    finally:
        controller.stop()


def run_pipeline(build_elements, repeats):
    """
    Build and run a pipeline `repeats` times, return the lowest CPU time.

    `build_elements` is called fresh for each repeat and must return a list
    of elements/bins to add to a new Gst.Pipeline and link in order, source
    first. Elements can't be reused across pipelines, hence the callback
    rather than a plain list.
    """
    Gst = _get_gst()
    samples = []
    for _ in range(repeats):
        try:
            elements = build_elements()
        except Exception as e:
            return None, str(e)

        pipeline = Gst.Pipeline.new("benchmark")
        for elem in elements:
            pipeline.add(elem)
        for a, b in zip(elements, elements[1:]):
            if not a.link(b):
                return None, f"failed to link {a.get_name()} to {b.get_name()}"

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
        return None
    ms_per_frame = cpu / frames * 1000
    core_share = ms_per_frame * fps / 10  # percent of one core at fps
    sustainable = 1000 / ms_per_frame if ms_per_frame else float("inf")
    warning = "  <-- below source rate" if sustainable < fps else ""
    print(
        f"  {name:<44} {ms_per_frame:6.2f} ms/frame  "
        f"{core_share:6.1f}% core  max {sustainable:5.1f} fps{warning}"
    )
    return ms_per_frame


def _make_video_source(width, height, frames, fps):
    Gst = _get_gst()
    src = Gst.ElementFactory.make("videotestsrc", "src")
    src.set_property("num-buffers", frames)
    src.set_property("pattern", "smpte")

    caps = Gst.ElementFactory.make("capsfilter", "src_caps")
    caps.set_property(
        "caps", Gst.Caps.from_string(f"video/x-raw,width={width},height={height},framerate={fps}/1")
    )
    return [src, caps]


def _make_overlays(controller, width, height):
    """Real QR + text overlay elements, sized the way the app sizes them for
    a `width`x`height` render frame. Returns the elements to link in, in
    order (either may be missing if the plugin isn't installed)."""
    elements = []

    qr = controller._create_qr_overlay_element()
    if qr is not None:
        controller.qr_overlay = qr
        controller._update_qr_size_for_resolution(width, height)
        elements.append(qr)

    text = controller._create_text_overlay_element()
    if text is not None:
        controller.text_overlay = text
        text.set_property("text", "benchmark")
        text.set_property("silent", False)
        controller._update_text_layout_for_resolution(width)
        elements.append(text)

    return elements


def benchmark_video(source_size, screen_size, fps, frames, repeats, qr_image_path):
    Gst = _get_gst()
    src_w, src_h = source_size

    print(
        f"\nVIDEO  {src_w}x{src_h} source -> {screen_size[0]}x{screen_size[1]} screen, "
        f"{frames} frames x{repeats}, decoding excluded"
    )
    print("  (max fps = what one core could sustain; below source rate means stutter)\n")

    results = {}

    with make_controller() as controller:
        if qr_image_path:
            controller.update_qr_overlay(qr_image_path)

        render_w, render_h = controller._choose_render_size(screen_size) or screen_size

        def baseline():
            src = _make_video_source(src_w, src_h, frames, fps)
            sink = Gst.ElementFactory.make("fakesink", "sink")
            sink.set_property("sync", False)
            vc = Gst.ElementFactory.make("videoconvert", "videoconvert")
            return [*src, vc, sink]

        def overlays_at_source_res():
            src = _make_video_source(src_w, src_h, frames, fps)
            vc = Gst.ElementFactory.make("videoconvert", "videoconvert")
            overlay_elems = _make_overlays(controller, src_w, src_h)
            vs = controller._create_videoscale_element()
            sink = Gst.ElementFactory.make("fakesink", "sink")
            sink.set_property("sync", False)
            return [*src, vc, *overlay_elems, vs, sink]

        def upscale_with_overlays():
            src = _make_video_source(src_w, src_h, frames, fps)
            vc = Gst.ElementFactory.make("videoconvert", "videoconvert")
            vs = controller._create_videoscale_element()
            caps = Gst.ElementFactory.make("capsfilter", "render_caps")
            caps.set_property("caps", Gst.Caps.from_string(_render_caps_string(render_w, render_h)))
            overlay_elems = _make_overlays(controller, render_w, render_h)
            sink = Gst.ElementFactory.make("fakesink", "sink")
            sink.set_property("sync", False)
            return [*src, vc, vs, caps, *overlay_elems, sink]

        variants = [
            ("convert only, no scale or overlays", baseline),
            ("overlays at source res, no upscale", overlays_at_source_res),
            ("upscale to screen, overlays at screen res (real pipeline)", upscale_with_overlays),
        ]

        for name, build in variants:
            cpu, error = run_pipeline(build, repeats)
            results[name] = report_video(name, cpu, error, frames, fps)

        # videoscale's resampling method dominates the upscale cost. This
        # matrix is exploratory tuning data, not tied to what the app runs --
        # the app always uses the default (method=1, "upscale to screen"
        # above) via _create_videoscale_element.
        print("\n  exploratory: alternative videoscale methods (not used by the app)\n")
        for method, label in (
            (0, "nearest"),
            (1, "bilinear/default"),
            (2, "4-tap"),
            (3, "lanczos"),
        ):

            def build(method=method):
                src = _make_video_source(src_w, src_h, frames, fps)
                vc = Gst.ElementFactory.make("videoconvert", "videoconvert")
                vs = Gst.ElementFactory.make("videoscale", "videoscale")
                vs.set_property("add-borders", True)
                vs.set_property("method", method)
                caps = Gst.ElementFactory.make("capsfilter", "render_caps")
                caps.set_property(
                    "caps", Gst.Caps.from_string(_render_caps_string(render_w, render_h))
                )
                sink = Gst.ElementFactory.make("fakesink", "sink")
                sink.set_property("sync", False)
                return [*src, vc, vs, caps, sink]

            cpu, error = run_pipeline(build, repeats)
            report_video(f"videoscale method={method} ({label})", cpu, error, frames, fps)

    return results


def benchmark_audio(seconds, repeats):
    Gst = _get_gst()
    buffers = int(seconds * AUDIO_RATE / AUDIO_SAMPLES_PER_BUFFER)
    actual = buffers * AUDIO_SAMPLES_PER_BUFFER / AUDIO_RATE

    print(f"\nAUDIO  {actual:.0f}s of {AUDIO_RATE / 1000:.1f}kHz stereo, x{repeats}\n")

    results = {}

    def make_source():
        src = Gst.ElementFactory.make("audiotestsrc", "src")
        src.set_property("num-buffers", buffers)
        caps = Gst.ElementFactory.make("capsfilter", "src_caps")
        caps.set_property("caps", Gst.Caps.from_string(f"audio/x-raw,rate={AUDIO_RATE},channels=2"))
        return [src, caps]

    # Each engine gets a controller of its own -- the pitch shift element and
    # audio sink bin are picked at controller construction time based on
    # config, and this benchmark wants to run the real
    # _create_audio_sink_bin() for each rather than reimplementing the
    # element chain.
    engines = [
        ("rubberband LADSPA, 0 semitones", {"pitch_shift_engine": "rubberband"}),
        ("signalsmithpitch, 0 semitones", {"pitch_shift_engine": "signalsmith"}),
    ]

    for name, overrides in engines:
        with make_controller(**overrides) as controller:

            def build(controller=controller):
                src = make_source()
                # A fresh audio sink bin per run: it already ends in a
                # (fake)sink, so nothing else needs to be appended.
                audio_bin = controller._create_audio_sink_bin()
                return [*src, audio_bin]

            cpu, error = run_pipeline(build, repeats)
            if cpu is None:
                print(f"  {name:<44} unavailable ({error})")
                results[name] = None
                continue
            core_share = cpu / actual * 100
            print(f"  {name:<44} {core_share:6.2f}% of a core in real time")
            results[name] = core_share

    return results


@pytest.fixture
def qr_image(tmp_path):
    """A real QR image, generated the same way kbox generates one, so the
    overlay benchmark measures compositing a real image rather than an
    empty/no-op overlay."""
    path = generate_qr_code("https://example.com/benchmark", str(tmp_path), size=100)
    return path


def test_benchmark_video(qr_image, capsys):
    """Runs the video pipeline benchmark and prints the results.

    Not an assertion-heavy test: the numbers are only meaningful on target
    hardware (see module docstring). This mainly guards against the
    benchmark code itself breaking, and against the elements it exercises
    (e.g. an overlay property name) drifting out from under it.
    """
    results = benchmark_video(SOURCE_SIZE, SCREEN_SIZE, FPS, FRAMES, REPEATS, qr_image)

    assert results["convert only, no scale or overlays"] is not None
    assert results["upscale to screen, overlays at screen res (real pipeline)"] is not None


def test_benchmark_audio(capsys):
    """Runs the audio pipeline benchmark and prints the results. See
    test_benchmark_video for why this isn't assertion-heavy."""
    # Short run for the automated test; use --audio-seconds via
    # benchmark_audio() directly for a real measurement.
    results = benchmark_audio(seconds=2.0, repeats=REPEATS)

    assert results["rubberband LADSPA, 0 semitones"] is not None


if __name__ == "__main__":
    import argparse

    def parse_size(text):
        width, height = text.lower().split("x")
        return int(width), int(height)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=parse_size, default="640x360")
    parser.add_argument("--screen", type=parse_size, default="1920x1080")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--audio-seconds", type=float, default=60.0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--qr-image", help="path to a QR PNG; a fresh one is generated if omitted")
    parser.add_argument("--only", choices=("video", "audio"))
    args = parser.parse_args()

    Gst = _get_gst()
    Gst.init(None)
    print(f"GStreamer {Gst.version_string()}")

    qr_path = args.qr_image
    tmpdir = None
    if not qr_path:
        tmpdir = tempfile.mkdtemp()
        qr_path = generate_qr_code("https://example.com/benchmark", tmpdir, size=100)

    try:
        if args.only != "audio":
            benchmark_video(args.source, args.screen, args.fps, args.frames, args.repeats, qr_path)
        if args.only != "video":
            benchmark_audio(args.audio_seconds, args.repeats)
        print()
    finally:
        if tmpdir:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)
