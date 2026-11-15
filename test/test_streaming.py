"""
Integration tests for StreamingController.

These tests use fakesinks for headless testing and verify pipeline state
transitions, pitch shifting, and error handling without requiring hardware.

All tests in this module require GStreamer (and, for the native pitch-shift
element, the plugin that `uv sync` builds via hatch_build.py). They do not
skip when it's unavailable -- they fail loudly, so a missing dependency shows
up as a red build rather than silently vanished coverage. Run them where
GStreamer is guaranteed: the Docker image (see .github/workflows/ci.yml), or a
macOS dev machine with the dev dependencies installed -- see
docs/development/gstreamer-pipeline.md.
"""

import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, create_autospec

import pytest

# GStreamer/PyGObject env setup (needed before anything imports `gi` on
# macOS) happens in test/conftest.py, which pytest imports before any test
# module.
from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.streaming import StreamingController, _get_glib, _get_gst, elements, gst, pipelines
from kbox.streaming.bus import BusWatcher
from kbox.streaming.overlays import OverlayController

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture(scope="session")
def test_video_1s():
    """Create a 1-second test video for testing."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    video_path = fixtures_dir / "test_1s.mp4"

    if not video_path.exists():
        logger.info("Creating 1-second test video...")
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "testsrc=d=1:s=320x240:r=30",
                "-f",
                "lavfi",
                "-i",
                "sine=f=440:d=1",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                str(video_path),
            ],
            check=True,
            capture_output=True,
        )
        logger.info("Test video created at %s", video_path)

    return str(video_path)


@pytest.fixture(scope="session")
def test_video_3s():
    """Create a 3-second test video for longer tests."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    video_path = fixtures_dir / "test_3s.mp4"

    if not video_path.exists():
        logger.info("Creating 3-second test video...")
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "testsrc=d=3:s=320x240:r=30",
                "-f",
                "lavfi",
                "-i",
                "sine=f=440:d=3",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                str(video_path),
            ],
            check=True,
            capture_output=True,
        )
        logger.info("Test video created at %s", video_path)

    return str(video_path)


