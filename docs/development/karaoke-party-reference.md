# Karaoke Party Reference

How karaoke parties work in practice, and why kbox is shaped the way it is.
Read this when a design question is really a question about what happens at a
party; it explains the *why* behind the product decisions in
[CLAUDE.md](../../CLAUDE.md) ("Product Context").

**Philosophy:** a karaoke system should facilitate connection, entertainment
and shared experience. The technology gets out of the way and lets people have
fun.

**Focus:** home karaoke, with awareness that kbox may be used in bar/KJ
settings. Formats and house rules vary, so kbox avoids being prescriptive.

---

## The flow of a party

### Before the singing

Guests trickle in; there is mingling and catching up. There may or may not be
an official start to the singing. It helps if guests can browse and queue songs
during this phase. A **theme** may be announced to inspire choices (set ahead
of time or on the spot).

### Singing

Once the host kicks things off, singing runs continuously:

- When one performer finishes, the next song starts automatically.
- The next singer is called (name on screen, subtly).
- Guests add songs whenever inspiration strikes, from their own devices.
- Most people watch the current performance; some browse for songs.

### Winding down

The ending is informal. The queue may empty and people stop adding songs; the
host may call a "last song"; the party may lose critical mass as guests leave;
sometimes songs remain queued but the host is tired. The hosts may sing one
last song to say goodnight.

---

## Roles

**Performer.** Anyone taking a turn at the mic. Some sing many songs, some one,
some none. All are fine; nobody should be pressured.

**Audience.** Watching, and often browsing for their next song, learning each
other's names from the display, and discovering music through what others
perform.

**Host / operator.** Keeps the party flowing: shows guests how to use the
system, monitors the flow, fixes technical problems quickly, accommodates
special requests, skips long outros, handles no-shows.

In home karaoke the host often wants to sing too and shouldn't be stuck on IT
duty all night, so the system has to be self-service enough that guests manage
their own song selections. In casual settings there may be no explicit
operator at all. In a bar, a dedicated KJ manages the queue and usually
doesn't sing.

---

## Queue and flow

### Adding songs

Guests search and add songs from their phones at any time. Searches are biased
toward karaoke tracks. kbox can also suggest songs based on history, the queue
and the party theme.

### Automatic flow

When a song ends the next starts. Dead air kills the energy. As a song wraps
up, the next singer's name appears on screen without distracting from the
current performance, giving them time to get ready (a few seconds in an
intimate room, longer in a bigger venue).

### Fairness

A common house rule is that you can't add a song while you already have one
queued, so nobody monopolizes the mic. Other hosts run first-come,
first-served. kbox doesn't enforce either: it shows a non-blocking soft nudge
when a guest adds a second unplayed song (there are legitimate reasons for it:
adding for someone else, a duet, fixing a mistake). Operators running strict
FCFS can turn the nudge off (`duplicate_singer_nudge_enabled`).

### No-shows

If someone is called up and isn't ready:

1. Ideally prevent it by letting people know their turn is approaching.
2. Otherwise move them down (not necessarily to the end) so the next person
   can go.

The goal is to keep the flow going, not to punish anyone. kbox has no
dedicated "bump down" feature; the general-purpose Play Next and reorder
controls cover it.

### Early departures

Someone who leaves with a song still queued has that song removed. The system
can't know they left, so the operator handles it. If it's their turn and
they're gone, skip.

### Empty queue

The party may wind down naturally or carry on if someone adds more songs,
depending on energy and the hour.

---

## Etiquette

- **Take turns.** Don't monopolize the mic.
- **Singing twice in a row is rude**, even if nobody else is queued.
- **Be ready for your turn.** Watch your queue position.

### Duets

