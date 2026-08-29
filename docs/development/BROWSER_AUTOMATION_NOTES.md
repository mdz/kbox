# Driving kbox via Browser Automation

Notes for using a browser-automation tool (Claude's Browser pane, Playwright, etc.)
against a live kbox instance — e.g. for burn-in testing or exercising the queue
end-to-end. These are quirks of automating *this app* specifically, not general advice.

## Native `confirm()` dialogs are silently declined

kbox uses native `window.confirm()` for a couple of destructive/notable actions:
- Adding a song over a certain length ("This song is X long. Are you sure...")
- Removing a song from the queue ("Remove '...' from queue?")

Automated browsers typically can't display native dialogs and auto-decline them
(`confirm()` returns `false`), which makes the action silently no-op — no error, no
network request, the UI just looks like nothing happened. This is easy to mistake for a
broken button or a failed click.

**Fix:** override `confirm` to auto-accept before triggering the action:
```js
window.confirm = () => true;
```
**Important:** this override did not reliably persist across actions in testing — reapply
it immediately before each action that might trigger a dialog, rather than once at the
start of a session. If an add/remove seems to silently fail, check the console for
`Page dialog suppressed (confirm): "..."` — that's the tell.

## Enter/Return does not submit the search form

Typing a query into the search box and pressing Return does not trigger a search. Click
the **Search** button explicitly.

## Search queries auto-append "karaoke"

The search backend appends "karaoke" to your query automatically — don't include it
yourself (e.g. search `Bohemian Rhapsody`, not `Bohemian Rhapsody karaoke`).

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

The UI can lag or misrepresent state (see the confirm-dialog trap above — the modal can
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
