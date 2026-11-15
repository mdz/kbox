# Backups

kbox keeps its queue, play history, guests and settings in one SQLite file,
`kbox.db`, in the `kbox-data` volume (`~/.kbox` outside Docker).

## Automatic snapshots

Each time the queue is cleared (the step that ends a party), kbox first
writes a snapshot of the database to `backups/` next to `kbox.db`. Snapshots
are taken with SQLite's online backup, so they are safe while kbox is running.

- Nothing is written if the database hasn't changed since the last snapshot.
- The newest 20 are kept.
- Files are named `kbox-YYYYMMDD-HHMMSS.db` and are readable only by their owner.
- A failed snapshot is logged and never stops the queue from clearing.

**Secrets are left out.** The guest access token, session secret and API
keys (LLM, YouTube) are removed from snapshots, so a copy is safe to store
elsewhere. After a restore, kbox generates a new access token and session
secret itself; re-enter your API keys under Settings. Snapshots still contain
guest first names and play history, so keep them somewhere private.

## Copy snapshots off the device

Snapshots on the same device are lost with it. Pull them to another machine
regularly, for example after each party. With Docker:

```bash
docker compose cp kbox:/root/.kbox/backups ./kbox-backups
```

Without Docker, copy `~/.kbox/backups/` with `rsync` or `scp`.

## Manual snapshot, list and restore

Run these inside the container (`docker compose exec kbox ...`) or from a
checkout with `uv run`:

```bash
python -m kbox.backup create                    # snapshot now
python -m kbox.backup create --include-secrets  # keep tokens/API keys (guard the file!)
python -m kbox.backup list
```

To restore, stop kbox, restore, and start it again. The previous database is
kept as `kbox.db.pre-restore`.

```bash
docker compose stop kbox
docker compose run --rm kbox python -m kbox.backup restore /root/.kbox/backups/kbox-YYYYMMDD-HHMMSS.db
docker compose start kbox
```

To restore a snapshot you copied back from another machine, first copy it
into the volume (for example with `docker compose cp` while the container
exists) and pass that path.
