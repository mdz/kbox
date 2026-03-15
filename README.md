# kbox

A self-contained, open-source karaoke system with a mobile-friendly web interface, AI-powered song suggestions, and a fullscreen browser display for any TV or monitor.

## Features

### Web Interface
- **Mobile-first design** — dark-themed, touch-optimized UI that works great on phones
- **Search** — find karaoke tracks instantly with ranked results that prioritize known karaoke channels and filter out non-karaoke content
- **Queue management** — add songs, reorder, remove, and see real-time status updates
- **"My next turn" indicator** — see how many songs until you're up and the estimated wait time
- **Long song warning** — configurable confirmation prompt for songs over a certain length

### AI-Powered Suggestions
- **"Suggest for me"** — get personalized song recommendations based on your history, the current queue, and an operator-configured party theme
- **Powered by LiteLLM** — works with OpenAI, Anthropic, Google, Ollama, or any compatible provider

### Display Page
- **Fullscreen browser-based display** (`/display`) for your TV or monitor
- Embeds the YouTube IFrame Player — just open the page and point it at a screen
- Automatically advances through the queue with configurable transition screens between songs
- Shows the current singer's name and who's up next

### User System
- **Name-based identity** — enter your name on first visit, tracked via browser session
- **Personal history** — view your past performances
- **Session security** — UUID-based identity bound to server sessions to prevent impersonation
- **Guest access tokens** — scan a QR code to join

### Operator Mode
- **PIN-protected controls** — unlock playback controls (play/pause, skip, previous, restart, seek) and queue management
- **Controls lock** — operator panel is locked by default to prevent accidental taps
- **Full queue control** — jump to any song, reorder, move to end, clear queue
- **User history access** — operators can view any user's performance history

### Configuration
All settings are managed through the web UI with a schema-driven settings panel:

| Category | Settings |
|---|---|
| **Display** | Transition duration |
| **AI** | Model, API key, base URL, creativity/temperature, party theme |
| **Security** | Operator PIN, guest access token |
| **Queue** | Long song warning threshold |

## Technology

- [FastAPI](https://fastapi.tiangolo.com/) web server with Jinja2 templates
- SQLite for persistent storage (queue, config, history)
- [YouTube IFrame Player API](https://developers.google.com/youtube/iframe_api_reference) for the display page
- [YouTube Data API v3](https://developers.google.com/youtube/v3) for search
- [LiteLLM](https://github.com/BerriAI/litellm) for AI suggestions and metadata extraction

## Quick Start (Docker)

```bash
docker-compose build
docker-compose up
```

Then open `http://localhost:8000` in your browser and configure your YouTube API key in Settings.

## Quick Start (Native)

```bash
uv sync
uv run python -m kbox.main
```

Open `http://localhost:8000` on your phone to queue songs, and `http://localhost:8000/display` on your TV/monitor for the fullscreen karaoke display. Configure your YouTube API key in Settings.

## Platform Support

- **macOS** — full support via Homebrew dependencies
- **Linux / Raspberry Pi** — optimized for direct display output
- **Docker** — containerized deployment

See `docs/HARDWARE_SETUP.md` for audio hardware setup (microphone input, mixing, signal flow).

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run mypy kbox/
```

## License

See [LICENSE](LICENSE).