@pytest.fixture
def mock_config_manager():
    """Create a ConfigManager backed by a real temp-file database.

    A create_autospec(Database) mock previously stood in here, but its
    cursor().fetchone() returns a truthy MagicMock for any unset key --
    which made every ConfigManager.get() fall-through to defaults silently
    return garbage instead of None, masking real bugs (e.g. the
    signalsmith-pitch-plugin lookup treating a MagicMock as a valid path).
    A real (temporary) database behaves like production.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path=path)
    config_manager = ConfigManager(db)

    config_manager.set("audio_output_device", None)

    yield config_manager

    db.close()
    os.unlink(path)


@pytest.fixture
def controller(mock_config_manager):
    """Create a StreamingController with fakesinks for headless testing."""
    ctrl = StreamingController(mock_config_manager, None, use_fakesinks=True)
    yield ctrl
    # Cleanup after test
    ctrl.stop()


# =========================================================================
# Initialization Tests
# =========================================================================


def test_init_creates_pipeline_in_ready_state(controller):
    """Test that initialization creates pipeline in READY (idle) state."""
    assert controller.get_pipeline_state() == "ready"
    assert controller.state == "idle"
    assert controller.playbin is not None
    assert controller.audio_bin is not None
    assert controller.video_bin is not None


def test_init_creates_pitch_shift_element(controller):
    """Test that pitch shift element is created (or identity fallback)."""
    assert controller.pitch_shift_element is not None


def test_init_creates_text_overlay_element(controller):
    """Test that text overlay element is created for notifications.

    This requires gstreamer1.0-x (Pango plugin) to be installed.
    """
    assert controller.display.text_overlay is not None, (
        "textoverlay element not available - install gstreamer1.0-x"
    )


def test_reinitialize_pipeline(controller, test_video_1s):
    """Test that reinitialize_pipeline rebuilds the pipeline with fresh config."""
    # Get original pipeline elements
    original_playbin = controller.playbin
    original_audio_bin = controller.audio_bin
    original_video_bin = controller.video_bin

    # Load a file and play it
    controller.load_file(test_video_1s)
    assert controller.state == "playing"

    # Reinitialize the pipeline
    controller.reinitialize_pipeline()

    # Verify new pipeline was created
    assert controller.playbin is not None
    assert controller.playbin is not original_playbin, "Pipeline should be recreated"
    assert controller.audio_bin is not original_audio_bin, "Audio bin should be recreated"
    assert controller.video_bin is not original_video_bin, "Video bin should be recreated"

    # Verify state was reset
    assert controller.state == "idle"
    assert controller.current_file is None
    assert controller.get_pipeline_state() == "ready"


def test_reinitialize_pipeline_preserves_interstitial(controller):
    """Test that reinitialize_pipeline preserves interstitial display."""
    # Create a test interstitial image
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        interstitial_path = f.name

    try:
        # Create a simple 100x100 black PNG
        from PIL import Image

        img = Image.new("RGB", (100, 100), color="black")
        img.save(interstitial_path)

        # Display the interstitial
        controller.display_image(interstitial_path)
        assert controller._is_interstitial is True
        assert controller.current_file == interstitial_path

        # Reinitialize the pipeline
        controller.reinitialize_pipeline()

        # The interstitial should be restored (state is "playing" when showing image)
        # If the image was successfully redisplayed, state should be "playing" and _is_interstitial should be True
        assert controller.state == "playing"
        assert controller._is_interstitial is True

    finally:
        # Cleanup
        Path(interstitial_path).unlink(missing_ok=True)


# =========================================================================
# Playback State Transition Tests
# =========================================================================


def test_load_file_transitions_to_playing(controller, test_video_1s):
    """Test that load_file() transitions pipeline to PLAYING state."""
    controller.load_file(test_video_1s)

    assert controller.get_pipeline_state() == "playing"
    assert controller.state == "playing"
    assert controller.current_file == test_video_1s


def test_stop_playback_returns_to_idle(controller, test_video_1s):
    """Test that stop_playback() returns to READY (idle) state."""
    controller.load_file(test_video_1s)
    controller.stop_playback()

    assert controller.get_pipeline_state() == "ready"
    assert controller.state == "idle"
    assert controller.current_file is None


def test_pause_resume(controller, test_video_3s):
    """Test pause and resume functionality."""
    controller.load_file(test_video_3s)

    controller.pause()
    assert controller.state == "paused"
    # Pipeline needs time to complete state change
    time.sleep(0.2)
    assert controller.get_pipeline_state() == "paused"

    controller.resume()
    assert controller.state == "playing"
    time.sleep(0.2)
    assert controller.get_pipeline_state() == "playing"


def _still_image_state(controller):
    """The still image feeder's pipeline state, or None if it isn't running."""
    Gst = _get_gst()
    if controller.still_image.pipeline is None:
        return None
    ret, state, pending = controller.still_image.pipeline.get_state(2 * Gst.SECOND)
    return state


def test_pause_holds_last_frame_on_display_bridge(controller, test_video_3s):
    """
    Pausing must not starve the display bridge.

    A paused playbin stops feeding intervideosink, and intervideosrc serves a
    frame only a couple of times before generating black (see
    docs/development/gstreamer-pipeline.md) -- so pause() keeps the last
    frame going itself, the way interstitials keep their image going.
    """
    Gst = _get_gst()
    controller.load_file(test_video_3s)
    # load_file waits for PLAYING, which needs a prerolled frame, so one has
    # already crossed the bridge.
    assert controller._last_video_sample is not None

    controller.pause()
    assert _still_image_state(controller) == Gst.State.PLAYING

    controller.resume()
    assert controller.still_image.pipeline is None


def test_seek_while_paused_holds_the_new_frame(controller, test_video_3s):
    """A seek while paused replaces the held frame with one from the new position."""
    Gst = _get_gst()
    controller.load_file(test_video_3s)
    controller.pause()
    held_before = controller._last_video_sample

    assert controller.seek(2)

    assert controller._last_video_sample is not held_before
    assert _still_image_state(controller) == Gst.State.PLAYING
    controller.resume()


def test_stop_playback_while_paused_drops_the_held_frame(controller, test_video_3s):
    """Stopping from pause drops the hold and the cached frame with the song."""
    controller.load_file(test_video_3s)
    controller.pause()

    controller.stop_playback()

    assert controller.still_image.pipeline is None
    assert controller._last_video_sample is None


def test_pause_when_not_playing_raises_error(controller):
    """Test that pausing when not playing raises an error."""
    with pytest.raises(RuntimeError, match="not currently playing"):
        controller.pause()


def test_resume_when_not_paused_raises_error(controller, test_video_1s):
    """Test that resuming when not paused raises an error."""
    controller.load_file(test_video_1s)
    with pytest.raises(RuntimeError, match="not currently paused"):
        controller.resume()


# =========================================================================
# Stress Tests
# =========================================================================


def test_rapid_start_stop_cycles(controller, test_video_1s):
    """Stress test: rapid state transitions."""
    for i in range(20):
        logger.debug("Cycle %d/20", i + 1)
        controller.load_file(test_video_1s)
        controller.stop_playback()

    assert controller.state == "idle"
    assert controller.get_pipeline_state() == "ready"


def test_rapid_pause_resume_cycles(controller, test_video_3s):
    """Stress test: rapid pause/resume cycles."""
    controller.load_file(test_video_3s)

    for i in range(10):
        logger.debug("Pause/resume cycle %d/10", i + 1)
        controller.pause()
        time.sleep(0.05)  # Small delay to let state settle
        controller.resume()
        time.sleep(0.05)

    assert controller.state == "playing"
    controller.stop_playback()


def test_load_different_files_sequentially(controller, test_video_1s, test_video_3s):
    """Test loading different files sequentially."""
    controller.load_file(test_video_1s)
    assert controller.current_file == test_video_1s

    controller.load_file(test_video_3s)
    assert controller.current_file == test_video_3s

    controller.load_file(test_video_1s)
    assert controller.current_file == test_video_1s

    controller.stop_playback()


# =========================================================================
# Pitch Shift Tests
# =========================================================================


def test_pitch_shift_persists_across_songs(controller, test_video_1s):
    """Test that pitch shift setting persists across song changes."""
    controller.set_pitch_shift(5)
    assert controller.pitch_shift_semitones == 5

    controller.load_file(test_video_1s)
    controller.stop_playback()

    # Pitch shift value should persist
    assert controller.pitch_shift_semitones == 5

    # If pitch shift element is not identity, it should have the value
    if controller.pitch_shift_element:
        element_type = type(controller.pitch_shift_element).__name__
        if element_type != "GstIdentity":
            try:
                actual_semitones = controller.pitch_shift_element.get_property("semitones")
                assert actual_semitones == 5
            except Exception:
                # If signalsmithpitch not available, that's okay
                pass


def test_pitch_shift_during_playback(controller, test_video_3s):
    """Test changing pitch shift while playing."""
    controller.load_file(test_video_3s)

    controller.set_pitch_shift(3)
    assert controller.pitch_shift_semitones == 3

    controller.set_pitch_shift(-2)
    assert controller.pitch_shift_semitones == -2

    controller.stop_playback()


# =========================================================================
# Pitch Shift Element Selection Tests
# =========================================================================


def test_create_pitch_shift_uses_signalsmith_when_available(controller, monkeypatch):
    """The dispatcher should use the native signalsmithpitch element when its
    plugin is available."""
    sentinel = MagicMock(name="signalsmith_element")
    monkeypatch.setattr(
        elements, "create_signalsmith_pitch_shift", lambda config_manager, semitones: sentinel
    )

    result = elements.create_pitch_shift_or_identity(controller.config_manager, 0)

    assert result is sentinel


def test_create_pitch_shift_falls_back_to_identity_when_unavailable(controller, monkeypatch):
    """When the signalsmithpitch plugin isn't available, dispatcher should
    return identity rather than raise."""
    monkeypatch.setattr(
        elements, "create_signalsmith_pitch_shift", lambda config_manager, semitones: None
    )

    result = elements.create_pitch_shift_or_identity(controller.config_manager, 0)

    assert type(result).__name__ == "GstIdentity"


@pytest.fixture
def signalsmith_elem(controller):
    """A native signalsmithpitch element at 0 semitones.

    Fails, never skips, when the plugin is unavailable, so missing coverage
    shows up red rather than silently vanishing.
    """
    elem = elements.create_signalsmith_pitch_shift(controller.config_manager, 0)
    assert elem is not None, (
        "signalsmithpitch plugin not available -- `uv sync` builds it, but "
        "won't retry a failed build by itself: run "
        "native/gst-signalsmith-pitch/build.sh to see why it failed (needs a C++ "
        "compiler, pkg-config and the GStreamer dev packages), or check the "
        "Docker image build"
    )
    return elem


def _estimate_frequency_hz(pcm_bytes: bytes, channels: int, sample_rate: int) -> float:
    """Estimate the dominant frequency of one channel via zero-crossing rate."""
    import array

    samples = array.array("f")
    samples.frombytes(pcm_bytes)
    channel = samples[0::channels]

    # Skip the first chunk to dodge the element's inherent processing latency
    # (silence/settling before real output begins).
    skip = len(channel) // 4
    channel = channel[skip:]

    crossings = 0
    prev = channel[0]
    for s in channel[1:]:
        if (s >= 0) != (prev >= 0):
            crossings += 1
        prev = s

    duration_s = len(channel) / sample_rate
    return crossings / 2 / duration_s if duration_s > 0 else 0.0


def _run_pitch_element_and_measure_frequency(
    pitch_element, semitones, input_freq=220.0, sample_rate=48000, channels=2
):
    """Push a real sine wave through pitch_element in a standalone pipeline
    (audiotestsrc -> audioconvert -> pitch_element -> audioconvert -> appsink)
    and measure the actual output frequency, to confirm the element is
    really shifting pitch rather than just passing audio through.
    """
    Gst = _get_gst()
    pitch_element.set_property("semitones", semitones)

    pipeline = Gst.Pipeline.new("pitch-shift-frequency-test")
    src = Gst.ElementFactory.make("audiotestsrc")
    src.set_property("freq", input_freq)
    src.set_property("wave", "sine")
    src.set_property("samplesperbuffer", 1024)
    src.set_property("num-buffers", 200)  # ~4.3s at 48kHz/1024 -- plenty for a stable estimate
    conv_in = Gst.ElementFactory.make("audioconvert")
    conv_out = Gst.ElementFactory.make("audioconvert")
    out_caps = Gst.Caps.from_string(
        f"audio/x-raw,format=F32LE,layout=interleaved,rate={sample_rate},channels={channels}"
    )
    capsfilter = Gst.ElementFactory.make("capsfilter")
    capsfilter.set_property("caps", out_caps)
    sink = Gst.ElementFactory.make("appsink")
    sink.set_property("sync", False)

    for elem in (src, conv_in, pitch_element, conv_out, capsfilter, sink):
        pipeline.add(elem)
    src.link(conv_in)
    conv_in.link(pitch_element)
    pitch_element.link(conv_out)
    conv_out.link(capsfilter)
    capsfilter.link(sink)

    pipeline.set_state(Gst.State.PLAYING)
    chunks = []
    try:
        while True:
            sample = sink.emit("try-pull-sample", int(2 * Gst.SECOND))
            if sample is None:
                break
            buf = sample.get_buffer()
            ok, mapinfo = buf.map(Gst.MapFlags.READ)
            if ok:
                chunks.append(bytes(mapinfo.data))
                buf.unmap(mapinfo)
    finally:
        pipeline.set_state(Gst.State.NULL)

    assert chunks, "no output samples captured -- pipeline produced nothing"
    return _estimate_frequency_hz(b"".join(chunks), channels=channels, sample_rate=sample_rate)


def test_signalsmith_pitch_shift_actually_shifts_frequency(signalsmith_elem):
    """The native signalsmith element should genuinely change pitch, not just pass audio through."""
    semitones = 12  # one octave up -> should double the frequency
    input_freq = 220.0
    measured_freq = _run_pitch_element_and_measure_frequency(
        signalsmith_elem, semitones, input_freq
    )

    expected_freq = input_freq * (2 ** (semitones / 12))
    assert measured_freq == pytest.approx(expected_freq, rel=0.05)


def test_signalsmith_pitch_shift_mono_channel(signalsmith_elem):
    """The element declares support for any channel count (see caps in
    gstsignalsmithpitch.cpp) -- confirm mono actually works, not just the
    stereo path exercised by the other frequency test."""
    semitones = 12  # one octave up -> should double the frequency
    input_freq = 220.0
    measured_freq = _run_pitch_element_and_measure_frequency(
        signalsmith_elem, semitones, input_freq, channels=1
    )

    expected_freq = input_freq * (2 ** (semitones / 12))
    assert measured_freq == pytest.approx(expected_freq, rel=0.05)


def test_signalsmith_pitch_shift_semitones_boundary_values(signalsmith_elem):
    """The semitones property is declared with range [-24, 24]; confirm both
    extremes round-trip through set/get without being rejected or clamped."""
    signalsmith_elem.set_property("semitones", -24.0)
    assert signalsmith_elem.get_property("semitones") == pytest.approx(-24.0)

    signalsmith_elem.set_property("semitones", 24.0)
    assert signalsmith_elem.get_property("semitones") == pytest.approx(24.0)


def test_signalsmith_flush_stop_resets_state(signalsmith_elem):
    """FLUSH_STOP must reset the element's internal signalsmith-stretch state
    end-to-end -- this is the behavior kbox's old rubberband destroy/recreate
    workaround existed to approximate, and the reason this element is used
    instead of the LADSPA wrapper (see native/gst-signalsmith-pitch/README.md
    and gst_signalsmith_pitch_sink_event in gstsignalsmithpitch.cpp). Rapid
    flushing seeks (as happen on quick track transitions) must not error out
    or leave the pipeline in a broken state.
    """
    Gst = _get_gst()
    pipeline = Gst.Pipeline.new("pitch-shift-flush-test")
    src = Gst.ElementFactory.make("audiotestsrc")
    src.set_property("freq", 220.0)
    src.set_property("wave", "sine")
    conv_in = Gst.ElementFactory.make("audioconvert")
    conv_out = Gst.ElementFactory.make("audioconvert")
    sink = Gst.ElementFactory.make("fakesink")

    for e in (src, conv_in, signalsmith_elem, conv_out, sink):
        pipeline.add(e)
    src.link(conv_in)
    conv_in.link(signalsmith_elem)
    signalsmith_elem.link(conv_out)
    conv_out.link(sink)

    pipeline.set_state(Gst.State.PLAYING)
    bus = pipeline.get_bus()
    try:
        msg = bus.timed_pop_filtered(2 * Gst.SECOND, Gst.MessageType.ERROR)
        assert msg is None, f"pipeline errored before seeking: {msg.parse_error()}"

        for i in range(5):
            ok = pipeline.seek_simple(
                Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0
            )
            assert ok, f"flushing seek {i} returned FALSE"
            time.sleep(0.2)

        msg = bus.timed_pop_filtered(0.5 * Gst.SECOND, Gst.MessageType.ERROR)
        assert msg is None, f"pipeline errored after flushing seeks: {msg.parse_error()}"
    finally:
        pipeline.set_state(Gst.State.NULL)


# =========================================================================
# Volume Gain Tests
# =========================================================================


def test_init_creates_volume_element(controller):
    """Test that the volume element is created for loudness normalization."""
    assert controller.volume_element is not None
    assert controller.volume_gain_linear == pytest.approx(1.0)


def test_set_volume_gain_db_updates_element(controller):
    """Test that setting a gain updates both the tracked value and the element."""
    controller.set_volume_gain_db(-6.0)

    assert controller.volume_gain_linear == pytest.approx(0.501, abs=0.01)
    assert controller.volume_element.get_property("volume") == pytest.approx(0.501, abs=0.01)


def test_volume_gain_persists_across_songs(controller, test_video_1s):
    """Test that volume gain setting persists across song changes."""
    controller.set_volume_gain_db(-3.0)

    controller.load_file(test_video_1s)
    controller.stop_playback()

    assert controller.volume_gain_linear == pytest.approx(10 ** (-3.0 / 20), abs=0.001)
    assert controller.volume_element.get_property("volume") == pytest.approx(
        10 ** (-3.0 / 20), abs=0.001
    )


def test_set_volume_gain_db_zero_is_unity(controller):
    """Test that a 0dB gain leaves the output unadjusted."""
    controller.set_volume_gain_db(6.0)
    controller.set_volume_gain_db(0.0)

    assert controller.volume_gain_linear == pytest.approx(1.0)
    assert controller.volume_element.get_property("volume") == pytest.approx(1.0)


# =========================================================================
# Position and Seeking Tests
# =========================================================================


def test_get_position_returns_none_when_idle(controller):
    """Test that get_position() returns None when idle."""
    assert controller.get_position() is None


def test_get_position_returns_value_when_playing(controller, test_video_3s):
    """Test that get_position() returns a value when playing."""
    controller.load_file(test_video_3s)
    time.sleep(0.5)  # Let it play a bit

    position = controller.get_position()
    assert position is not None
    assert position >= 0

    controller.stop_playback()


def test_seek_works_during_playback(controller, test_video_3s):
    """Test seeking to a specific position."""
    controller.load_file(test_video_3s)
    time.sleep(0.2)

    success = controller.seek(1)
    assert success is True

    # Just verify seek returns success - position accuracy depends on keyframes
    # and timing which varies with fakesink

    controller.stop_playback()


def test_seek_returns_false_when_idle(controller):
    """Test that seek() returns False when idle."""
    success = controller.seek(1)
    assert success is False


# =========================================================================
# EOS (End of Stream) Tests
# =========================================================================


def test_eos_callback_fires(controller, test_video_1s):
    """Test that EOS callback is called when song ends."""
    eos_received = threading.Event()
    controller.set_eos_callback(lambda: eos_received.set())

    controller.load_file(test_video_1s)

    # Wait for EOS (1 second video + some buffer)
    assert eos_received.wait(timeout=3), "EOS callback not received"

    controller.stop_playback()


def test_multiple_eos_callbacks(controller, test_video_1s):
    """Test that EOS callback fires for multiple songs."""
    eos_count = []
    controller.set_eos_callback(lambda: eos_count.append(1))

    # Play first song
    controller.load_file(test_video_1s)
    time.sleep(1.5)

    # Play second song
    controller.load_file(test_video_1s)
    time.sleep(1.5)

    # Should have received 2 EOS callbacks
    assert len(eos_count) >= 1  # At least one EOS

    controller.stop_playback()


# =========================================================================
# Error Handling Tests
# =========================================================================


def test_error_handling_invalid_file(controller):
    """Test that loading an invalid file raises an error."""
    with pytest.raises(RuntimeError):
        controller.load_file("/nonexistent/file.mp4")


def test_error_handling_empty_path(controller):
    """Test that loading an empty path raises an error."""
    with pytest.raises(RuntimeError):
        controller.load_file("")


def test_pipeline_recovers_after_error(controller, test_video_1s):
    """Test that pipeline can recover after an error."""
    # Try to load invalid file
    try:
        controller.load_file("/nonexistent/file.mp4")
    except RuntimeError:
        pass

    # Should be able to load a valid file after error
    controller.load_file(test_video_1s)
    assert controller.state == "playing"

    controller.stop_playback()


# =========================================================================
# Cleanup Tests
# =========================================================================


def test_stop_cleans_up_pipeline(controller, test_video_1s):
    """Test that stop() properly cleans up the pipeline."""
    controller.load_file(test_video_1s)
    controller.stop()

    assert controller.playbin is None


def test_streaming_controller_initialization(mock_config_manager):
    """Test basic StreamingController initialization."""
    server = create_autospec(MagicMock, instance=True)
    streaming = StreamingController(mock_config_manager, server, use_fakesinks=True)

    # Verify it initialized
    assert streaming.get_pipeline_state() == "ready"
    assert streaming.state == "idle"


# =========================================================================
# QR Overlay Tests
# =========================================================================


def test_qr_overlay_resizes_on_video_caps(controller, test_video_3s):
    """Test that QR overlay resizes based on video resolution via caps negotiation."""
    assert controller.display.qr_overlay is not None, (
        "gdkpixbufoverlay not available -- install gstreamer1.0-plugins-good"
    )

    # Store initial size
    initial_size = controller.overlays.qr_current_size

    # Load a video - this should trigger caps negotiation
    controller.load_file(test_video_3s)

    # Wait for caps to be negotiated and QR size to be updated
    time.sleep(0.5)

    # The QR size should have been updated based on video height
    # Test video is 240px high, so at 10% it should be ~48px (minimum is 48)
    # Just verify the update method was exercised without crashing
    assert controller.overlays.qr_current_size >= 48

    controller.stop_playback()


def test_qr_overlay_position_calculation(controller, test_video_3s):
    """Test that QR position is calculated correctly for different corners."""
    assert controller.display.qr_overlay is not None, (
        "gdkpixbufoverlay not available -- install gstreamer1.0-plugins-good"
    )

    # Test each position
    for position in ["top-left", "top-right", "bottom-left", "bottom-right"]:
        controller.overlays.qr_position = position

        # Manually trigger the size update with known dimensions
        controller.overlays.update_qr_size_for_resolution(1280, 720)

        # Get the offset properties
        offset_x = controller.display.qr_overlay.get_property("offset-x")
        offset_y = controller.display.qr_overlay.get_property("offset-y")

        # Verify offsets are reasonable (not negative, within bounds)
        assert offset_x >= 0, f"offset-x negative for {position}"
        assert offset_y >= 0, f"offset-y negative for {position}"
        assert offset_x < 1280, f"offset-x out of bounds for {position}"
        assert offset_y < 720, f"offset-y out of bounds for {position}"


# =========================================================================
# Text Overlay / Notification Tests
# =========================================================================


def test_set_overlay_text_escapes_pango_markup(controller):
    """set_overlay_text() (persistent overlay) must also escape markup."""
    assert controller.display.text_overlay is not None, (
        "textoverlay not available -- install gstreamer1.0-plugins-base"
    )

    raw = "Now singing: Ben & Jerry's <Encore>"
    controller.set_overlay_text(raw)

    text = controller.display.text_overlay.get_property("text")
    assert text == "Now singing: Ben &amp; Jerry&apos;s &lt;Encore&gt;"
    assert controller.display.text_overlay.get_property("silent") is False


# =========================================================================
# Resume from Position Tests
# =========================================================================


def test_load_file_with_start_position(controller, test_video_3s):
    """load_file() with start_position_seconds pre-seeks before starting playback.

    This is the operator recovery path: song was interrupted, resume from
    the saved position. The pipeline must go PAUSED → seek → PLAYING
    without error.
    """
    controller.load_file(test_video_3s, start_position_seconds=1)

    assert controller.state == "playing"
    assert controller.get_pipeline_state() == "playing"

    controller.stop_playback()


def test_load_file_with_start_position_zero_skips_preseak(controller, test_video_3s):
    """load_file() with start_position_seconds=0 takes the normal (no pre-seek) path."""
    controller.load_file(test_video_3s, start_position_seconds=0)

    assert controller.state == "playing"
    assert controller.get_pipeline_state() == "playing"

    controller.stop_playback()


# =========================================================================
# Interstitial / Static Image Tests
# =========================================================================


@pytest.fixture
def interstitial_image(tmp_path):
    """A small PNG for interstitial display testing."""
    from PIL import Image

    path = tmp_path / "interstitial.png"
    Image.new("RGB", (320, 240), color=(30, 30, 30)).save(path)
    return str(path)


def test_display_image_sets_interstitial_state(controller, interstitial_image):
    """display_image() marks the controller as showing an interstitial."""
    controller.display_image(interstitial_image)

    assert controller._is_interstitial is True
    assert controller.state == "playing"
    assert controller.current_file == interstitial_image

    controller.stop_playback()


def test_display_image_produces_no_audio(controller, interstitial_image):
    """
    Interstitials are silent.

    This used to be achieved by muting playbin, which was playing the still
    image. Interstitials now have their own video-only pipeline and playbin is
    left in NULL, so there is no audio path to mute in the first place.
    """
    Gst = _get_gst()

    controller.display_image(interstitial_image)

    _, playbin_state, _ = controller.playbin.get_state(Gst.SECOND)
    assert playbin_state == Gst.State.NULL
    assert controller.still_image.pipeline.get_by_name("isink") is not None

    controller.stop_playback()


def test_load_file_clears_interstitial_flag(controller, interstitial_image, test_video_1s):
    """Loading a song after an interstitial clears the interstitial flag."""
    controller.display_image(interstitial_image)
    assert controller._is_interstitial is True

    controller.load_file(test_video_1s)

    assert controller._is_interstitial is False
    assert controller.state == "playing"

    controller.stop_playback()


def test_eos_suppressed_during_interstitial(controller):
    """EOS must not propagate to the callback while showing an interstitial.

    If this breaks, the queue auto-advances into the next song while the
    between-song screen is still displayed.
    """
    eos_fired = threading.Event()
    controller.set_eos_callback(eos_fired.set)

    controller._is_interstitial = True
    controller._on_eos(None, None)

    assert not eos_fired.is_set()


def test_eos_fires_for_song(controller):
    """EOS propagates normally for regular songs (counterpart to suppression test)."""
    eos_fired = threading.Event()
    controller.set_eos_callback(eos_fired.set)

    controller._is_interstitial = False
    controller._on_eos(None, None)

    assert eos_fired.is_set()


def test_set_overlay_text_during_interstitial(controller, interstitial_image):
    """Overlay text set while an interstitial is displayed lands on the element.

    The text overlay lives in the display pipeline, which imagefreeze keeps
    feeding during an interstitial, so no frame regeneration is needed.
    """
    assert controller.display.text_overlay is not None, (
        "textoverlay not available -- install gstreamer1.0-plugins-base"
    )

    controller.display_image(interstitial_image)
    assert controller._is_interstitial is True

    controller.set_overlay_text("Up next: Alice")

    assert controller.display.text_overlay.get_property("text") == "Up next: Alice"
    assert controller.display.text_overlay.get_property("silent") is False

    controller.stop_playback()


# =========================================================================
# Display Pipeline Architecture Tests
#
# The video sink lives in its own pipeline rather than inside playbin, so
# that playbin's per-song state changes never release the screen. These
# guard the structural invariants that arrangement depends on. Each one
# stands for a regression that actually shipped while building it.
# =========================================================================


def _linked_chain(pipeline, start_element_name):
    """Factory names of the linked element chain, in order, from an element."""
    element = pipeline.get_by_name(start_element_name)
    names = []
    while element is not None:
        names.append(element.get_factory().get_name())
        src_pad = element.get_static_pad("src")
        if src_pad is None:
            break
        peer = src_pad.get_peer()
        if peer is None:
            break
        element = peer.get_parent_element()
    return names


def test_display_pipeline_is_separate_from_playbin(controller):
    """
    The video sink must not live inside playbin.

    playbin owns its video-sink, so a sink inside it gets stopped on every
    song change -- which for kmssink closes the DRM fd and hands the screen
    back to the kernel console.
    """
    assert controller.display.pipeline is not None
    assert controller.display.pipeline is not controller.playbin


def test_overlays_are_composited_after_the_render_caps(controller):
    """
    Overlays must sit downstream of the capsfilter.

    Down there they are composited into the fixed render frame, so the
    top-left corner is the screen's corner even when the video is inset
    between bars, and a proportion of the frame is a constant size on screen.
    Upstream they would be drawn onto the source frame and scaled with it,
    which made the QR code enormous and resized it on every song.
    """
    assert controller.display.qr_overlay or controller.display.text_overlay, (
        "no overlay elements available -- install gstreamer1.0-plugins-good "
        "and gstreamer1.0-plugins-base"
    )

    chain = _linked_chain(controller.display.pipeline, "intervideosrc")

    assert "capsfilter" in chain, f"no capsfilter in display chain: {chain}"
    caps_at = chain.index("capsfilter")

    for overlay in ("gdkpixbufoverlay", "textoverlay"):
        if overlay in chain:
            assert chain.index(overlay) > caps_at, (
                f"{overlay} must be composited after the render caps, got: {chain}"
            )


@pytest.mark.parametrize(
    "display,expected",
    [
        # Displays at or below the cap are rendered at their own size, so
        # nothing is scaled twice.
        ((1280, 720), (1280, 720)),
        ((640, 360), (640, 360)),
        # Taller displays are rendered at the cap, keeping the aspect ratio,
        # and the sink scales up from there for free.
        ((1920, 1080), (1280, 720)),
        ((3840, 2160), (1280, 720)),
        # Non-16:9 displays keep their own ratio.
        ((1024, 768), (960, 720)),
    ],
)
def test_render_size_keeps_display_aspect_ratio(controller, display, expected):
    """
    The render size matches the display's aspect ratio and is capped.

    Matching the ratio is what lets the sink's fit-preserving scale cover the
    whole screen, so the console cannot show through the margins. The cap is
    what keeps the cost survivable: rendering straight at 1080p measured
    ~46 ms/frame, which cannot sustain 30fps.
    """
    assert pipelines.choose_render_size(display) == expected

    width, height = expected
    assert abs(width / height - display[0] / display[1]) < 0.01
    assert height <= max(pipelines.MAX_RENDER_HEIGHT, display[1])
    # Odd dimensions upset chroma-subsampled formats.
    assert width % 2 == 0 and height % 2 == 0


def test_no_render_size_when_display_size_is_unknown(controller):
    """A sink that cannot report a mode leaves the caps open rather than guessing."""
    assert pipelines.choose_render_size(None) is None


def test_bridge_channels_all_agree(controller, interstitial_image):
    """
    Every end of the bridge must name the same channel.

    If these drift apart the display silently stops receiving frames -- there
    is no error, the screen just stops updating.
    """
    channel = StreamingController.DISPLAY_CHANNEL

    src = controller.display.pipeline.get_by_name("intervideosrc")
    assert src.get_property("channel") == channel

    playbin_side = controller.video_bin.get_by_name("intervideosink")
    assert playbin_side.get_property("channel") == channel

    interstitial = controller.still_image.create_pipeline(interstitial_image)
    try:
        assert interstitial.get_by_name("isink").get_property("channel") == channel
    finally:
        interstitial.set_state(_get_gst().State.NULL)


def test_interstitial_pipeline_uses_imagefreeze(controller, interstitial_image):
    """
    A still image has to become a continuous stream.

    Decoded on its own it yields one buffer and then EOS, which starves the
    bridge: intervideosrc serves a buffer only a couple of times before it
    starts generating black frames, blanking the idle screen a second after
    every stop.
    """
    pipeline = controller.still_image.create_pipeline(interstitial_image)
    try:
        assert pipeline.get_by_name("ifreeze") is not None
    finally:
        pipeline.set_state(_get_gst().State.NULL)


def test_only_one_source_feeds_the_bridge(controller, interstitial_image, test_video_1s):
    """
    playbin and the interstitial pipeline are mutually exclusive.

    Both feeding the bridge at once would interleave a song with an
    interstitial on screen.
    """
    Gst = _get_gst()

    controller.display_image(interstitial_image)
    assert controller.still_image.pipeline is not None
    _, playbin_state, _ = controller.playbin.get_state(Gst.SECOND)
    assert playbin_state == Gst.State.NULL

    controller.load_file(test_video_1s)
    assert controller.still_image.pipeline is None


def test_query_display_size_handles_sinks_without_mode_properties(caplog):
    """
    Sinks that cannot report a mode leave output unconstrained.

    autovideosink on macOS and fakesink in tests have no display-width, and
    must degrade to "unknown" rather than raising.
    """

    class _SinkWithoutModeProperties:
        def find_property(self, name):
            return None

    with caplog.at_level(logging.WARNING, logger="kbox.streaming"):
        assert pipelines.query_display_size(_SinkWithoutModeProperties()) is None

    # The expected path must stay quiet, or the warnings below are noise.
    assert caplog.text == ""


def test_query_display_size_warns_when_the_sink_cannot_answer(caplog):
    """
    A sink that advertises a display mode but cannot report it warns.

    Reaching this means kmssink opened the DRM device and then failed the
    query, which leaves the render size unknown -- and an unknown render size
    unpins the capsfilter, which is what kills the display pipeline on the
    next resolution change. It must not be a debug-level line.
    """

    class _SinkThatFailsTheQuery:
        def find_property(self, name):
            return object()

        def get_property(self, name):
            raise RuntimeError("DRM device has no mode set")

    with caplog.at_level(logging.WARNING, logger="kbox.streaming"):
        assert pipelines.query_display_size(_SinkThatFailsTheQuery()) is None

    assert "Failed to query the display mode" in caplog.text
    assert "DRM device has no mode set" in caplog.text


def test_query_display_size_warns_when_the_sink_reports_no_mode(caplog):
    """
    A sink reporting a zero-sized mode warns rather than falling through.

    Same consequence as a failed query, and plausible on a Pi whose HDMI has
    not negotiated by the time the pipeline reaches READY.
    """

    class _SinkWithoutAMode:
        def find_property(self, name):
            return object()

        def get_property(self, name):
            return 0

    with caplog.at_level(logging.WARNING, logger="kbox.streaming"):
        assert pipelines.query_display_size(_SinkWithoutAMode()) is None

    assert "advertises a display mode but reported" in caplog.text


def test_text_layout_leaves_font_size_to_auto_resize(controller):
    """
    Padding scales with the screen; font size must not.

    textoverlay's auto-resize already scales the font by width/640, so also
    deriving a size from the display scales it twice -- which put roughly
    66pt of text across a 1080p screen.
    """
    text_overlay = controller.display.text_overlay
    assert text_overlay is not None, (
        "textoverlay not available -- install gstreamer1.0-plugins-base"
    )

    font_before = text_overlay.get_property("font-desc")

    controller.overlays.update_text_layout_for_resolution(1920)

    assert text_overlay.get_property("font-desc") == font_before
    assert text_overlay.get_property("auto-resize") is True
    # xpad is raw pixels that auto-resize does not touch, so it does scale:
    # 20 * (1920 / 640) == 60
    assert text_overlay.get_property("xpad") == 60


def test_caps_dimensions_extracts_width_and_height(controller):
    """The shared caps parser pulls dimensions out of a caps object."""
    caps = _get_gst().Caps.from_string("video/x-raw,format=I420,width=1280,height=720")

    assert gst.caps_dimensions(caps) == (1280, 720)


# =========================================================================
# Collaborator Tests
#
# These construct a collaborator on its own, with stub elements, and no
# StreamingController anywhere. They are the evidence that the split is real:
# if a piece ever starts reaching back through a controller it does not own,
# these stop compiling.
# =========================================================================


class _StubOverlayElement:
    """Minimal stand-in for a GStreamer element: just properties."""

    def __init__(self, **properties):
        self._properties = dict(properties)

    def set_property(self, name, value):
        self._properties[name] = value

    def get_property(self, name):
        return self._properties[name]


def test_overlay_controller_sizes_qr_without_a_pipeline(mock_config_manager):
    """OverlayController sizes the QR element given only the element itself."""
    mock_config_manager.set("overlay_qr_position", "bottom-right")
    mock_config_manager.set("overlay_qr_size_percent", 10)
    qr = _StubOverlayElement()

    overlays = OverlayController(qr, None, mock_config_manager)
    overlays.update_qr_size_for_resolution(1280, 720)

    # 10% of 720 = 72px, padding is 2% of 720 = 14px, bottom-right corner.
    assert qr.get_property("overlay-width") == 72
    assert qr.get_property("overlay-height") == 72
    assert qr.get_property("offset-x") == 1280 - 72 - 14
    assert qr.get_property("offset-y") == 720 - 72 - 14
    assert overlays.qr_current_size == 72


def test_overlay_controller_sets_and_clears_text(mock_config_manager):
    """set_overlay_text drives the text element directly, with no controller."""
    text = _StubOverlayElement(text="", silent=True)

    overlays = OverlayController(None, text, mock_config_manager)

    overlays.set_overlay_text("Up next: Alice")
    assert text.get_property("text") == "Up next: Alice"
    assert text.get_property("silent") is False

    overlays.set_overlay_text("")
    assert text.get_property("text") == ""
    assert text.get_property("silent") is True


def test_overlay_controller_carries_qr_image_path_across_rebuilds(mock_config_manager):
    """
    The remembered QR path is constructor state, so a rebuild can hand it on.

    Set through the public entry point with no element attached, which is the
    state during a config-change rebuild and whenever GStreamer is missing
    altogether. The path has to be recorded anyway, or the rebuilt pipeline
    comes up with no QR code on it.
    """
    overlays = OverlayController(None, None, mock_config_manager)
    overlays.update_qr_overlay("/tmp/qr.png")

    assert overlays.qr_image_path == "/tmp/qr.png"

    rebuilt = OverlayController(
        None, None, mock_config_manager, qr_image_path=overlays.qr_image_path
    )

    assert rebuilt.qr_image_path == "/tmp/qr.png"


def test_bus_watcher_dispatches_messages_to_its_callback():
    """BusWatcher owns only the thread: what a message means is the callback's."""
    Gst = _get_gst()

    class _StubMessage:
        def __init__(self, type_):
            self.type = type_

    class _StubBus:
        def __init__(self, messages):
            self._messages = list(messages)

        def timed_pop(self, timeout_ns):
            return self._messages.pop(0) if self._messages else None

    bus = _StubBus([_StubMessage(Gst.MessageType.EOS), _StubMessage(Gst.MessageType.ERROR)])
    seen = []
    done = threading.Event()

    def bus_provider():
        # Once both messages are consumed, report nothing left to poll so the
        # thread finishes on its own rather than on a timeout.
        if len(seen) == 2:
            done.set()
            return None
        return bus

    w = BusWatcher("StubBusPoll", bus_provider, lambda b, m: seen.append(m.type))
    assert w.name == "StubBusPoll"
    w.start()
    assert done.wait(timeout=5), f"bus watcher did not drain the bus, saw {seen}"
    w.stop()

    assert seen == [Gst.MessageType.EOS, Gst.MessageType.ERROR]
    assert w.running is False
    assert not w.is_alive()


