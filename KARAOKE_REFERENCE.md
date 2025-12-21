# Karaoke Party Reference

This document captures how karaoke parties work in practice, based on real-world hosting experience. It serves as a reference for developers when implementing features, explaining the *why* behind design decisions.

For technical implementation details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Overview

**Philosophy**: A karaoke system should facilitate connection, entertainment, and shared experience. The technology should get out of the way and let people have fun.

**Current focus**: Home karaoke environments, with awareness that the system may be used in bar/KJ environments in the future. We avoid being overly prescriptive, since formats and house rules vary.

---

## The Flow of a Karaoke Party

### Pre-Singing Phase

Guests trickle in over time. There's mingling, catching up, and socializing before the singing begins. During this phase:

- There may or may not be an official "start" to the singing portion
- It's helpful if guests can browse and add songs to the queue during this time
- A **theme** may be announced to inspire song selections (chosen ahead of time or decided last minute)

### Singing Phase

Once the host decides to kick things off, the singing typically proceeds continuously:

- When one performer finishes, the next song starts automatically
- The system should "call the next singer" (display their name, announce them subtly)
- Guests add songs to the queue whenever they feel inspired, using their own devices
- Most guests will be watching the current performance, but some may be browsing for songs

### Winding Down

The ending is informal:

- The queue may empty naturally and people stop adding songs
- The host may announce a "last song" to wrap up the evening
- Sometimes the party loses "critical mass" as guests leave, and energy dwindles
- Sometimes songs remain in the queue but the host is tired and calls it a night
- The host(s) may perform one last song to say goodnight

---

## Roles

### Performer / Singer

Anyone taking a turn at the mic. Some people sing many songs throughout the night; others sing only one or choose not to sing at all. Both are perfectly fine—no one should be pressured.

### Audience

Guests who are watching and enjoying the performances. They may also be:

- Browsing for their next song on their mobile device
- Learning each other's names from the display
- Discovering new music through what others perform

### Host / Operator

The person responsible for keeping the party flowing. Responsibilities include:

- Showing guests how to use the system
- Monitoring the flow and taking action when needed
- Handling technical problems quickly
- Accommodating special requests
- Skipping long outros or handling no-shows

**Key insight**: In home karaoke, the host often wants to sing too. They shouldn't be stuck on "IT duty" all night. The system should be self-service enough that guests can manage their own song selections.

**Note**: In casual home settings, there may not be an explicit operator—the role may be shared or implicit. In bar environments, a dedicated KJ (Karaoke Jockey) manages the queue and typically does not sing.

---

## Queue Management and Flow

### How Songs Get Added

- Guests add songs via their own mobile devices at any time
- They typically search for a specific song, with "karaoke" automatically appended to searches
- Future: Suggest songs based on theme, singing history, or what others are performing

### Automatic Flow

When a song ends, the next one starts automatically. This continuous flow is essential—dead air kills the energy.

The system should announce the next singer as the current song wraps up:
- Display their name on-screen (subtly, to not distract from the current performance)
- Possibly use text-to-speech or a popup notification
- Give performers time to get ready (5 seconds in intimate settings, up to a few minutes in larger venues)

### Fairness Rules (Configurable)

A common house rule: **You cannot add a new song to the queue while you already have one queued.** This ensures everyone gets a fair turn and no one monopolizes the mic.

However, this is the host's preference—others may run first-come-first-served. The system should support both approaches.

### Handling No-Shows

If someone is called up but isn't ready (stepped away, etc.):

1. **Ideally, prevent this** by alerting people when their turn is approaching
2. If it happens, **bump them down the queue** (not necessarily to the end) so the next person can go
3. The goal is to keep the flow going, not punish anyone

### Early Departures

If someone has to leave but still has a song in the queue:
- Their song should be removed
- The system won't know they've left, so the operator handles this
- If it's their turn and they're unavailable, skip immediately to the next song

### Empty Queue

