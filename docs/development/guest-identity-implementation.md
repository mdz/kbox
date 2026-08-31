# Guest Identity: Technical Design

Implements the "name-keyed identity" candidate direction from
[`guest-identity.md`](guest-identity.md). That document
settles the product/UX questions (pure recognition, zero authentication,
ghost identities accepted, renaming out of scope, etc.) — this document
doesn't re-litigate them. Read it first; this doc assumes its conclusions and
answers "how do we build it."

## Baseline: what exists today

(Full detail for anyone modifying this code; skip if you already know it.)

- `users` table (`kbox/database.py:71-76`): `id TEXT PRIMARY KEY` (client-generated
  UUID v4), `display_name TEXT NOT NULL`, `created_at`. No unique constraint
  on `display_name`, no normalization, no icon/color/avatar column.
- `UserRepository` (`kbox/database.py:515-582`) and `UserManager.get_or_create_user`
  (`kbox/user.py:28-52`) look up **only by `id`**. There is no lookup-by-name
  path anywhere in the codebase — identity resolution is 100% "whatever UUID
  the client happens to send."
- `POST /api/users` (`kbox/web/server.py:1021-1061`): first call from a browser
  binds `request.session["user_id"]` (Starlette signed-cookie session) to
  whatever `user_id` the client sends; every subsequent call from that
  session ignores the request body's `user_id` and uses the session's. This
  is what the `# This prevents user impersonation` docstring refers to — it
  stops a stale/concurrent request from switching a session's bound identity,
  not name collisions between different guests.
- Client flow (`kbox/web/static/js/auth.js`): `initializeUserIdentity()` reads
  `localStorage['kbox_user_id']`/`['kbox_user_name']`. If both present, it
  silently re-registers to resync the cookie. If either is missing, it shows
  `#name-modal`; `saveUserName()` generates a UUID (`generateUUID()` in
  `utils.js`) only if one isn't already held client-side, persists both to
  `localStorage`, and calls `registerUser()`, i.e. `POST /api/users`.
- A patched `window.fetch` (`auth.js:57-71`) retries any `/api/*` call that
  comes back 401/403 by re-running `registerUser(userId, userName)` once —
  a backstop for a session-cookie race where a concurrent unauthenticated
  poll response can clobber a just-set `user_id`. It never touches
  `/api/users` or `/api/auth/*`, and it always resends the client's
  **current** in-memory `userId`/`userName` — it cannot itself cause an
  identity switch, which matters below.
- `favorites`, `queue_items`, `playback_history`, `user_events` all hold
  `user_id TEXT` with no `FOREIGN KEY` constraint (SQLite schema has none
  anywhere) — joins are manual, in application code. `queue_items` and
  `playback_history` also store a **denormalized `user_name` copy** at write
  time, not a live join to `users.display_name`.
- `GET /api/history/{user_id}` and `GET /api/favorites/{user_id}` 403 unless
  `current_user_id == user_id`, with comments framing this as
  impersonation prevention.
- Schema migrations live in `kbox/database.py`, gated by
  `if current_version < N:` blocks against `SCHEMA_VERSION` (currently `6`,
  set at `kbox/database.py:31`). Each migration is a `_create_x`/`_add_x`
  method that checks `PRAGMA table_info` before `ALTER TABLE`, so it's safe
  to run against both fresh and existing databases.