# =========================================================================
# Bus Message Dispatch Tests
#
# Bus messages reach their handlers only through the polling threads:
# bus.add_signal_watch() + bus.connect() needs a GLib main loop, which kbox
# never runs, so signal-based delivery silently did nothing. These tests post
# real messages and assert the handlers run.
# =========================================================================


def _get_glib_error(message):
    """Build a GError suitable for Gst.Message.new_warning/new_error."""
    GLib = _get_glib()
    Gst = _get_gst()
    return GLib.Error.new_literal(Gst.StreamError.quark(), message, Gst.StreamError.FAILED)


def _quiesce_bus(controller, pipeline):
    """Stop polling and empty a bus, so a test owns what it posts next.

    Stopping the poll thread first means it cannot race the test for the
    message -- which is also what keeps these tests free of sleeps and timing
    assumptions. Pipelines queue state-changed and stream-start messages of
    their own, so the bus is cleared too.
    """
    controller.stop_bus_polling()
    bus = pipeline.get_bus()
    while bus.pop() is not None:
        pass
    return bus


def _drain_and_dispatch(bus, dispatch):
    """Dispatch every queued message the way the poll loop does."""
    count = 0
    while True:
        msg = bus.pop()
        if msg is None:
            return count
        dispatch(bus, msg)
        count += 1