Duets are common. Socially, a duet is one person's turn and the partner joins
without spending theirs. kbox does not model this yet: today a duet is just
another song in the queue (see [issue #130](https://github.com/mdz/kbox/issues/130)).

### Special requests

Usually granted: "Can I go next? I have to leave soon." Generally not:
"Can I sing two in a row?"

### Formality

Small, intimate crowds are informal and rules bend. Larger crowds or strangers
make order matter more. The host sets the tone.

---

## The display

**During a performance** the performer and audience typically see the same
content (mirrored to two screens): lyrics visible to both, which helps the
audience follow along and lets people discover shared tastes. Showing the
performer lyrics and the audience something else is a possible future setup.

**Up next.** As a song wraps up, show who's next, subtly.

**Between songs** show the next performer's name prominently. The song title is
optional; sometimes the surprise is part of the fun.

**Queue visibility on phones.** Position in the queue, estimated wait, and
download status of your song. This reduces anxiety and helps people be ready.

---

## Identity: names without friction

Karaoke is social, and friction kills the vibe. Guests identify by **first name
or nickname only**: no logins, passwords, emails or accounts. That makes it
easy to add a song in seconds, and showing names on screen helps guests learn
each other's. When two people use the same name, disambiguate lightly ("Mike"
vs "Mike B.") without formal registration. See
[guest-identity.md](guest-identity.md) for the full treatment.

---

## When things go wrong

Karaoke is live performance, and things will go wrong.

| Problem | Operator response |
|---|---|
| Wrong song added (found at performance time) | Skip and help them find the right one |
| Bad track quality or unexpected format | Skip, or find another version |
| Song out of the singer's range | Adjust pitch; skip if that doesn't help |
| Long outro drags on | Skip to the next performer |
| Performer isn't ready when called | Move them down, advance |
| Accidental playback disruption | Recover quickly; controls are locked by default to prevent this |
| Technical failure mid-song | Attempt recovery; resume from saved position |
| Accident or emergency | Pause immediately; resume when resolved |

### Why reliability is paramount

A failure mid-performance means performer anxiety and embarrassment, broken
flow, and a broken shared experience. The system must minimize failures,
recover gracefully when they happen, and give the operator tools to fix any
situation: skip, pause/resume, seek (when recovery lands in the wrong spot),
reorder, remove songs, and adjust pitch on the fly. Without "a million
buttons"; controls stay accessible but unobtrusive.

---

## What makes a great party

Most people are excited to sing, songs light up the crowd, one performer flows
into the next, there are **no technical disruptions**, and everyone is having
fun whether performing or watching.

| Goal | System support |
|---|---|
| People find great songs | Search, suggestions |
| Smooth flow | Auto-advance, up-next display, wait-time visibility |
| No disruptions | Reliability, graceful recovery, locked operator controls |
| Low-friction participation | Name-only identity, mobile-first, self-service |
| Shared experience | Names on screen, visible queue, lyrics for the audience |

kbox facilitates but can't guarantee a great party. Good song choices, a
supportive audience, a host who sets the tone, and enough enthusiastic people
matter most.

---

## Design implications

| User need | Feature |
|---|---|
| Identity without friction | Name-only identification, no auth |
| Prevent accidental disruption | Operator PIN; controls locked by default |
| Keep the party flowing | Auto-advance, skip, queue reordering |
| Vocal range issues | Per-song pitch adjustment |
| Know when your turn is coming | Wait-time estimate, up-next display |
| Reduce anxiety about songs | Download status visibility |
| Recover from errors | Retry logic, position recovery, operator controls |
| Operator not stuck at the controls | Mobile-friendly UI, guest self-service |
| Varied house rules | Soft nudge instead of a hard one-song rule |
| Special requests | Play Next / reorder, operator discretion |

---

## Physical setup

A typical home setup has one or two screens (one behind the performer, one
facing them), a wireless or wired mic, and the performer standing at the front
of the room or seated and casual. Mirrored screens let the audience follow the
lyrics while the performer sees them facing the crowd; a single performer-only
screen also works. See [hardware-setup.md](../hardware/hardware-setup.md).

## Bar / KJ environments

| Aspect | Home | Bar |
|---|---|---|
| Who manages the queue | Shared, or a designated host | Dedicated KJ |
| How songs are added | Self-service on phones | Paper slips to the KJ, or KJ-managed |
| Does the operator sing? | Often | Usually not |
| Anonymity | Everyone knows or learns each other | Possibly strangers |
| Formality | Flexible | More structured |
| Tips | n/a | Common |
| Song refusal | Rare | May refuse offensive or very long songs |

kbox should stay flexible enough to support both without being prescriptive
about either.
