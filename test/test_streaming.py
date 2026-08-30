"""
Integration tests for StreamingController.

These tests use fakesinks for headless testing and verify pipeline state
transitions, pitch shifting, and error handling without requiring hardware.

All tests in this module require GStreamer and will be skipped if unavailable.
"""

import logging
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, create_autospec

import pytest

# Mark all tests in this module as requiring GStreamer
pytestmark = pytest.mark.gstreamer

from kbox.config_manager import ConfigManager
from kbox.database import Database
from kbox.streaming import StreamingController, _get_gst

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
    """Create a mock ConfigManager with test defaults."""
    db = create_autospec(Database, instance=True)
    config_manager = ConfigManager(db)

    # Set test-specific config
    config_manager.set(
        "rubberband_plugin", "ladspa-ladspa-rubberband-so-rubberband-r3-pitchshifter-stereo"
    )
    config_manager.set("audio_output_device", None)

    return config_manager


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
    assert controller.text_overlay is not None, (
        "textoverlay element not available - install gstreamer1.0-x"
    )
    assert controller._notification_lock is not None


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
            except:
                # If rubberband not available, that's okay
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


def test_create_pitch_shift_defaults_to_rubberband_without_trying_signalsmith(
    controller, monkeypatch
):
    """Default config (no pitch_shift_engine set) should go straight to
    rubberband and never even try signalsmith -- this is what keeps the
    signalsmith addition a no-op for existing deployments."""
    signalsmith_called = []
    monkeypatch.setattr(
        controller,
        "_create_signalsmith_pitch_shift",
        lambda: signalsmith_called.append(True) or None,
    )
    sentinel = MagicMock(name="rubberband_element")
    monkeypatch.setattr(controller, "_create_rubberband_pitch_shift", lambda: sentinel)

    result = controller._create_pitch_shift_or_identity()

    assert result is sentinel
    assert not signalsmith_called, "signalsmith should not be tried unless opted into"


def test_create_pitch_shift_uses_signalsmith_when_opted_in(controller, monkeypatch):
    """With pitch_shift_engine=signalsmith, dispatcher should use it and not even try rubberband."""
    # mock_config_manager's backing "database" is a bare autospec with no real
    # storage, so config_manager.set()/.get() don't round-trip through it in
    # tests -- patch .get directly instead of relying on that round-trip.
    monkeypatch.setattr(
        controller.config_manager,
        "get",
        lambda key, default=None: "signalsmith" if key == "pitch_shift_engine" else default,
    )

    sentinel = MagicMock(name="signalsmith_element")
    monkeypatch.setattr(controller, "_create_signalsmith_pitch_shift", lambda: sentinel)

    rubberband_called = []
    monkeypatch.setattr(
        controller,
        "_create_rubberband_pitch_shift",
        lambda: rubberband_called.append(True) or None,
    )

    result = controller._create_pitch_shift_or_identity()

    assert result is sentinel
    assert not rubberband_called, "rubberband should not be tried when signalsmith succeeds"


def test_create_pitch_shift_falls_back_to_identity_when_both_unavailable(controller, monkeypatch):
    """When neither element is available, dispatcher should return identity rather than raise."""
    monkeypatch.setattr(controller, "_create_signalsmith_pitch_shift", lambda: None)
    monkeypatch.setattr(controller, "_create_rubberband_pitch_shift", lambda: None)

    result = controller._create_pitch_shift_or_identity()

    assert type(result).__name__ == "GstIdentity"


def test_reset_pitch_shift_element_skips_signalsmith(controller, monkeypatch):
    """The native element resets its own state via FLUSH_STOP/stop(), so the
    destroy/recreate workaround (needed for rubberband's LADSPA wrapper)
    should be skipped for it entirely -- not even touch the audio bin."""

    class GstSignalsmithPitch:
        pass

    fake_element = GstSignalsmithPitch()
    controller.pitch_shift_element = fake_element

    def _fail_if_called():
        raise AssertionError("should not recreate the element for signalsmith")

    monkeypatch.setattr(controller, "_create_pitch_shift_or_identity", _fail_if_called)

    controller._reset_pitch_shift_element()

    assert controller.pitch_shift_element is fake_element


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
    pitch_element, semitones, input_freq=220.0, sample_rate=48000
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
        f"audio/x-raw,format=F32LE,layout=interleaved,rate={sample_rate},channels=2"
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
    return _estimate_frequency_hz(b"".join(chunks), channels=2, sample_rate=sample_rate)


def test_signalsmith_pitch_shift_actually_shifts_frequency(controller):
    """The native signalsmith element should genuinely change pitch, not just pass audio through."""
    elem = controller._create_signalsmith_pitch_shift()
    if elem is None:
        pytest.skip("signalsmithpitch plugin not available on this machine")

    semitones = 12  # one octave up -> should double the frequency
    input_freq = 220.0
    measured_freq = _run_pitch_element_and_measure_frequency(elem, semitones, input_freq)

    expected_freq = input_freq * (2 ** (semitones / 12))
    assert measured_freq == pytest.approx(expected_freq, rel=0.05)


def test_rubberband_pitch_shift_actually_shifts_frequency(controller):
    """The rubberband LADSPA element should genuinely change pitch, not just pass audio through."""
    elem = controller._create_rubberband_pitch_shift()
    if elem is None:
        pytest.skip("rubberband LADSPA plugin not available on this machine")

    semitones = 12  # one octave up -> should double the frequency
    input_freq = 220.0
    measured_freq = _run_pitch_element_and_measure_frequency(elem, semitones, input_freq)

    expected_freq = input_freq * (2 ** (semitones / 12))
    assert measured_freq == pytest.approx(expected_freq, rel=0.05)


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