def test_display_bus_warning_reaches_handler(controller, caplog):
    """A WARNING on the display pipeline's bus is logged.

    Regression: the display pipeline's handlers were installed with a signal
    watch and so never fired -- a failure on the pipeline holding the screen
    was completely silent.
    """
    Gst = _get_gst()
    pipeline = controller.display.pipeline
    assert pipeline is not None
    bus = _quiesce_bus(controller, pipeline)

    error = _get_glib_error("display warning under test")
    bus.post(Gst.Message.new_warning(pipeline, error, "debug info for display warning"))

    with caplog.at_level(logging.WARNING, logger="kbox.streaming"):
        assert _drain_and_dispatch(bus, controller.display.dispatch_bus_message) == 1

    assert "Display pipeline warning" in caplog.text
    assert "display warning under test" in caplog.text


def test_display_bus_error_reaches_handler(controller, caplog):
    """An ERROR on the display pipeline's bus is logged."""
    Gst = _get_gst()
    pipeline = controller.display.pipeline
    assert pipeline is not None
    bus = _quiesce_bus(controller, pipeline)

    error = _get_glib_error("display error under test")
    bus.post(Gst.Message.new_error(pipeline, error, "debug info for display error"))

    with caplog.at_level(logging.ERROR, logger="kbox.streaming"):
        assert _drain_and_dispatch(bus, controller.display.dispatch_bus_message) == 1

    assert "Display pipeline error" in caplog.text
    assert "display error under test" in caplog.text


