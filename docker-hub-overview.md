# kbox

A self-contained, open-source karaoke system. Guests add songs from their phones, a fullscreen display page shows the lyrics video on any TV or monitor, and the queue advances on its own so there is no dead air.

Source, docs and issues: https://github.com/mdz/kbox

## Features

- **Mobile-first web UI** for searching karaoke tracks, managing the queue and seeing when your turn is coming up
- **AI song suggestions** via LiteLLM (OpenAI, Anthropic, Google, Ollama and others)
- **Fullscreen display page** (`/display`) with the current singer and who is up next
- **Name-only identity** for guests, with QR-code join links; no accounts or passwords
- **PIN-protected operator controls** for skip, pause, seek, reorder and pitch shifting

## Tags

Images are published for each release as multi-arch manifests (`linux/amd64` and `linux/arm64`, so Raspberry Pi works).

| Tag | Meaning |
|---|---|
| `X.Y.Z` | An exact release, e.g. `0.1.0` |
| `X.Y`, `X` | The newest release in that minor / major line |
| `latest` | The newest stable (non-pre-release) version |

Pre-releases (`-alpha`, `-rc`) are published under their exact tag only and never move `latest`. Browse the available versions on the [Tags tab](https://hub.docker.com/r/mdzz/kbox/tags) and substitute one for `<version>` in the examples below, or use `latest` for the newest verified release.

## Quick start

```bash
docker run -d --name kbox --restart unless-stopped \
  --device /dev/snd --device /dev/dri \
  -p 8000:8000 \
  -v kbox-data:/root/.kbox \
  -v kbox-library:/root/.kbox/library \
  mdzz/kbox:<version>
```

Then open `http://localhost:8000` and add your YouTube API key in Settings.

- `/dev/snd` provides audio output and `/dev/dri` provides video output.
- `kbox-data` holds the database and configuration; `kbox-library` holds downloaded songs.
- The web UI listens on port 8000.

## Docker Compose

```yaml
services:
  kbox:
    image: mdzz/kbox:<version>
    devices:
      - "/dev/snd:/dev/snd"
      - "/dev/dri:/dev/dri"
    ports:
      - "8000:8000"
    volumes:
      - kbox-data:/root/.kbox
      - kbox-library:/root/.kbox/library
    restart: unless-stopped

volumes:
  kbox-data:
  kbox-library:
```

## License

See the [LICENSE](https://github.com/mdz/kbox/blob/main/LICENSE) in the repository.