- `sessions` table / `kbox/session.py` (PR #99) is an unrelated concept — a
  "party session" bookended by clear-queue, used to tag
  `queue_items.session_id`/`playback_history.session_id`. It does not scope
  `users` or the cookie session. Name-keyed identity should **not** be scoped
  to party sessions — a guest's identity needs to resolve the same way
  whether it's their first song tonight or their fifth party this year.

## What changes

Per the conceptual doc, the UUID stays the real primary key and stays what
everything foreign-keys against. What's new:

1. A **normalized-name lookup** so the server can find existing identities
   for a typed name.
2. A **recognition/claim flow** — client shows candidates, guest picks one or
   says "I'm new," client tells the server which UUID it's now representing.
3. **Self-chosen-with-a-default icon/color** per identity, for telling
   candidates apart at a glance.
4. Comment/docstring updates so the retired "impersonation" framing doesn't
   get "fixed" as a regression later.
5. A one-time, operator-run **migration script** to coalesce existing
   guests' duplicate identities (separate from the schema migration).

Everything else — session-cookie mechanics, the fetch-patch race backstop,
FK shape, favorites/history 403s, the fact that identity is unverified and
self-declared — is unchanged. This is deliberately a small, additive change
to a system that already does most of the hard part (session binding,
write-gating) correctly.

## Data model

### `users` table — new columns (schema version 7)

```sql
ALTER TABLE users ADD COLUMN normalized_name TEXT;
ALTER TABLE users ADD COLUMN icon TEXT;
ALTER TABLE users ADD COLUMN color TEXT;
ALTER TABLE users ADD COLUMN last_seen_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_users_normalized_name ON users(normalized_name);
```

- **`normalized_name`**: trimmed, whitespace-collapsed, case-folded form of
  `display_name`, computed in Python (not SQL — SQLite has no built-in
  whitespace-collapse, and normalization is exactly the kind of business
  logic this codebase keeps in Python rather than in schema). Not declared
  `NOT NULL` at the SQLite level (SQLite can't add a `NOT NULL` column
  without a table rebuild); enforced instead by always computing and setting
  it in `UserRepository.create()`/`update_display_name()` — same pattern the
  denormalized `user_name` columns already use elsewhere in this schema.
  Not unique — multiple rows sharing a `normalized_name` is the expected,
  designed-for case (that's what a name collision *is*).
- **`icon`/`color`**: nullable in principle, but always populated at creation
  time (see below) — never left blank in practice, so callers can treat them
  as present.
- **`last_seen_at`**: updated every time a session (re)binds to this user —
  both the quiet resync path and the explicit claim path. Feeds the "last
  visit" context shown in a recognition list; see below.

### Icon/color assignment

The conceptual doc frames icon/color as "self-chosen... picked the first
time that name needed disambiguating." Taken literally, that only covers the
*second* guest under a name — the first guest isn't present to pick anything
retroactively when someone else later collides with them, so a literal
implementation leaves the earlier record icon-less until (if ever) that guest
happens to type their name again.

This design deviates slightly for simplicity: **every user gets a
deterministic default icon/color at row-creation time**, derived from their
UUID —

```python
PALETTE = [("🦊", "#e67e22"), ("🐼", "#2ecc71"), ("🐙", "#9b59b6"), ...]

def pick_avatar(user_id: str) -> tuple[str, str]:
    idx = int(hashlib.sha1(user_id.encode()).hexdigest(), 16) % len(PALETTE)
    return PALETTE[idx]
```

— so a candidate list never has to render a blank slot, and the one-time
migration (below) can backfill existing rows the same way, with no special
case for "the collision happened before this feature existed." This is
still consistent with "self-chosen": the default is exactly what a guest
sees when they're first shown a collision, and the client-side recognition
UI lets them override it for their own identity at that moment (persisted via
the same claim call). It's a default, not a lock-in.

### Deriving recognition-list context

"Last song" isn't a column anywhere — it's derived per candidate at lookup
time:

```sql
SELECT video_id, song_metadata_json, performed_at
FROM playback_history
WHERE user_id = ?
ORDER BY performed_at DESC
LIMIT 1
```

Candidate lists are always small (bounded by how many real people have ever
shared a first name at this party), so one extra indexed query per candidate
is not a performance concern — no need to denormalize this onto `users`.

## API changes

### `GET /api/users/lookup?name=<raw name>`

New, unauthenticated (no session required — this runs *before* identity is
established), read-only.

```json
{
  "normalized_name": "vlad",
  "candidates": [
    {
      "user_id": "3f9e...",
      "display_name": "Vlad",
      "icon": "🦊",
      "color": "#e67e22",
      "last_seen_at": "2026-08-20T21:14:00Z",
      "last_song_title": "Take On Me"
    }
  ]
}
```

Empty `candidates` = genuinely new name, client skips straight to "type it,
go." Server normalizes the query param the same way it normalizes on write,
so the client sends the raw typed string and never needs its own copy of the
normalization rule.

### `POST /api/users/claim`

New. Body: `{"user_id": "<uuid>", "display_name": "<raw typed name>"}`. This
is the **only** thing that gates on it. Behaviorally identical to today's
`get_or_create_user` at the manager layer (create if absent, else touch
`display_name`/`normalized_name`), but with one deliberate difference in
`server.py`: it **unconditionally overwrites**
`request.session["user_id"]`, even if the session already has a different
`user_id` bound.

```python
@app.post("/api/users/claim")
async def claim_user(
    request: Request,
    request_data: UserRequest,
    user_mgr: UserManager = Depends(get_user_manager),
):
    """
    Explicitly claim an identity — either an existing one the guest
    recognized from a lookup, or a freshly generated UUID for "I'm new."

    Unlike /api/users, this always (re)binds the session, even if it was
    already bound to something else. That's intentional: this endpoint is
    only called from the human-driven recognition flow (name entry after
    localStorage was empty), which is precisely the situation where a
    stale session binding is no longer trustworthy. It is never called by
    the fetch-patch race backstop, which only ever resends the client's
    current identity via /api/users and so can't trigger an identity
    switch on its own.
    """
    user = user_mgr.get_or_create_user(
        user_id=request_data.user_id, display_name=request_data.display_name
    )
    request.session["user_id"] = user.id
    user_mgr.touch_last_seen(user.id)
    return user
```

This is intentionally unrestricted in the same way the rest of the design
is: claiming `user_id` for an existing candidate the guest picked, or one
they typed straight out of a `lookup` response, requires no proof — "typing
Matt is sufficient, full stop" per the conceptual doc. UUIDs remain
effectively unguessable (122 bits from `crypto.randomUUID()`), and this
system has explicitly decided that isn't a threat worth defending against
at these stakes. Worth stating outright so it doesn't read as an oversight
to a future reviewer.

### `POST /api/users` — unchanged behavior, narrowed purpose

Kept exactly as it works today: binds `session["user_id"]` only if unbound,
otherwise ignores the request body and uses the session's existing id. Two
callers remain:

- The "I already know who I am" resync on page load, when
  `localStorage['kbox_user_id']` is already present (no lookup/recognition
  needed — see client flow below).
- The `window.fetch` race backstop, which must never be able to switch
  identity mid-session — this is exactly why it keeps calling the
  ignore-if-already-bound endpoint rather than `/claim`.

Docstring gets updated to drop the impersonation framing (see below) but the
code itself doesn't change.

### `/api/history/{user_id}` and `/api/favorites/{user_id}`

No behavior change. They still 403 unless `current_user_id == user_id`. That
gate has nothing to do with proving identity — it's about which *session*
currently holds which UUID, which is exactly as meaningful post-recognition
as it was before (a session that just claimed "Vlad" via recognition is now
authoritatively representing Vlad for write purposes, same as a session that
got a fresh UUID today). Only the comments change.

## Client flow (`auth.js`)

```
initializeUserIdentity()
├─ localStorage has both kbox_user_id and kbox_user_name?
│    └─ yes → registerUser(id, name)  [POST /api/users, unchanged]
│              ("I already know who I am" — no recognition needed)
└─ no → show #name-modal
         guest types a name
         → GET /api/users/lookup?name=<typed>
         ├─ candidates empty → generate UUID, POST /api/users/claim, done
         │                     ("nothing changes from today")
         └─ candidates non-empty → show recognition list
                  (icon, color, display_name, last_seen_at, last_song_title)
                  + a "None of these — I'm new" option
              guest taps a candidate
                  → POST /api/users/claim with that candidate's user_id
              guest taps "I'm new"
                  → generate UUID, POST /api/users/claim with the new id
              either way: persist resolved id/name to localStorage,
              resolve identityReadyPromise, hide modal
```

Concretely:

- `saveUserName()` splits into two steps: on name entry, call
  `lookup()` instead of immediately generating a UUID and registering. Only
  after the guest resolves the candidate list (or the list comes back empty)
  does a UUID get generated (if needed) and `claim()` get called.
- New `#recognition-modal` (or a second state of `#name-modal`) rendering the
  candidate list — plain template, no new framework needed; follows the
  existing modal pattern in `kbox/web/templates/components/modals.html`.
- `localStorage` writes move to *after* claim resolves, not before —
  otherwise a guest who backs out mid-recognition (closes the tab) could end
  up with a locally-cached identity the server never actually bound.
- The within-night storage-churn case (Vlad/Lessa) is now just this same
  flow running again: `localStorage` empties mid-party, next action re-shows
  the modal, guest types "Vlad," sees themselves as the (likely) single
  candidate, taps once, done — a re-prompt instead of a silent new identity,
  exactly as the conceptual doc calls for.

## Migrating existing identities (operator-run, one-time)

Separate from the schema migration above — this is the data cleanup the
conceptual doc's "Migrating existing identities" section flags as necessary
but out of scope to fully design. Since there are no SQL foreign-key
constraints anywhere in this schema, merging two UUIDs into one is a
straightforward per-table `UPDATE` + delete of the now-empty user row — no
cascade logic needed:

```python
def merge_users(db, keep_id: str, merge_id: str):
    """Fold merge_id's history into keep_id. Operator-invoked, one-time use."""
    for table, col in [
        ("favorites", "user_id"),
        ("queue_items", "user_id"),
        ("playback_history", "user_id"),
        ("user_events", "user_id"),
    ]:
        # favorites has a (user_id, video_id) PK — a song favorited under
        # both ids would collide; skip rows that already exist under keep_id
        # rather than erroring the whole merge over one duplicate favorite.
        ...
    db.execute("DELETE FROM users WHERE id = ?", (merge_id,))
```

This should be a small script under `scripts/` (or a `kbox` CLI subcommand,
whichever this codebase's convention favors for one-off operator tools —
check for an existing `scripts/` directory or `__main__` pattern before
picking), run manually by the operator against a specific party's known
regulars. Not a web endpoint — no guest-facing surface for this, per the
conceptual doc's design principle that operator involvement here is one-time
migration only, never an ongoing mechanism.

## Comment/docstring updates

The conceptual doc explicitly calls out that these should change so they
don't get "fixed" as a regression later by someone without this context.
Behavior at every one of these sites is unchanged — only the framing:

| Location | Current framing | Update to |
|---|---|---|
| `register_user` docstring, `kbox/web/server.py:1027-1032` | "This prevents user impersonation" | Explain it prevents a *stale request* from switching a session's already-bound identity — not guest-vs-guest impersonation, which this system doesn't defend against |
| `get_user_history`, `kbox/web/server.py:~1064-1080` | "prevent impersonation" | "identity is self-declared and unverified; this gates writes/reads to the session that currently holds this UUID, nothing more" |
| `get_user_favorites`, `kbox/web/server.py:~1135-1148` | same | same |
| Any `# ignore request_data.user_id to prevent impersonation` comments on mutating endpoints (e.g. `add_song`) | same | same |

## Explicitly not changing / out of scope

Matches the conceptual doc's non-goals, called out here so they're not
mistaken for gaps in this design:

- **No party-session scoping.** `kbox/session.py`'s "party session" concept
  (PR #99) is unrelated and stays unrelated — a guest's identity must resolve
  identically whether it's their first song tonight or their tenth party.
- **No auto-merge/auto-prune of ghost identities.** Mis-taps or "I'm new"
  guesses permanently add a row; nothing here reconciles that automatically.
- **No renaming support.** Typing a different name is indistinguishable from
  a new identity; no code handles "Mike" becoming "Mike B." as an operation
  on an existing row.
- **No additional proof/credential anywhere**, including on `/claim` — see
  the explicit callout above.

## Testing

- Unit coverage (pytest, no pipeline/hardware involved — this is pure
  database/HTTP logic, well within what pytest-with-mocks actually verifies
  per this repo's testing conventions):
  - Normalization function: whitespace collapse, case-fold, trim, idempotence.
  - `lookup`: empty-name-space case, single candidate, multi-candidate,
    ordering (recommend `last_seen_at DESC`).
  - `claim`: creates when `user_id` absent; rebinds session even when
    already bound to a different id; updates `display_name`/`normalized_name`
    on an existing id.
  - `/api/users` unchanged-behavior regression test: still refuses to rebind
    an already-bound session.
  - Migration: running schema upgrade against a v6 database backfills
    `normalized_name`/`icon`/`color` for all existing rows and is idempotent
    (safe to run twice).
- This isn't streaming/playback/pipeline code, so it doesn't trigger this
  project's mandatory e2e-before-merge gate — but it's a core, guest-facing
  flow every party goes through at the door, so a manual click-through
  (fresh name, colliding name, "I'm new" on a collision, within-session
  storage-clear simulation) is still worth doing before merge even though
  it isn't required by that gate.

## Open questions for implementation

- Icon palette content (actual emoji/color set) — visual design, not decided
  here.
- Exact copy/animation for the "Welcome back" recognition moment — the
  conceptual doc asks for this to feel like a nice moment, not a checkpoint;
  that's a UI-polish pass on top of the mechanism above, not blocking it.
- Where operator one-off scripts conventionally live in this codebase (see
  `merge_users` note above) — pick the existing convention rather than
  inventing a new one.