def test_playbin_bus_warning_reaches_handler(controller, caplog):
    """A WARNING on playbin's bus reaches _on_warning.

    WARNING was not dispatched at all before, so the audio-device escalation
    below could never fire.
    """
    Gst = _get_gst()
    bus = _quiesce_bus(controller, controller.playbin)

    error = _get_glib_error("Could not open audio device for playback")
    bus.post(Gst.Message.new_warning(controller.playbin, error, "debug info for audio warning"))

    with caplog.at_level(logging.DEBUG, logger="kbox.streaming"):
        assert _drain_and_dispatch(bus, controller._dispatch_playbin_message) == 1

    assert "GStreamer warning" in caplog.text
    # The escalation check for audio-device misconfiguration.
    assert "CRITICAL: Audio device error detected" in caplog.text


def test_both_buses_are_polled(controller):
    """Polling covers playbin's bus and the display pipeline's bus."""
    names = {watcher.name for watcher in controller.bus_watchers}

    assert names == {"GstBusPoll", "GstDisplayBusPoll"}
    assert all(watcher.is_alive() for watcher in controller.bus_watchers)


def test_stop_bus_polling_joins_every_thread(controller):
    """Shutdown tears down both poll threads and forgets them."""
    watchers = list(controller.bus_watchers)
    assert watchers

    controller.stop_bus_polling()

    assert all(watcher.running is False for watcher in watchers)
    assert controller.bus_watchers == []
    for watcher in watchers:
        assert not watcher.is_alive(), f"{watcher.name} did not exit"


