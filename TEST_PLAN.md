# Hardware End-to-End Test Plan for kbox

## Phase 0: Deployment

### Option A: Docker Deployment

```bash
# On your Mac
./deploy.sh <pi-ip>

# On the Pi
cd /home/pi/kbox
docker-compose build
docker-compose up
```

### Option B: Native Deployment (faster iteration)

If Docker rebuilds are too slow, run natively on the Pi:

```bash
# One-time setup on Pi
sudo apt update
sudo apt install python3-gst-1.0 gstreamer1.0-alsa python3-mido python3-rtmidi \
    rubberband-ladspa gstreamer1.0-plugins-bad gstreamer1.0-plugins-good \
    python3-pip python3-venv ffmpeg

# Create venv and install Python deps
cd /home/pi/kbox
python3 -m venv .venv --system-site-packages  # Include system GStreamer
source .venv/bin/activate
pip install fastapi uvicorn jinja2 itsdangerous google-api-python-client yt-dlp

# Run directly
python -m kbox.main
```

**Iteration workflow:**

```bash
# Quick sync from Mac (after initial setup)
./deploy.sh <pi-ip>

# On Pi - just restart
cd /home/pi/kbox
source .venv/bin/activate
pkill -f "kbox.main" || true
python -m kbox.main
```

### Pre-Deployment Checklist

- [ ] Sync code to Pi via `./deploy.sh <pi-ip>`
- [ ] Choose deployment method (Docker vs Native)
- [ ] Verify YouTube API key is configured in database
- [ ] Confirm LADSPA/rubberband plugin is installed
- [ ] Check GStreamer plugins: `gst-inspect-1.0 kmssink` and `gst-inspect-1.0 ladspa`

### Hardware Connections

- [ ] HDMI connected to display (note resolution)
- [ ] Audio capture card connected (USB)
- [ ] MIDI keyboard connected (USB)
- [ ] Network connected (WiFi or Ethernet)
- [ ] Note the Pi's IP address for web UI access

---

## Phase 1: Infrastructure Tests

### 1.1 Server Startup

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Server starts | Run `python -m kbox.main` | Logs show "kbox is running!" with IP | |
| No GStreamer errors | Check startup logs | No GStreamer init errors | |
| Web UI accessible | Navigate to `http://<pi-ip>:8000` from phone | Page loads | |

### 1.2 Configuration

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Config screen loads | Tap gear icon (as operator) | Config form shows | |
| Audio devices detected | Check `audio_input_device`, `audio_output_device` | ALSA devices listed | |
| Save config | Change a value, save | Success message | |

---

## Phase 2: Core User Journey

### 2.1 User Registration

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Name prompt | Load web UI (fresh browser) | Name modal appears | |
| Name saved | Enter name, submit | Modal closes, name persisted | |

### 2.2 YouTube Search

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Search works | Enter "bohemian rhapsody" | Results with thumbnails appear | |
| Auto-karaoke | Check results | Results include "karaoke" versions | |
| Select song | Tap a result | Add song modal appears | |

### 2.3 Queue Management

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Add song | Confirm add from modal | Song appears in queue | |
| Download starts | Observe queue | Status changes: pending -> downloading -> ready | |
| Multiple songs | Add 3+ songs | All queue, download in background | |
| Pitch preset | Set pitch to +2 before adding | Pitch saved with song | |

### 2.4 Operator Authentication

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| PIN prompt | Tap playback controls button | Prompted for PIN | |
| Wrong PIN | Enter wrong PIN | Error message | |
| Correct PIN | Enter correct PIN (default: 1234) | Operator mode enabled | |

---

## Phase 3: Playback Tests (Critical for Hardware)

### 3.1 Video Playback (KMS Sink)

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| First song plays | Press Play as operator | Video appears on HDMI display | |
| Video fills screen | Observe display | Proper scaling, no letterboxing issues | |
| Video smooth | Watch for 30 seconds | No stuttering or dropped frames | |
| Resolution correct | Check display | Matches expected resolution | |

**Troubleshooting notes for KMS sink issues:**

- Check `/var/log/syslog` for KMS errors
- Verify display mode: `cat /sys/class/drm/card*/status`
- Try forcing resolution in config if needed

### 3.2 Audio Playback

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Audio plays | During video playback | Audio from speakers (HDMI or other) | |
| Audio sync | Watch lip sync | Audio matches video (no drift) | |
| Volume adequate | Listen | Sufficient volume level | |
| No crackling | Listen for 2+ minutes | Clean audio, no pops/clicks | |

