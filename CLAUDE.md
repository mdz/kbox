# Git Behavior

## Committing Changes

1. Add new files with `git add <specific-file>` before committing
2. Commit with `git commit -a` (all modified tracked files) or `git commit <files>` (subset)

## What NOT to do

- NEVER use `git add -A`, `git add .`, or `git add --all`
- NEVER add untracked files — they may be local notes, logs, or temporary data

## What Belongs in the Repo

**Commit:** source code, dependency specs, project config, user-facing documentation

**Do NOT commit:** personal notes, planning docs, internal architecture docs, TODOs, local convenience scripts, one-time setup utilities

Guiding question: "Would someone cloning this repo to USE the software need this file?" If no, don't commit. When uncertain, ask before committing.

# Debugging

## Use Existing Loggers

- Use `self.logger` — classes already have one
- Use DEBUG level: `self.logger.debug('[DEBUG] ...')`
- Don't invent new logging mechanisms (no custom file writes, HTTP endpoints, new logging infra)
- Code may run in Docker or on remote machines — the existing logger already handles this

## Revert Failed Fixes

1. Revert the failed change first — don't pile hacks on top of failed hacks
2. Return to the last known good state before trying a new approach
3. One change at a time, so we know exactly what worked or didn't

## Known Benign Log Warnings

These show up periodically and are not indicative of a kbox bug — don't chase them:

- **yt-dlp: "GVS PO Token which was not provided" / android client formats skipped** —
  ongoing YouTube-vs-yt-dlp arms race over bot-detection tokens. yt-dlp falls back to
  another client automatically; only worth investigating if extraction actually fails
  outright (no playable format found), not just this warning appearing.

# Testing

This is a multimedia project with GStreamer pipelines, audio/video hardware, and real-time playback. pytest tests use mocks and fakesinks — they verify logic but don't test the actual pipeline.

Before a PR with substantial changes (especially to streaming, playback, queue, or pipeline code) can be merged:

1. Run pytest as a quick sanity check
2. Ask the user to test end-to-end — run the actual app, play a song, verify it works
3. Only merge after the user confirms e2e testing passes

Don't merge based on pytest alone for changes that could affect the real pipeline.

# Tech Stack

- **Python package/env manager: `uv`.** Always use `uv` — do not call `pip`, `python -m venv`, or a bare `python`/`pytest` directly.
- [FastAPI](https://fastapi.tiangolo.com/) + Jinja2 templates for the web server.
- SQLite for persistent storage (queue, config, history).
- **Display/playback**: two supported backends — a **GStreamer** pipeline (real audio/video pipeline, hardware output) and the [YouTube IFrame Player API](https://developers.google.com/youtube/iframe_api_reference) embedded on the `/display` page.
- **Search**: [YouTube Data API v3](https://developers.google.com/youtube/v3) when an API key is configured, with a `youtube-dl` / `yt-dlp` fallback when it isn't.
- [LiteLLM](https://github.com/BerriAI/litellm) for AI suggestions and metadata extraction (works with OpenAI, Anthropic, Google, Ollama, etc.).
- Runs on macOS, Linux/Raspberry Pi, and in Docker.

## Common Commands

```bash
uv sync --group dev          # install deps (incl. dev tools)
uv run python -m kbox.main   # run the app (http://localhost:8000)
uv run pytest                # tests
uv run ruff check .          # lint
uv run mypy kbox/            # type-check
```

## Driving kbox via Browser Automation

Before using a browser-automation tool against a live kbox instance (e.g. for burn-in
testing), see [docs/development/BROWSER_AUTOMATION_NOTES.md](docs/development/BROWSER_AUTOMATION_NOTES.md) —
covers native `confirm()` dialogs being silently auto-declined, search form quirks, and
the operator-auth vs. controls-unlock distinction.

# Product Context

This software runs karaoke parties. Primary focus is home karaoke; bar/KJ environments are a secondary consideration (keep flexibility, don't hardcode one mode).

## Design Principles

- **Reliability is paramount.** Mid-song failures embarrass performers and break the shared experience. Prefer graceful recovery (resume from saved position, retry) over clever features.
- **The technology gets out of the way.** Minimize friction everywhere — in identity, in adding songs, in operator controls.
- **Continuous flow.** Dead air kills the energy. Songs auto-advance; the next singer is announced before the current song ends.
- **Self-service for guests.** The host often wants to sing too and shouldn't be stuck on IT duty. Guests manage their own song selections via mobile.

## Identity

- Users identify by **first name or nickname only** — no logins, passwords, emails, or accounts.
- Duplicate names are disambiguated lightly (e.g., "Mike" vs "Mike B."), never via formal registration.

## Queue & Flow

- Songs auto-advance when one ends.
- Display the next singer's name subtly as the current song wraps (not distracting).
- **Fairness**: a non-blocking "soft nudge" (never a hard block) warns a guest when they add a second unplayed song, since there are legitimate reasons for it (adding for someone else, duets, fixing a mistake). Toggle: `duplicate_singer_nudge_enabled` config (group "queue", default on) — operators running strict FCFS can turn it off.
- **No-shows**: handled via the general-purpose "Play Next" / reorder controls (no dedicated bump-down feature — removed in favor of this simpler, more general mechanism).
- **Duets**: idea, not currently implemented — see [issue #130](https://github.com/mdz/kbox/issues/130). No code exists for treating a duet as a single turn today.
- Show queue position, estimated wait time, and song download status on mobile — reduces anxiety and helps people be ready.

## Operator Controls

- Controls are **locked by default** (PIN-protected) to prevent accidental disruption.
- Operator must be able to: skip, pause/resume, seek (recovery), reorder queue, remove songs, adjust pitch per-song.
- Keep controls accessible but unobtrusive — no "million buttons."
- UI must be mobile-friendly so the operator isn't chained to one device.

## Display

- Performer and audience typically see the **same mirrored content** (lyrics/karaoke video on both screens).
- Between songs: show the next performer's name prominently; song title is optional (surprise is part of the fun).