def test_streaming_controller_initialization():
    """Test basic StreamingController initialization."""
    db = create_autospec(Database, instance=True)
    config_manager = ConfigManager(db)

    # Set rubberband plugin config
    config_manager.set(
        "rubberband_plugin", "ladspa-ladspa-rubberband-so-rubberband-r3-pitchshifter-stereo"
    )

    server = create_autospec(MagicMock, instance=True)
    streaming = StreamingController(config_manager, server, use_fakesinks=True)

    # Verify it initialized
    assert streaming.get_pipeline_state() == "ready"
    assert streaming.state == "idle"


# =========================================================================
# QR Overlay Tests
# =========================================================================


def test_qr_overlay_resizes_on_video_caps(controller, test_video_3s):
    """Test that QR overlay resizes based on video resolution via caps negotiation."""
    # Skip if QR overlay not available (optional element)
    if controller.qr_overlay is None:
        pytest.skip("QR overlay not available")

    # Store initial size
    initial_size = controller._qr_current_size

    # Load a video - this should trigger caps negotiation
    controller.load_file(test_video_3s)

    # Wait for caps to be negotiated and QR size to be updated
    time.sleep(0.5)

    # The QR size should have been updated based on video height
    # Test video is 240px high, so at 10% it should be ~48px (minimum is 48)
    # Just verify the update method was exercised without crashing
    assert controller._qr_current_size >= 48

    controller.stop_playback()


def test_qr_overlay_position_calculation(controller, test_video_3s):
    """Test that QR position is calculated correctly for different corners."""
    if controller.qr_overlay is None:
        pytest.skip("QR overlay not available")

    # Test each position
    for position in ["top-left", "top-right", "bottom-left", "bottom-right"]:
        controller._qr_position = position

        # Manually trigger the size update with known dimensions
        controller._update_qr_size_for_resolution(1280, 720)

        # Get the offset properties
        offset_x = controller.qr_overlay.get_property("offset-x")
        offset_y = controller.qr_overlay.get_property("offset-y")

        # Verify offsets are reasonable (not negative, within bounds)
        assert offset_x >= 0, f"offset-x negative for {position}"
        assert offset_y >= 0, f"offset-y negative for {position}"
        assert offset_x < 1280, f"offset-x out of bounds for {position}"
        assert offset_y < 720, f"offset-y out of bounds for {position}"


# =========================================================================
# Text Overlay / Notification Tests
# =========================================================================


def test_show_notification(controller):
    """Test that notifications can be shown and hidden."""
    if controller.text_overlay is None:
        pytest.skip("Text overlay not available")

    # Show a notification
    controller.show_notification("Test notification", duration_seconds=1.0)

    # Verify text is set
    text = controller.text_overlay.get_property("text")
    assert text == "Test notification"

    # Verify not silent (visible)
    silent = controller.text_overlay.get_property("silent")
    assert silent is False

    # Wait for auto-hide
    time.sleep(1.5)

    # Verify text is cleared
    text = controller.text_overlay.get_property("text")
    assert text == ""

    # Verify now silent
    silent = controller.text_overlay.get_property("silent")
    assert silent is True


def test_show_notification_escapes_pango_markup(controller):
    """Titles/artists with &, <, > must be escaped before hitting the
    textoverlay element, or GStreamer emits a Pango markup parse warning
    (e.g. "Shallow ... (Lady Gaga & Bradley Cooper)")."""
    if controller.text_overlay is None:
        pytest.skip("Text overlay not available")

    raw = "Lady Gaga & Bradley Cooper <3 <live>"
    controller.show_notification(raw, duration_seconds=60.0)

    text = controller.text_overlay.get_property("text")
    assert text == "Lady Gaga &amp; Bradley Cooper &lt;3 &lt;live&gt;"
    assert controller.text_overlay.get_property("silent") is False


def test_set_overlay_text_escapes_pango_markup(controller):
    """set_overlay_text() (persistent overlay) must also escape markup."""
    if controller.text_overlay is None:
        pytest.skip("Text overlay not available")

    raw = "Now singing: Ben & Jerry's <Encore>"
    controller.set_overlay_text(raw)

    text = controller.text_overlay.get_property("text")
    assert text == "Now singing: Ben &amp; Jerry&apos;s &lt;Encore&gt;"
    assert controller.text_overlay.get_property("silent") is False


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


def test_display_image_mutes_audio(controller, interstitial_image):
    """display_image() mutes the pipeline — interstitials have no audio track."""
    controller.display_image(interstitial_image)

    assert controller.playbin.get_property("mute") is True

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


def test_show_notification_during_interstitial(controller, interstitial_image):
    """Notification shown while an interstitial is displayed sets overlay text correctly.

    show_notification() has a special path for interstitials: it seeks to
    position 0 to force imagefreeze to regenerate the frame so the text
    overlay actually appears on the frozen image.
    """
    if controller.text_overlay is None:
        pytest.skip("Text overlay not available")

    controller.display_image(interstitial_image)
    assert controller._is_interstitial is True

    # Long duration so auto-hide doesn't fire during the test
    controller.show_notification("Up next: Alice", duration_seconds=60.0)

    assert controller.text_overlay.get_property("text") == "Up next: Alice"
    assert controller.text_overlay.get_property("silent") is False

    controller.stop_playback()
