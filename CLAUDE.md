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

# Testing

This is a multimedia project with GStreamer pipelines, audio/video hardware, and real-time playback. pytest tests use mocks and fakesinks — they verify logic but don't test the actual pipeline.

Before committing substantial changes (especially to streaming, playback, queue, or pipeline code):

1. Run pytest as a quick sanity check
2. Ask the user to test end-to-end — run the actual app, play a song, verify it works
3. Only commit after user confirms e2e testing passes

Don't commit based on pytest alone for changes that could affect the real pipeline.

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
- **Configurable fairness rules** — e.g., "one song queued per person" is a common house rule, but first-come-first-served must also be supported. Don't hardcode.
- **No-shows**: bump the singer down the queue (not to the end), advance to the next.
- **Duets**: count as one person's turn; the other joins without "using their turn."
- Show queue position, estimated wait time, and song download status on mobile — reduces anxiety and helps people be ready.

## Operator Controls

- Controls are **locked by default** (PIN-protected) to prevent accidental disruption.
- Operator must be able to: skip, pause/resume, seek (recovery), reorder queue, remove songs, adjust pitch per-song.
- Keep controls accessible but unobtrusive — no "million buttons."
- UI must be mobile-friendly so the operator isn't chained to one device.

## Display

- Performer and audience typically see the **same mirrored content** (lyrics/karaoke video on both screens).
- Between songs: show the next performer's name prominently; song title is optional (surprise is part of the fun).