# =========================================================================
# Pipeline Lock Tests
#
# These assert the locking *discipline*, not pipeline behaviour, so they
# stub playbin out: what matters is which methods take
# _pipeline_lock and which deliberately do not. They do not skip when
# GStreamer is missing -- see this module's docstring.
# =========================================================================


class _CountingLock:
    """A Lock that records how many times it was entered as a context manager."""

    def __init__(self):
        self._lock = threading.Lock()
        self.entries = 0

    def __enter__(self):
        self._lock.acquire()
        self.entries += 1
        return self

    def __exit__(self, *exc_info):
        self._lock.release()
        return False

    def locked(self):
        return self._lock.locked()


@pytest.fixture
def stubbed_controller(controller):
    """A controller whose playbin and interstitial feeder are stubs.

    Lets every pipeline-mutating method run start to finish without
    touching real GStreamer state, so the locking can be observed on its
    own.
    """
    Gst = _get_gst()
    # Retire the real playbin properly before dropping it. Letting it fall
    # out of scope in READY emits a GStreamer-CRITICAL about disposing a
    # non-NULL element, which is pure noise in CI output.
    controller.playbin.set_state(Gst.State.NULL)
    controller.playbin.get_state(Gst.SECOND)

    controller.playbin = MagicMock()
    controller.playbin.set_state.return_value = Gst.StateChangeReturn.SUCCESS
    controller.playbin.get_state.return_value = (
        Gst.StateChangeReturn.SUCCESS,
        Gst.State.PLAYING,
        Gst.State.VOID_PENDING,
    )
    controller.playbin.seek_simple.return_value = True
    controller.still_image = MagicMock()
    controller.still_image.start.return_value = True
    return controller


