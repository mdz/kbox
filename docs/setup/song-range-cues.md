# Song Range Cues (optional)

When a guest adds a song, kbox can show a short hint under the pitch control
about how the song's vocal line sits, for example:

- "Most voices can sing this in the original key"
- "Most voices will find it easier 2 steps up", with a **Try +2** button
- "Wide range: has both high and low parts"

Nothing changes unless the guest taps the button. When kbox doesn't know a
song, it shows nothing.

## What it needs

- **AI settings configured.** kbox uses the AI model to work out the artist
  and title from the karaoke video's name.
- **A free USDB account.** Song ranges come from
  [USDB](https://usdb.animux.de/), a community database of UltraStar
  karaoke-game note files. It covers roughly two thirds of commonly sung
  songs. USDB has no API, and its search and note files are only available
  to logged-in members.

## Setup

1. Register at [usdb.animux.de](https://usdb.animux.de/) (username, email,
   and password; no approval step).
2. In kbox, open **Settings → API & Storage** and enter the **USDB Username**
   and **USDB Password**.

That's all. The first time anyone opens a song, kbox looks it up, which takes a
few seconds. A guest who adds the song before then just doesn't see the hint.
After that the answer is cached, for every karaoke version of the song.

## Being a good neighbour

USDB is run by volunteers. kbox sends one request at a time, at least a
second apart, and caches every answer, including "not found" (rechecked
after 30 days). After a failure it leaves USDB alone for five minutes.

## Limitations

- **Hints describe the original recording.** Some karaoke tracks are in a
  different key, and for those the hint is off by the difference.
- **Hints ignore octaves.** USDB note files are often an octave off, so kbox
  only uses where the song sits within an octave and how wide it is.
  "Original key" assumes each singer picks the octave that suits them, as
  people do when singing a song by someone with a much higher or lower voice.
- **Hints are for a typical voice.** kbox doesn't know your voice, so the
  hint is a starting point. You can still change pitch during the song.