**Latency measurement:**

- Use a clap test video to measure audio-video sync
- Target: <50ms latency

### 3.3 Pitch Shifting

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Pitch +2 | Tap + button twice in operator controls | Audio noticeably higher | |
| Pitch -2 | Tap - button twice | Audio noticeably lower | |
| Pitch instant | Adjust during playback | Change is immediate (<1 second) | |
| Pitch display | Check UI | Shows correct semitones and interval name | |
| Pitch reset on skip | Skip to next song | Pitch resets to song's default | |

### 3.4 MIDI Keyboard Control

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| MIDI detected | Check startup logs | MIDI device found | |
| Middle C | Press C4 on keyboard | Pitch resets to 0 | |
| Higher note | Press E4 | Pitch shifts +4 semitones | |
| Lower note | Press A3 | Pitch shifts -3 semitones | |

---

## Phase 4: Queue Flow Tests

### 4.1 Auto-Advance

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Song ends | Let song play to completion | Next song starts automatically | |
| Queue updates | Check queue display | Previous song removed, next highlighted | |
| Download ahead | Queue 5+ songs | Later songs download while playing | |

### 4.2 Manual Controls

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Skip | Press Skip button | Jumps to next song | |
| Previous | Press Previous | Goes back to previous song | |
| Pause | Press Pause | Playback pauses, video freezes | |
| Resume | Press Play | Playback resumes smoothly | |

### 4.3 Queue Manipulation

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Play Now | Select queued song, tap "Play Now" | Immediately jumps to that song | |
| Play Next | Tap "Play Next" | Song moves to position 2 | |
| Move to End | Tap "Move to End" | Song goes to end of queue | |
| Remove | Tap "Remove from Queue" | Song removed | |

---

## Phase 5: User Experience Features

### 5.1 "Your Turn" Screen

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Notification | When user's song is next | "Coming up" notification appears | |
| Your turn | When user's song plays | Full-screen "Your Turn" appears | |
| Live pitch | Adjust pitch on Your Turn screen | Changes apply immediately | |

### 5.2 Song History

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Remember pitch | Play song with +3 pitch, skip, queue same song | Defaults to +3 pitch | |
| Multiple users | Different users play same song | Each gets their own history | |

---

## Phase 6: Error Handling

### 6.1 Download Errors

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Network down | Disconnect network, try search | Graceful error message | |
| Bad video ID | Queue invalid video | Error status in queue | |
| Skip error | Skip past errored song | Continues to next song | |

### 6.2 Playback Recovery

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| Empty queue | Skip last song | Idle state, no crash | |
| Corrupt file | Delete cached file mid-play | Error handling, skips to next | |

---

## Phase 7: Burn-In / Stress Test

### 7.1 Extended Session

| Test | Duration | Monitor | Pass/Fail |
|------|----------|---------|-----------|
| 2-hour session | Queue 20+ songs, let play | No memory leaks, stable playback | |
| CPU usage | During playback | Stays reasonable (<80% sustained) | |
| Temperature | During extended play | No thermal throttling | |

### 7.2 Multi-User Simulation

| Test | Steps | Expected | Pass/Fail |
|------|-------|----------|-----------|
| 3+ devices | Connect 3 phones to web UI | All see same queue state | |
| Concurrent adds | Add songs from multiple devices | No race conditions | |
| Rapid operations | Quick skip/pause/play | Responsive, no crashes | |

---

## Hardware-Specific Notes

### Video Display (KMS)

The `kbox/streaming.py` uses `kmssink` on Linux. If video doesn't display:

1. Check DRM device: `ls /dev/dri/`
2. Verify KMS support: `cat /sys/class/drm/card*/status`
3. May need to run without X11 (console mode) for KMS to work

### Audio Latency

Key configuration in `kbox/config_manager.py`:

- `audio_output_device`: Should be `hdmi:CARD=vc4hdmi0,DEV=0` or similar
- GStreamer pipeline uses direct ALSA for low latency

### ALSA Device Discovery

Run these to find correct device names:

```bash
arecord -l  # List capture devices
aplay -l    # List playback devices
```

---

## Sign-Off Checklist

Before the party:

- [ ] All Phase 1-4 tests pass
- [ ] 30-minute unattended burn-in successful
- [ ] Backup of working configuration exported
- [ ] Known issues documented with workarounds
- [ ] Emergency fallback plan (can revert to f43307b if needed)


