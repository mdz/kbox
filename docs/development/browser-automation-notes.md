# Driving kbox via Browser Automation

Notes for using a browser-automation tool (Claude's Browser pane, Playwright, etc.)
against a live kbox instance — e.g. for burn-in testing or exercising the queue
end-to-end. These are quirks of automating *this app* specifically, not general advice.

## Native `confirm()` dialogs are silently declined

Guest flows no longer use native dialogs: add-song warnings (already queued,
second song, long song) appear inline in the add-song modal with an "Add anyway"
button, and removing a song is a two-step button ("Remove from Queue" → "Tap again
to remove"). Pitch changes in the edit-song dialog apply immediately.

The only remaining native `window.confirm()` is the operator-only **Clear entire
queue**. Automated browsers typically can't display native dialogs and auto-decline
them (`confirm()` returns `false`), so that action silently no-ops — no error, no
network request. If it seems to do nothing, check the console for
`Page dialog suppressed (confirm): "..."`.

**Fix:** reapply `window.confirm = () => true;` immediately before clicking it (the
override did not reliably persist across actions in testing).

## Enter/Return does not submit the search form

Typing a query into the search box and pressing Return does not trigger a search. Click
the **Search** button explicitly.

## Search queries auto-append "karaoke"

The search backend appends "karaoke" to your query automatically. Including it yourself
is harmless — the query is normalized so exactly one "karaoke" survives, wherever you
put it — but it's redundant, so prefer `Bohemian Rhapsody`.

## An autocomplete dropdown opens while you type

Typing three or more characters into the search box opens a suggestion list
(`#search-suggestions`) anchored under the input, which overlays whatever is beneath it.
Two consequences for automation:

- A click aimed at something just below the search box may hit a suggestion instead.
  Press Escape, or click the **Search** button (which closes the dropdown), before
  interacting with elements further down the page.
- Setting the input's value directly via JavaScript does **not** open the dropdown, since
  no `input` event fires. That's usually what you want — use it to avoid the overlay
  entirely.

The feature degrades to nothing when the suggestion endpoint is unreachable, so on a
sandboxed network you may never see a dropdown at all.

## Element refs go stale fast — the queue polls aggressively

The queue and playback-status views poll roughly once a second, re-rendering the DOM.
A `ref_N` from `read_page`/`find` can go stale within about a second, especially for
elements inside the queue list. Symptoms: `ref is stale (element removed)` errors on
actions that worked fine moments earlier.

**Mitigations:**
- Call `find` immediately before the click that uses its result — don't hold onto refs
  across multiple steps
- For elements inside the queue list specifically, consider a fresh screenshot and a
  raw pixel-coordinate click instead of a ref, since the list re-renders most often
- If a ref goes stale, just re-find it — this is normal and expected, not a bug

## Identity vs. operator authentication vs. "Unlock Controls" are three separate gates

- **Guest identity**: entering a name and clicking Continue sets a per-session cookie.
  This is all that's needed to search and queue songs as that name.
- **Operator authentication** (🔑 icon, PIN entry): required to manage *other users'*
  queue entries (Edit Song modal → Remove from Queue, Replace Song, Queue Management:
  Jump to Song / Play Next / Move Up / Move Down / Move to End) and to see "Clear Queue".
- **Unlock Controls**: a *separate* toggle, available after operator auth, specifically
  gating the transport controls (Play/Pause/Stop/Skip/Previous/seek). Being
  operator-authenticated does not automatically unlock these — click "Unlock Controls"
  separately.

## Verifying state server-side, independent of the UI

The UI can lag or misrepresent state (see the confirm-dialog trap above — the action can
sit there indefinitely while nothing has actually happened). To check ground truth
directly against the API, the app gates endpoints behind a session cookie set via the
`?key=` query param on first request:
```bash
curl -s -c cookies.txt "http://localhost:8000/api/queue?key=YOUR_KEY" -o /dev/null
curl -s -b cookies.txt http://localhost:8000/api/queue
curl -s -b cookies.txt http://localhost:8000/api/playback/status
```
Useful for confirming a queue add/remove actually landed, independent of whatever the
browser DOM currently shows.

## Running an isolated instance (worktrees, scratch data)

Never point automation at your real `~/.kbox`. Start the checked-in `kbox`
entry from `.claude/launch.json` (`preview_start {name: "kbox"}`). It is already isolated and runs:

```bash
uv run python -m kbox.main --data-dir .kbox-scratch --access-token uxtest
```

- `--data-dir` (or `KBOX_DATA_DIR`) relocates the database, video library and QR
  code. The relative path resolves inside the current checkout/worktree, so each
  worktree gets its own throwaway instance (`.kbox-scratch/` is gitignored). Delete
  the directory to reset.
- `--access-token` (or `KBOX_ACCESS_TOKEN`) pins the guest token, so the guest page
  is `http://localhost:8000/?key=uxtest` with no sqlite edits. Starting a new party
  session still rotates the token, as in normal use. The operator PIN is the default
  (`1234`) on a fresh scratch DB.
- Both are for development; a stock install ignores them unless set.

**Static assets are versioned at server start.** URLs look like
`/static/<hash>/js/...` and the hash is generated each launch, with immutable
caching. After editing JS/CSS, restart the server (`preview_stop` + `preview_start`)
to see changes.

**Playback with fake content:** fake video ids end up in `content_status` error and
`/api/playback/play` fails, so next-up / now-playing states can't be reached with
made-up songs. Use real, downloadable videos, or stub the status API in the page.
There is no fake-content dev mode yet.
