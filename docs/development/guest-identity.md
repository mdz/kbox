# Identity for kbox

## What identity is for in kbox

Every guest is represented by a lightweight identity: a self-chosen display
name, with no login, password, or server-side account. That identity is what
lets kbox attribute a queued song to a person, apply fairness rules ("one
song queued per person"), recognize duets as one turn, remember playback
history, and hold a per-guest list of
[favorites](https://github.com/mdz/kbox/pull/102). None of that requires
knowing *who someone really is* — it requires being able to say "this request
and that request came from the same person" reliably enough to run a party.

## Requirements

The requirements that shape kbox's identity needs are unusual enough that the
standard, off-the-shelf approaches to "add identity to an app" don't actually
fit:

- **Zero registration friction.** A guest should be able to type a first name
  and immediately start queuing songs. Nothing that resembles signing up.
- **No PII required.** No guest should ever be required to give an email,
  phone number, or real identity just to use kbox.
- **Must work standalone today; a hosted component is foreseen, not ruled
  out.** kbox currently runs self-hosted, often on a Raspberry Pi with no
  guaranteed internet access beyond the local network, so identity can't
  *require* reaching an external service to function tonight. But a hosted
  kbox component is a real future direction (see
  [relay-proposal.md](relay-proposal.md)),
  so this isn't a permanent architectural constraint the way "no forced
  registration" is — it's a current-deployment reality worth designing
  around, not a hard requirement to design *against* a future hosted option.
- **Continuity across sessions matters, but proof of identity does not.** The
  goal is "let AJ pick up where AJ left off," not "verify this is
  cryptographically the same AJ." Stakes are low — the worst case of getting
  it wrong is someone sees a stranger's queue history at a party they're
  physically standing in, not a security incident. Self-attestation is an
  acceptable and sufficient signal in this trusted, physically co-located
  environment — in fact, outright impersonation (someone claiming to be a
  regular they aren't) isn't worth defending against at the stakes involved
  here.

That combination — frictionless, no PII, must work without depending on
external infrastructure today, needs continuity, but explicitly does not need
proof — is why the standard tools don't transplant cleanly:

- **OAuth / social login** (Google, Apple, Facebook) requires the guest to
  have an account with a third party and consent to linking it, which is a
  disproportionate trust ask for a home karaoke queue and produces exactly
  the "prove who you are" identity that isn't needed here — a mismatch that
  holds regardless of whether kbox itself is hosted or self-hosted.
- **Username + password** asks a guest to invent and remember a credential
  for a system they'll touch for one evening every couple of months — the
  definition of the friction this app exists to avoid — and creates a
  forgotten-password support burden wildly disproportionate to what's at
  stake (a song queue).
- **Email magic links** require collecting an email address and assume the
  guest can open their inbox in real time mid-party, plus create a retained
  record kbox has no reason to keep.
- **Phone/SMS OTP** requires SMS infrastructure and cost disproportionate to
  a self-hosted hobby-scale project, collects PII, adds a wait-for-text step,
  and doesn't work for non-cellular devices (tablets, laptops) that may also
  join.
- **Bare room-code / session-join models** (common in party games) solve
  frictionless joining but typically don't need to solve continuity at all —
  most are single-session by design, so they've sidestepped the actual hard
  part of kbox's problem rather than solved it.

The common thread: every standard approach is built around proving identity,
because most apps have something worth protecting (money, spam, account
takeover) that justifies the friction. kbox has deliberately traded that
protection away because there's nothing here worth protecting that way — which
is exactly what makes this an unusual identity design problem rather than a
solved one.

## Why the current localStorage/UUID design keeps failing

*(Background/history — the candidate direction below sidesteps this whole
class of problem rather than solving it; kept here because it explains why a
simpler approach is worth taking seriously.)*

kbox today identifies guests by a UUID generated client-side and stored in
`localStorage`. Identity is therefore only as durable as one browser's local
storage — a new device, a cleared browser, a reinstall, or storage simply
being evicted by the OS/browser all silently produce a brand-new,
disconnected identity with none of the guest's history.

An audit of the user table shows this isn't an edge case: every guest name
that has appeared across more than one party has a distinct identity for
each appearance, with no exceptions. The likely specific mechanism: Safari
(WebKit) caps script-writable storage (`localStorage`, `IndexedDB`) to **7
days of actual Safari usage** without a first-party visit to the site — a
deliberate anti-tracking feature, not a disk-space measure, but it produces
exactly this symptom, and guests returning months apart are far past that
window. This was checked against the code directly: kbox's own registration
logic (`/api/users` in `kbox/web/server.py`) already falls back correctly to
the client's stored UUID whenever the server-side session has expired, so
the churn isn't a bug in that fallback path — it only fails when
`localStorage` itself is gone. Consistent with this: the operator's own
device is Android/Chrome, which doesn't apply this kind of fixed-schedule
storage cap, and their identity has persisted across parties without issue
(suggestive, not conclusive, since the operator also uses kbox far more
often than any guest).

There is also a known, sharper case of storage loss happening *within a
single event* — a guest's identity churning more than once in one night,
observed so far in two guests (Vlad and Lessa), cause unknown. Name-keyed
identity doesn't make this vanish — the client still caches the resolved
identity locally for convenience, and whatever is clearing that cache mid-night
would presumably still clear it. What changes is the failure mode: instead of
silently losing history the way it does today, the guest just gets
re-prompted with the recognition tap covered below — an extra tap, not a
loss. That's a strictly better failure mode, worth having regardless of
whether the underlying cause ever gets root-caused, which is why it's no
longer treated as a blocker here — but it's still worth root-causing
opportunistically, and it's the mechanism that determines how often Vlad and
Lessa specifically will see that extra tap.

This has been a low-stakes cosmetic loss so far. It stops being low-stakes
once favorites ships — losing a deliberately-curated list of songs someone's
been planning to sing is a much worse experience than losing passive
history, and raises the bar on how well this needs to work.

## Candidate direction: name-keyed identity

The UUID stays exactly what it is today — the actual primary key, what
`favorites`, `queue_items`, `playback_history`, and everything else still
foreign-keys against. What changes is how a guest *finds* their UUID: instead
of trusting whatever UUID the client happens to have in `localStorage`, the
server resolves a typed name to the right UUID via a lookup index, and hands
that UUID back to the client for the rest of the session. Nothing downstream
of "we now know which UUID this is" changes. This is worth being explicit
about mainly so nobody reads "keyed on the name" as "the name becomes the
primary key" and goes rewriting schema that doesn't need to move — the name
is a label and a lookup path, not the identity itself.

That lookup needs to match on a normalized form of the name (trimmed,
whitespace-collapsed, case-folded) rather than an exact string — otherwise
"Matt," "matt," and "Matt " become three different lookups and quietly
recreate the exact duplicate-identity problem this document exists to fix,
just with a different root cause than `localStorage` eviction. The name a
guest actually typed is still what's displayed; normalization only affects
how it's matched.

A guest types "Vlad," and "Vlad" resolves to the same underlying identity —
on any device, any browser, any night, indefinitely. There is no client
storage the server depends on for continuity (the client still caches the
resolved name/UUID locally as a convenience, so a guest isn't re-prompted on
every page load within one sitting — see below on what that means for the
within-night churn case). No ITP eviction, no session-cookie expiry, no
new-device problem changes the *server's* ability to resolve who someone is.
This doesn't solve the storage-durability problem from the previous section,
it makes it irrelevant to identity resolution specifically.

This is a significant simplification over every direction considered
earlier in this document (device-token continuity, phone-number linking,
webview-wrapper apps) — all of those existed to work around client storage
being unreliable. If storage isn't load-bearing for identity at all, none of
that machinery is needed.

**The one real cost: name collisions.** Two different real people can share
a first name — increasingly likely as a group grows (birthday paradox), and
already lightly anticipated in kbox's product conventions ("Mike" vs "Mike
B."). The design goal for handling this: **pure disambiguation, zero
authentication.** Never ask a guest to prove or recall anything — only to
*recognize* themselves among a short list, with recognition as easy and
low-stakes as picking your face out of a lineup:

- A name with no existing record at all: nothing changes from today — type
  it, go. Worth naming honestly: this is the common case on day one and an
  increasingly rare one after that, since most first names that come up at
  all will have a record within a few parties. The single-tap "one or more
  existing records" path below is realistically the default experience this
  design should be optimized for, not the exception — which is fine, since
  it's already framed as a welcome-back moment rather than friction to
  minimize away.
- A name with **one or more** existing records: show the candidate(s), each
  labeled with whatever helps at-a-glance recognition — a self-chosen
  icon/color picked the first time that name needed disambiguating, plus a
  snippet of context like last song sung or last visit. This applies even
  when there's only a single existing record — one prior "Matt" doesn't mean
  *this* Matt is the same person, so it's still a real ambiguity, just a
  2-way one ("is this you, or are you new?") rather than an N-way one. The
  guest either recognizes themselves, or says "none of these, I'm new" —
  just as valid an answer as recognizing one. Nothing is being tested;
  there's no wrong answer, no memory pressure, no penalty for guessing "new"
  when unsure. Worst case, exactly as bad as today: a fresh, disconnected
  identity.
- For the common single-match case, this is a single, low-stakes tap with no
  real decision behind it for a genuinely returning guest — and it's a nice
  moment to lean into rather than just tolerate: "Welcome back, Matt!" is a
  much better first impression than silent, invisible re-recognition would
  have been anyway.
- Once a name has two or more people under it, **every** guest with that
  name faces the same short recognition list on future visits, not just the
  newest one — a natural, permanent consequence of resolving collisions this
  way, not a bug. With icon/context labels, this should stay a quick glance
  rather than real friction.

This deliberately excludes anything that resembles a quiz or security
question (e.g. "which of these songs have you sung?") — that pattern
implicitly asks a guest to *prove* they remember something, which reintroduces
exactly the recall-under-pressure problem this design is trying to avoid.
Context shown in the list is there to jog recognition, not to be interrogated
about.

Worth being explicit, not accidental, about the endpoint this reaches: there
is no barrier at all to claiming to be a specific returning guest — typing
"Matt," or picking "Matt" from a 2-way list, is sufficient, full stop. This
is a further step down the same "no proof needed" road the rest of this
document already commits to, just taken to its logical conclusion rather
than stopping partway with a credential like a phone number.

Two things worth being precise about, since it's easy to understate them:

- **This isn't really "impersonation risk," and the cost isn't just read
  access.** The realistic failure mode is a returning guest tapping the
  wrong candidate by mistake, not an adversary — this app is for a friendly,
  physically co-located group, not the general public, and that framing
  should be leaned into rather than hedged around. But the identity a guest
  lands on gates *writes*, not just viewing — deleting a favorite, consuming
  someone else's turn under fairness rules — so a mis-tap can clobber another
  guest's curated favorites list, not just expose it. That argues for making
  recognition labels genuinely distinctive (the icon/color idea), not for
  adding a credential. The current codebase has code and comments describing
  session-bound identity as anti-impersonation protection (e.g. the "This
  prevents user impersonation" comment on `/api/users`'s registration logic,
  and the 403 `/api/history` returns on a mismatched session). This document
  deliberately retires that framing — those should be updated to match, so
  they don't get "fixed" as a regression later by someone who doesn't have
  this context.
- **Context shown in a recognition list (last song, last visit) isn't a
  privacy leak.** Everything in kbox is already visible to every guest at
  the party — there's no privacy boundary between guests to begin with, so
  showing this context to help someone recognize themselves isn't new
  exposure, it's the same openness the rest of the app already has.

**Ghost identities from mis-taps are accepted, not mitigated.** Guessing
"none of these, I'm new" when unsure permanently adds a candidate for that
name — every future guest with that name sees one more entry in the list,
forever, since nothing here prunes or merges automatically. That's a real,
understood consequence, not an oversight — a handful of unused rows sitting
in SQLite costs nothing, and an occasional slightly-longer recognition list
is a minor, bounded cost against never getting a wrong-merge and never
needing an ongoing operator-cleanup mechanism. No auto-pruning, no ongoing
merge tooling — the one-time migration below is the only cleanup this
document calls for.

**Renaming isn't supported.** "Mike" wanting to become "Mike B." is an
identity operation this design doesn't define (it could collide with an
existing "Mike B.", among other questions) — out of scope here, alongside
the other general-public/multi-tenant concerns already excluded in
Non-goals.

## Migrating existing identities

The current user table already has guests with multiple disconnected
identities from past parties (see previous section). Moving to a name-keyed
model doesn't retroactively fix this — it only prevents *future* churn.
Getting existing regulars' history connected requires a one-time,
manually-driven pass: coalescing each known guest's duplicate identities into
a single record under their name (and, for any name that turns out to
already collide, assigning the distinguishing icon/context at that point,
same as the ongoing mechanism would). This is a data migration/bootstrapping
task, separate from the ongoing mechanism, and doesn't need to be solved in
this document — noted so it isn't lost.

## Prior art: how comparable systems handle this

A survey of systems with a similar combination of requirements — frictionless,
no account, low-stakes, needs some continuity without needing proof — turned
up a consistent pattern: **almost nothing actually solves this well.** Most
comparable systems sidestep the problem rather than solve it.

- **Party/social games** (Jackbox, Kahoot, Codenames Online, Gartic Phone,
  Spyfall-style apps) are mostly single-session by design and simply don't
  need continuity. The one exception, Jackbox, ties history to a **cookie on
  the controller device**, not to the name a player types — continuity is
  device-bound and silent, not identity-bound. Kahoot has an opt-in "player
  identifier" for tracking across a series, but it's host-configured, not
  guest self-service, and Kahoot avoids persistent participant data by
  default specifically for privacy-regulation reasons.
- **Local co-presence systems** (Nintendo Switch local play, Xbox guest
  accounts) don't solve continuity for guests at all — a guest profile
  explicitly cannot save progress; the platforms' own answer is "make a real
  account if you want persistence." Guest identity is treated as intentionally
  disposable, not something to recover.
- **Low-stakes ordering/queueing systems** are the closest real analogue, and
  the ones that do solve continuity do it with a durable real-world
  identifier rather than name alone: restaurant waitlist apps (e.g.
  TablesReady) use **phone number** as the durable key, not name — precisely
  because name collisions are common enough that name alone isn't a reliable
  key for them. kbox is making a different bet: accepting name-collision
  friction (handled via recognition, not a credential) rather than adding
  PII collection to avoid it. QR-kiosk waitlists with no phone number
  collected don't attempt continuity at all. Captive Wi-Fi portals use
  device/MAC-address or cookie binding to skip re-login on return — the same
  device-token idea as Jackbox — though this is explicitly degrading now
  that OSes randomize MAC addresses by default.
- **Karaoke-specific software** (kJams, PCDJ Karaoki, KJ Deluxe, Siglos) is
  the most directly comparable domain. kJams in particular has real
  self-service continuity via its companion mobile app, kJams Cue: a
  returning singer picks their name from a list **and enters a password** to
  reconnect. That's a meaningfully different (and heavier) bar than what this
  document lands on — kbox's pure-recognition, zero-authentication approach
  goes further than even the closest comparable industry does in trading
  away proof of identity, which is worth being aware of as a deliberate,
  unusual choice rather than assuming it's already how this kind of system
  normally works.

No named UX pattern or term of art for this specific niche turned up
("progressive identity" exists but describes e-commerce guest-checkout
account-strength ladders that converge on a durable account, which isn't the
shape of kbox's problem).

## Non-goals

- **Not designing for a hosted/cloud backend.** A future cloud-hosted kbox
  service (see [relay-proposal.md](relay-proposal.md)) may eventually make identity
  continuity structurally easier — e.g. a real account that follows a guest
  across venues and devices. That's a separate future effort; this problem is
  scoped to the current local-only, single-Pi-per-party model. Solutions here
  should avoid actively foreclosing that future, but shouldn't be designed
  around it arriving.
- **Not adding any credential or verification step.** No password, PIN,
  phone number, or proof-of-ownership of anything. The collision-handling
  design above is explicitly a recognition aid, not an authentication
  mechanism, and that distinction should hold even as the design gets more
  detailed.
- **Not designing for a fully public/untrusted context in this pass.** The
  purely-trusted, zero-authentication model assumes a private, physically
  co-located party. A bar or public KJ setting (a real secondary use case for
  kbox) might need something more, but that's not being solved here.

## Design principles

1. No guest-facing step that resembles login, registration, or credential
   management, ever — not even an optional one.
2. Continuity should cost as little as possible for the common case — free
   for a genuinely new name, a single low-stakes recognition tap (framed as
   a welcome-back moment, not a checkpoint) for a returning or ambiguous one.
3. Any existing record for a typed name is a real ambiguity to resolve, even
   just one — never assume "only one match" means "must be the same
   person." Resolve ambiguity through recognition from a short list, never
   through recall, proof, or anything that could be gotten "wrong."
4. Operator involvement is for one-time data migration only, not an ongoing
   mechanism guests or the host are expected to rely on regularly.

## Status

Requirements, problem framing, prior-art research, and a candidate direction
(name-keyed identity with pure-recognition collision handling) above are
settled as the current thinking, including a critical review pass (UUID
remains the real key with a normalized name lookup on top; ghost identities
from mis-taps are accepted, not mitigated; the write-access/favorites risk
and the existing anti-impersonation code comments are called out explicitly;
Vlad/Lessa's within-night churn becomes a re-prompt rather than silent loss;
renaming is unsupported). Not yet addressed: the concrete UI for the
collision-recognition list (icon picker, context shown), and the one-time
migration plan for existing duplicate identities.
