# kbox

A self-contained, open-source karaoke system with YouTube integration, real-time pitch shifting, and a mobile-friendly web interface.

## Features

- **YouTube Integration**: Search and queue karaoke videos directly from YouTube
- **Real-time Pitch Shifting**: Adjust pitch per song using rubberband
- **Web Interface**: Mobile-friendly queue management and playback controls
- **User System**: Track songs by user with persistent history
- **Operator Mode**: PIN-protected controls for playback and queue management

## Technology

- [GStreamer](https://gstreamer.freedesktop.org/) with [gst-python](https://gstreamer.freedesktop.org/bindings/python.html) for audio/video playback
- [rubberband](https://breakfastquay.com/rubberband/) via [LADSPA](https://www.ladspa.org/) for pitch shifting
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) for YouTube downloads
- [FastAPI](https://fastapi.tiangolo.com/) for the web server

## Quick Start (Docker)

```bash
docker-compose build
docker-compose run --rm kbox python3 configure_api_key.py YOUR_YOUTUBE_API_KEY
docker-compose up
```

Then open `http://localhost:8000` in your browser.

## Quick Start (Native)

### Prerequisites

**Debian/Ubuntu:**
```bash
sudo apt install python3-gst-1.0 gstreamer1.0-alsa rubberband-ladspa \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-good ffmpeg
```

**macOS:**
See `ldocs/SETUP_MACOS.md` for GStreamer and rubberband setup.

### Run

```bash
# Install dependencies
uv sync

# Configure YouTube API key (one-time)
uv run python configure_api_key.py YOUR_YOUTUBE_API_KEY

# Start the server
uv run python -m kbox.main
```

## Hardware Setup

kbox requires external audio hardware for microphone input and mixing. See the [Hardware Setup Guide](docs/HARDWARE_SETUP.md) for detailed information on:
- Simple setups with audio interfaces (e.g., Focusrite Scarlett Solo)
- Advanced setups with mixers and home theater systems
- Signal flow diagrams and troubleshooting

## Configuration

Configuration is stored in SQLite and managed through the web interface:

- **Operator PIN**: Default is `1234` (change in Settings)
- **YouTube API Key**: Required for search functionality
- **Audio Devices**: Auto-detected, configurable in Settings

## Development

```bash
# Install dev dependencies
uv sync --group dev

# Run tests
uv run pytest

# Run linting
uv run ruff check .
uv run mypy kbox/
```

## License

See [LICENSE](LICENSE).