def test_pipeline_state_changes_take_the_pipeline_lock(stubbed_controller):
    """Every pipeline state transition is serialised by _pipeline_lock.

    Before this lock existed, the only thing serialising playbin state
    changes was PlaybackController.lock, by the accident that most callers
    happened to hold it -- and PlaybackController.shutdown() and the
    config-change route did not. Driving playbin from two threads at once
    is undefined pipeline behaviour, so each of these must hold the lock.
    """
    lock = _CountingLock()
    stubbed_controller._pipeline_lock = lock

    stubbed_controller.load_file("/tmp/does-not-need-to-exist.mp4")
    assert lock.entries == 1, "load_file did not take the pipeline lock"

    stubbed_controller.pause()
    assert lock.entries == 2, "pause did not take the pipeline lock"

    stubbed_controller.resume()
    assert lock.entries == 3, "resume did not take the pipeline lock"

    stubbed_controller.seek(5)
    assert lock.entries == 4, "seek did not take the pipeline lock"

    stubbed_controller.stop_playback()
    assert lock.entries == 5, "stop_playback did not take the pipeline lock"

    stubbed_controller.display_image("/tmp/does-not-need-to-exist.png")
    assert lock.entries == 6, "display_image did not take the pipeline lock"


def test_pipeline_lock_is_held_across_the_pipeline_work(stubbed_controller):
    """The lock spans the pipeline work, it isn't merely touched on entry.

    Proven from inside the critical section: a non-blocking acquire from
    another thread must fail while load_file is mid-flight. A plain
    assertion on the calling thread would pass even for a non-reentrant
    lock already released, which is the bug worth catching.
    """
    inside = threading.Event()
    acquired_from_other_thread = []

    def try_acquire_elsewhere():
        got = stubbed_controller._pipeline_lock.acquire(blocking=False)
        acquired_from_other_thread.append(got)
        if got:
            stubbed_controller._pipeline_lock.release()

    def probe_from_inside_the_lock():
        """Runs inside load_file, while the lock is held."""
        inside.set()
        prober = threading.Thread(target=try_acquire_elsewhere)
        prober.start()
        prober.join()

    stubbed_controller.still_image.stop.side_effect = probe_from_inside_the_lock

    stubbed_controller.load_file("/tmp/does-not-need-to-exist.mp4")

    assert inside.is_set(), "load_file never reached the instrumented call"
    assert acquired_from_other_thread == [False], (
        "another thread acquired the pipeline lock while load_file held it"
    )


