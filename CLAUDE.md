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
