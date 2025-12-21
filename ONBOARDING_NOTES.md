# Onboarding / Setup Notes

This file tracks setup steps, gotchas, and improvements needed for a better onboarding experience.

## Current Issues / TODOs

### Critical Setup Issues

1. **YouTube API key required at startup** (blocking)
   - Server exits with error if API key not configured
   - Can't use web UI to configure it (catch-22)
   - Current workaround: Use `configure_api_key.py` script or `setup_api_key.sh` before first run
   - **Fix needed**: Allow server to start without API key, show configuration UI as first step

2. **Database location in Docker**
   - Database stored at `/root/.kbox/kbox.db` inside container
   - Must use named volume for persistence (not bind mount to ./data)
   - **Fix needed**: Make database location configurable via environment variable

3. **First-time setup flow**
   - No guided setup wizard
   - Operator PIN (default 1234) not obvious
   - YouTube API key setup confusing
   - **Fix needed**: Welcome screen with setup wizard on first run

## Setup Steps (Current)

### Prerequisites
- YouTube Data API v3 key from Google Cloud Console
- Raspberry Pi 5 with HDMI display, audio capture card, MIDI keyboard (optional)

### Docker Deployment

1. **Deploy code:**
   ```bash
   ./deploy.sh <pi-ip>
   ```

2. **On Pi, build and set API key:**
   ```bash
   cd /home/pi/kbox
   docker-compose build
   docker-compose run --rm kbox python3 configure_api_key.py YOUR_API_KEY
   docker-compose up
   ```

3. **Access web UI:**
   - Navigate to `http://<pi-ip>:8000` from phone/browser
   - Enter name when prompted
   - Enter operator PIN (default: 1234) to access controls

### Native Deployment

1. **One-time system setup:**
   ```bash
   sudo apt update
   sudo apt install python3-gst-1.0 gstreamer1.0-alsa python3-mido python3-rtmidi \
       rubberband-ladspa gstreamer1.0-plugins-bad gstreamer1.0-plugins-good \
       python3-pip python3-venv ffmpeg
   ```

2. **Deploy code:**
   ```bash
   ./deploy.sh <pi-ip>
   ```

3. **On Pi, setup Python environment:**
   ```bash
   cd /home/pi/kbox
   python3 -m venv .venv --system-site-packages
   source .venv/bin/activate
   pip install fastapi uvicorn jinja2 itsdangerous google-api-python-client yt-dlp
   ```

4. **Configure API key:**
   ```bash
   python configure_api_key.py YOUR_API_KEY
   ```

5. **Run:**
   ```bash
   python -m kbox.main
   ```

## Configuration Steps Needed

1. **Audio devices** (ALSA)
   - Find input device: `arecord -l`
   - Find output device: `aplay -l`
   - Set in config: `audio_input_device`, `audio_output_device`
   - Example: `hdmi:CARD=vc4hdmi0,DEV=0` for HDMI audio

2. **MIDI device** (optional)
   - Find MIDI devices: `amidi -l`
   - Set in config: `midi_input_name`

3. **Operator PIN** (optional)
   - Default: `1234`
   - Change via config screen or database

4. **Cache directory** (optional)
   - Default: `~/.cache/kbox`
   - Set in config: `cache_directory`

## Proposed Onboarding Flow

### Phase 1: First Run Wizard

1. **Welcome screen** (if no API key configured)
   - "Welcome to kbox! Let's set things up."
   - Step 1: Enter YouTube API key
     - Link to instructions: SETUP_YOUTUBE_API.md
     - Input field with "I'll do this later" option
   - Step 2: Configure audio devices
     - Show detected devices
     - Test buttons (play test tone, record test)
   - Step 3: Set operator PIN
     - Default: 1234 (changeable)
   - Step 4: Test playback
     - Play a test video to verify everything works

2. **Configuration persistence**
   - Save to database immediately
   - Can skip steps and return later via config screen

### Phase 2: Guided Configuration

1. **Audio setup wizard**
   - Detect all ALSA devices
   - Play test tone through each output device
   - Record test from each input device
   - Visual feedback for working devices

2. **MIDI setup wizard** (if MIDI keyboard connected)
   - Detect MIDI devices
   - Test: "Press any key on your keyboard"
   - Show detected notes in real-time

3. **Display setup**
   - Test video output
   - Adjust resolution if needed
   - Verify KMS is working

### Phase 3: Health Checks

1. **Startup diagnostics**
   - Check all required dependencies
   - Verify GStreamer plugins (kmssink, ladspa)
   - Test audio/video device access
   - Report any issues with clear fix instructions

2. **Runtime health monitoring**
   - Log playback errors
   - Monitor download failures
   - Track API quota usage

## Known Gotchas

1. **KMS video requires console mode**
   - X11 must not be running
   - Video output only works in console/TTY mode
   - Docker handles this automatically

2. **Rubberband LADSPA plugin**
   - Must be compiled and installed to `~/.ladspa/`
   - Or system-wide to `/usr/lib/ladspa/`
   - Check with: `gst-inspect-1.0 ladspa`

3. **HDMI audio device names**
   - Pi 5: `hdmi:CARD=vc4hdmi0,DEV=0`
   - May vary by Pi model
   - Use `aplay -l` to find correct name

4. **Database permissions in Docker**
   - Container runs as root
   - Database created at `/root/.kbox/kbox.db`
   - Volume mounts must preserve permissions

## Future Improvements

- [ ] First-run setup wizard
- [ ] Configuration validation and testing
- [ ] Health check system
- [ ] Better error messages with fix suggestions
- [ ] Guided audio device setup
- [ ] API key validation on input
- [ ] Test mode for setup verification
- [ ] Backup/restore configuration
- [ ] Configuration import/export