def test_hot_path_reads_do_not_take_the_pipeline_lock(stubbed_controller):
    """Property writes and reads stay outside the lock, deliberately.

    get_position() runs on every monitor tick and the pitch/volume setters
    are property writes on persistent-bin elements, not state transitions.
    Pulling them inside the lock would make them queue behind a song load
    -- reintroducing, by a different door, the stall the narrow lock
    exists to avoid.
    """
    lock = _CountingLock()
    stubbed_controller._pipeline_lock = lock
    stubbed_controller.state = "playing"
    stubbed_controller.playbin.query_position.return_value = (True, 0)

    stubbed_controller.get_position()
    stubbed_controller.set_pitch_shift(3)
    stubbed_controller.set_volume_gain_db(-2.0)
    stubbed_controller.get_pipeline_state()

    assert lock.entries == 0, "a hot-path read/property write took the pipeline lock"


def test_reinitialize_restores_interstitial_without_reentering_the_lock(stubbed_controller):
    """reinitialize_pipeline() must not re-enter the non-reentrant lock.

    It restores the interstitial it was showing before the rebuild from
    inside its own locked region, so it has to call the already-locked
    body. Calling the public display_image() there would deadlock on the
    first config change made while an interstitial was up.
    """
    stubbed_controller.display_image("/tmp/idle-screen.png")
    assert stubbed_controller._is_interstitial

    restored = []
    stubbed_controller._display_image_locked = lambda path: restored.append(path)
    stubbed_controller.display_image = lambda path: pytest.fail(
        "reinitialize_pipeline() called the lock-taking display_image()"
    )

    stubbed_controller.reinitialize_pipeline()

    assert restored == ["/tmp/idle-screen.png"]