When the queue runs out, the party may:
- Naturally wind down (it's late, people are tired)
- Continue if someone adds more songs
- Depends on energy level and time of night

---

## Etiquette and Unwritten Rules

### General Etiquette

- **Take turns**: Don't monopolize the mic
- **Singing twice in a row is rude**: Even if no one else is queued, it's unusual to request back-to-back songs
- **Be ready for your turn**: Pay attention to queue position; don't make everyone wait

### Duets

Duets are very common and don't require special accommodation:
- Treated as one person's "turn"
- The other person joins without "using their turn"
- The system may just show one name, or both if easily captured

### Special Requests

Common requests that hosts typically accommodate:
- "Can I go next? I have to leave soon" — usually granted
- "Can we sing this together?" — duet, handled as above

Requests that are generally not acceptable:
- "Can I sing two songs in a row?" — considered rude

### Flexibility vs. Structure

- **Smaller/intimate crowds**: More informal, rules are flexible
- **Larger crowds or more strangers**: Order becomes more important
- The host sets the tone for their event

---

## The Display

### During Performance

The performer and audience typically see the same content (mirrored to two screens):
- Lyrics/karaoke video visible to both
- Helps the audience follow along and enjoy the performance
- Can facilitate discovering shared musical interests

**Alternative setups** (future consideration):
- Performer sees lyrics; audience sees a visualization or static image
- Could reduce distraction and focus attention on the performer

### Up Next Notification

As the current song wraps up, show who's next:
- Display performer name subtly (don't distract from current performance)
- Give them time to prepare and come to the front

### Transition / Interstitial Screen

Between songs, display:
- **Next performer's name prominently**
- Song title is optional—sometimes the surprise is part of the fun
- Builds anticipation without revealing exactly what's coming

### Queue Visibility

On mobile devices, users can see:
- Their position in the queue
- Estimated wait time
- Download status of their song (pending → downloading → ready)

This reduces anxiety and helps people plan when to be ready.

---

## Identity: Names Without Friction

### Philosophy

Karaoke is social. Friction kills the vibe. We want guests to join in effortlessly.

### Implementation

- Users identify themselves with **first name or nickname only**
- No logins, passwords, email addresses, or accounts
- Keep it fluid and fast

### Benefits

- **Low barrier to participation**: Anyone can add a song in seconds
- **Learning names**: Showing singer names on-screen helps guests learn each other's names
- **Social atmosphere**: Feels like a party, not a software product

### Disambiguation

If two people use the same name:
- Find out if they're the same person we already know about, or someone new
- Disambiguate without adding complexity (e.g., "Mike" vs "Mike B.")
- Avoid requiring formal registration

---

## When Things Go Wrong

Karaoke is live performance. Things will go wrong. The system must handle problems gracefully to maintain the party atmosphere.

### Common Problems

| Problem | Operator Response |
|---------|-------------------|
| Wrong song added (discovered at performance time) | Skip and help them find the right one |
| Bad track quality or unexpected format | Skip or find an alternative version |
| Song out of singer's vocal range | Adjust pitch if possible; skip if not |
| Long outro drags on | Skip to next performer |
| Performer isn't ready when called | Bump them down, advance to next |
| Accidental playback disruption | Recover quickly; operator controls are locked by default to prevent this |
| Technical failure mid-song | Attempt recovery; resume from saved position if possible |
| Accident or emergency (spill, injury) | Pause immediately; resume when resolved |

### Why Reliability is Paramount

A technical failure mid-performance causes:
- Performer anxiety and embarrassment
- Disruption to the party flow
- Breaks the spell of the shared experience

The system must:
- Minimize the chance of failures
- Recover gracefully when they occur
- Give the operator tools to fix any situation

### Operator Recovery Powers

The operator needs to be able to:
- Skip songs instantly
- Pause and resume playback
- Adjust playback position (if recovery goes to wrong spot)
- Reorder the queue
- Remove songs from the queue
- Adjust pitch on the fly

But they shouldn't need "a million buttons"—keep controls accessible but unobtrusive.

---

## What Makes a Great Karaoke Party

### The Goal

At a great party:
- Most people are excited to sing
- Songs "light up the crowd" (familiar, singable, fun)
- Smooth, uninterrupted flow from one performer to the next
- **Zero technical disruptions**
- Everyone is having fun, whether performing or watching, the entire time

### How the System Helps

| Goal | System Support |
|------|----------------|
| People find great songs | Search, suggestions, discovery features |
| Smooth flow | Auto-advance, up-next notifications, wait time visibility |
| No disruptions | Reliability, graceful error recovery, operator controls |
| Low friction participation | Name-only identity, mobile-friendly, self-service |
| Connection and shared experience | Display names, visible queue, audience can follow lyrics |

### What the System Cannot Do

The system facilitates, but cannot guarantee, a great party. The human elements matter most:
- Good song choices by performers
- Supportive, engaged audience
- Host setting the right tone
- Critical mass of enthusiastic participants

---

## Physical Setup Reference

### Typical Home Setup

- **Screens**: One or two, mirrored (one behind performer, one facing performer)
- **Microphones**: Wireless is convenient; wired works fine
- **Performer position**: Typically standing at front of room or on a small stage
- **Informal variant**: Performer stays seated and sings casually

### Why Mirrored Screens

- Audience can follow the lyrics and enjoy the performance
- Performer can see lyrics while facing the audience
- A single screen (performer-only) also works fine

---

## Design Implications Summary

This table maps user needs (from this document) to technical features (from [ARCHITECTURE.md](ARCHITECTURE.md)):

| User Need | Technical Feature |
|-----------|-------------------|
| Fluid identity without friction | Name-only identification (no auth) |
| Prevent accidental disruption | Operator PIN, controls disabled by default |
| Keep party flowing | Auto-advance, easy skip, queue reordering |
| Handle vocal range issues | Per-song pitch adjustment |
| Know when your turn is coming | Wait time estimation, up-next notification |
| Reduce anxiety about songs | Download status visibility |
| Recover from errors gracefully | Retry logic, position recovery, operator controls |
| Operator not stuck at controls | Mobile-friendly UI, self-service for guests |
| Support varied house rules | Configurable fairness rules (one-song-at-a-time, etc.) |
| Accommodate special requests | Queue reordering, operator discretion |

---

## Notes on Bar/KJ Environments

While our current focus is home karaoke, we keep bar environments in mind for future compatibility:

| Aspect | Home | Bar |
|--------|------|-----|
| Who manages queue | Shared/implicit, or designated host | Dedicated KJ |
| How songs are added | Self-service via mobile devices | Paper slips to KJ, or KJ-managed system |
| Does operator sing? | Often yes | Usually no |
| Anonymity | Everyone knows (or learns) each other | May be strangers |
| Formality | Flexible, informal | More structured |
| Tips | N/A | Common (paper slips with cash) |
| Song refusal | Rare | May refuse inappropriate/offensive/very long songs |

The system should be flexible enough to support both modes without being overly prescriptive about either.

---

## Related Documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — Technical architecture and implementation details
- [FEATURE_IDEAS.md](FEATURE_IDEAS.md) — Ideas for future features

