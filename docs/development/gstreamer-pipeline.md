# The GStreamer Pipeline

How kbox gets audio and video onto a screen, why it is arranged this way, and
the things that look like they should work but do not.

Read this before changing anything in `kbox/streaming/`. Most of the notes
below exist because something plausible was tried, shipped, and had to be
measured on real hardware before it became obvious it was wrong.

## The shape of it

Three pipelines, not one.

```mermaid
flowchart TB
    subgraph display["Display pipeline — created once, PLAYING for the whole session"]
        ISRC[intervideosrc<br/>channel=kbox-display] --> VC2[videoconvert]
        VC2 --> VS[videoscale<br/>add-borders=true]
        VS --> CF[capsfilter<br/>fixed render size]
        CF --> QR[gdkpixbufoverlay<br/>QR code]
        QR --> TXT[textoverlay<br/>now singing / notifications]
        TXT --> SINK[kmssink]
    end

    subgraph playbin["playbin — cycles NULL/READY/PLAYING per song"]
        PB[playbin] --> ABIN[audio sink bin<br/>convert, pitch, volume, alsasink]
        PB --> VBIN[video sink bin<br/>videoconvert, intervideosink]
    end

    subgraph still["Interstitial pipeline — one per still image"]
        FS[filesrc] --> DEC[decodebin] --> IF[imagefreeze] --> ISINK[intervideosink]
    end

    VBIN -.->|frames, during a song| ISRC
    ISINK -.->|frames, between songs| ISRC
    SINK --> SCREEN[/screen/]
```

**The display pipeline owns the screen.** It goes to PLAYING once at startup
and stays there until the process exits. Nothing about a song change touches
it.

**playbin decodes songs** and renders into an `intervideosink`, which hands
buffers to the display pipeline's `intervideosrc` over a named channel. It is
free to cycle through NULL and READY as often as it likes.

**A separate pipeline feeds still images.** Interstitials (idle screen,
"up next", end of queue) are PNGs, and go through `imagefreeze` rather than
playbin.

playbin and the interstitial pipeline are **mutually exclusive** — each stops
the other before it starts feeding. If both fed the bridge at once, a song and
an interstitial would interleave on screen.

That mutual exclusion is maintained by hand today. An alternative that makes it
structural, prototyped but not yet implemented, is in "Feeding the bridge
between songs" below.

## Why kmssink, despite gstreamer-plugins-bad

kmssink ships in gstreamer-plugins-bad, which sometimes reads as a quality
warning. It isn't one — "bad" means the module hasn't accumulated enough
review, tests, or wide use to get promoted, not that it's poorly maintained.
kmssink has an active maintainer and sees regular patches, and it's the
backend other kiosk/signage software (e.g. Kodi's GBM output) relies on too.

The alternatives all give up something kbox needs:

- **waylandsink** — also lives in plugins-bad, so no maturity gain, and it
  requires a compositor process (weston/cage/labwc) running alongside kbox.
- **glimagesink** (EGL/GBM) — in plugins-base, but does its scaling on the
  GPU via GL rather than the display controller's overlay plane. See "kmssink
  already scales in hardware" below for why that plane scaling is
  load-bearing for frame rate.
- **X11 + ximagesink/xvimagesink** — in plugins-base, but needs a full X
  server running just to show one fullscreen video.

Net: every alternative either shares kmssink's plugins-bad status or trades
away the hardware plane scaling this pipeline's frame budget depends on.
Revisit only if kmssink itself becomes a concrete blocker (a specific bug,
missing feature), not because of the module it ships in.

## How it used to work, and why it changed

The first generation put everything in one playbin:

```
playbin
  ├── audio-sink: audioconvert → pitch shift → volume → alsasink
  └── video-sink: videoconvert → overlays → videoscale → kmssink
```

Interstitials were played *through playbin* by pointing its `uri` at a PNG.

This had two visible faults, filed as
[#94](https://github.com/mdz/kbox/issues/94) and
[#93](https://github.com/mdz/kbox/issues/93):

**The console flashed between songs.** playbin owns whatever is set as its
`video-sink`, so every song change dragged kmssink through PAUSED→READY.
`GstBaseSink.stop()` runs on that transition, and for kmssink it closes the DRM
file descriptor — dropping DRM master and handing the screen back to the
kernel console, `getty` prompt and all.

The fix is the split above: the sink's *lifetime* has to be independent of the
content being played. Nothing else works, because the problem is not which
state playbin drops to (see the traps below).

**The console showed in the margins of non-16:9 videos.** kmssink scales to
fit *while preserving aspect*, so a 4:3 source on a 16:9 screen lands centred
with bars either side, and the console is what is underneath. The fix is to
letterbox the source into a frame that already carries the display's aspect
ratio, so the frame kmssink scales covers the screen.

## Feeding the bridge between songs

Something must always be feeding the display bridge or the screen goes black
(see the `intervideosrc` trap below). Today that job is split between playbin
during a song and a second pipeline, built and torn down per still image,
between songs.

That works, but the cost is spread across `kbox/streaming.py`:
`_create_interstitial_pipeline` and `_stop_interstitial_pipeline` plus its six
call sites, each of which must stop one feeder before starting the other, the
`_is_interstitial` flag, and an EOS guard — all of it maintaining a single
invariant by hand, namely that exactly one thing feeds the bridge at a time.

An `input-selector` makes that invariant structural instead: a selector has one
active pad, so "two feeders at once" stops being representable. Two placements
were prototyped. They differ only in **which `GstPipeline` the selector lives
in**, and that turns out to decide the whole outcome.

### Ruled out: the selector inside playbin3's sink bin

The first prototype put the selector inside a collapsed `playbin3` pipeline —
an `appsrc`/`imagefreeze` branch alongside the pad playbin3 decodes into,
feeding `kmssink` directly with no bridge at all. Switching worked and was
fast (~20-30 ms), but **a background URI swap stalled whichever branch was
showing**: with the selector sitting on the interstitial branch, setting
playbin3's `uri` (even under `instant-uri`, even though the decode branch was
not selected) dropped frame delivery to 1-2 frames over 2+ seconds, from
~30/second immediately before. Bus tracing showed playbin3 rebuilding its
entire internal decode graph — fresh `urisourcebin`, `parsebin`, `qtdemux`,
`multiqueue`, `h264parse`, `aacparse`, 100+ bus messages in under half a
second — inside the same `GstPipeline` as the selector, sharing its clock and
state machine.

That is the worst possible moment for a freeze: "up next" is on screen
*because* the next song is loading. The conclusion was that `input-selector`
only routes buffers; it does not reproduce the insulation that comes from
being a separate `GstPipeline`.

### Works: the selector inside the display pipeline

The second prototype keeps that insulation. playbin stays in its own
`GstPipeline` feeding the bridge exactly as it does today, and only the
interstitial feeder moves — into the display pipeline, as a second selector
branch:

```mermaid
flowchart LR
    subgraph display["Display pipeline — one GstPipeline, PLAYING for the session"]
        ISRC[intervideosrc<br/>channel=kbox-display] --> VC[videoconvert]
        VC --> VS[videoscale<br/>add-borders] --> CF[capsfilter<br/>render size]
        BLACK[videotestsrc<br/>pattern=black, is-live] --> CF2[capsfilter<br/>same render size]
        CF2 --> IOV[gdkpixbufoverlay<br/>interstitial PNG, full frame]
        CF --> SEL[input-selector]
        IOV --> SEL
        SEL --> QR[gdkpixbufoverlay<br/>QR] --> TXT[textoverlay] --> SINK[kmssink]
    end
    PB[playbin — separate GstPipeline] -.->|frames during a song| ISRC
```

The stall does not reproduce. Measured at the sink with pad probes, three
consecutive runs, with a real `GLib.MainLoop` on a dedicated thread:

| phase | frames at sink |
|---|---|
| Cold idle, before anything has ever played | 30.0 fps |
| Interstitial image swapped at runtime (`location` property) | 30.0 fps, new image in 28-34 ms |
| **Baseline, interstitial showing** | **30.0 fps** |
| **During background playbin URI swap + preroll** | **30.0 fps** |
| Switch to the song branch | 30.0 fps, first frame in 6-19 ms |
| Second background URI swap, song on screen | 30.0 fps |
| Switch back to the interstitial | 30.0 fps, first frame in 5-8 ms |

Frame counts alone prove nothing here — an unfed `intervideosrc` emits black
at exactly 30fps, which is indistinguishable from a working branch if you only
count buffers. The runs above sampled luma at the sink to confirm the content
actually changed: interstitial (violet background, mean 33) → ball-on-black
test clip (mean 18) → SMPTE bars (mean 105) → interstitial (mean 35).

**Both branches must be normalised to identical caps _before_ the selector.**
The first attempt kept the shared `videoconvert`/`videoscale`/`capsfilter`
downstream of the selector, mirroring today's layout. It failed the moment a
real 640x360 frame arrived against a 1280x720 capsfilter with no scaler in
that branch: negotiation broke and the bridge branch went to 0 fps
permanently, while the interstitial branch carried on at 30fps — which reads
exactly like a selector bug and is not one. Giving each branch its own scaler
and capsfilter fixes it, and is the right shape anyway: with matched caps on
both pads, a switch never renegotiates anything downstream, which is what
kmssink's fixed bufferpool needs (see "Why the render size is fixed").

### Pausing the idle branch

The interstitial branch runs continuously in this arrangement, including
during songs — unlike today's interstitial pipeline, which only exists between
them. That is a real cost and it needs gating. Two ways, both measured while a
song was playing:

| | branch free-running | pad-block probe | locked-state PAUSED |
|---|---|---|---|
| Process CPU | 0.17-0.20 cores | 0.02-0.03 | **0.02-0.04** |
| Song frame rate | 30.0 fps | 30.0 fps | **30.0 fps** |
| Resume latency | — | 0-4 ms | **0-14 ms** |
| Frame rate after resume | — | 80 fps (catch-up burst) | **30.0 fps (clean)** |

Both stop the branch dead (91 frames per 3s → 0). The difference is on
resume: a blocking pad probe leaves `videotestsrc` on its old timeline, so it
races to catch up and dumps a burst of late-timestamped frames at the sink —
harmless-looking for a still image, but not something to hand `kmssink`
untested. Dropping the branch's elements to PAUSED with `set_locked_state(True)`
and resuming with `sync_state_with_parent()` gets the same CPU saving and
re-syncs base time on the way back, with no burst.

So **use locked-state PAUSED**, not pad blocking.

### Note on pre-encoding interstitials to video

A third option — render each interstitial to a short looping video file so
playbin can load it like any other URI — has been raised twice and rejected
twice on the grounds that it "adds an encode to every transition." That
reasoning is wrong and should not be recycled: it conflates when the content
is *determined* with when it must be *on screen*. Rendering can start the
moment the queue changes, which is usually minutes ahead of the transition.
The only exposed case is a queue mutation seconds before a song ends, which
degrades to showing the previous or a generic screen — not to a stall.

It is still not the recommended path, but for a weaker reason: it trades the
`imagefreeze` pipeline for an encoder dependency plus segment-seek-on-EOS loop
handling, which is a similar amount of machinery somewhere less familiar. If
the selector approach falls down on hardware, this deserves a real prototype
rather than a third dismissal.

### What is still unverified

All of the above is macOS, GStreamer 1.26.0, `fakesink` standing in for
`kmssink` — the same caveat class as everything else in this document that
hasn't been to the Pi. Specifically open:

- **kmssink's behaviour across a selector switch.** Matched caps on both pads
  should mean no renegotiation and no bufferpool reallocation, but that is
  reasoning, not a measurement.
- **Whether to pin pixel format in the branch caps.** Width/height/PAR are
  pinned; format is not, matching today. Both branches should negotiate to
  whatever the sink prefers, but if they ever diverge, the format change at a
  switch is bufferpool-relevant.
- **The CPU numbers are a comparison between configurations on one machine**,
  not a prediction of Pi cost. The ratio is the meaningful part.
- Overlay behaviour across a switch, EOS and pause paths, and the actual
  refactor: none of it written or tested yet.

## Why the render size is fixed and capped

The `capsfilter` after `videoscale` pins every frame to one size for the whole
session — the display's aspect ratio, capped at `MAX_RENDER_HEIGHT` (720).
Both halves of that matter.

**Fixed**, because kmssink allocates DRM framebuffers for one frame size and
cannot reallocate them when caps change underneath it. Letting the frame size
follow the source produces `failed to activate bufferpool`, then an internal
data stream error, then a pipeline that renders nothing at all while the
console sits on screen. Adding a `queue` does not help; the constraint is
kmssink's, not a threading problem.

**Capped**, because software scaling to the display's full resolution is too
expensive to keep up. Measured on a Pi 5, 640x360 source at 30fps:

| render size | cost | sustained frame rate |
|---|---|---|
| 1920x1080 | ~46 ms/frame | 23.6 fps — drops 21% of frames |
| 1280x720 | ~25 ms/frame | 30.0 fps |
| 960x540 | ~16 ms/frame | 30.0 fps |

Rendering smaller costs nothing in quality: kmssink's upscale is free, and
sources at or below the render height pass through `videoscale` untouched.
Raising the cap to the display's resolution reintroduces frame drops, so treat
it as load-bearing rather than a tuning preference.

## Overlays

The QR code and notification text are composited **after** the capsfilter, in
the fixed render frame:

- Its top-left corner is the screen's top-left, even when the video is inset
  between bars — so the QR sits in the bar rather than on top of the video.
- kmssink scales the whole frame by one factor, so anything sized as a
  *proportion* of the render frame is a constant size on screen.

Upstream of the scaler they would be drawn onto the source frame and magnified
along with it. A QR sized for a 1080p screen drawn on a 360-line video is
about 45% of its height, and stays that big once scaled up.

## Reducing the scaler's cost

The 360p→720p upscale (~25 ms/frame, see above) adds no information — it
exists purely so overlays land in a frame with enough resolution to stay
sharp. Four ways to cut that cost were investigated; measurements below are
all on a Pi 5, 640x360 @ 30fps unless noted.

**Shipped: `videoscale method=nearest-neighbour`.** Overlays are composited
*after* the capsfilter (see above), so the scaler's method only affects the
video itself, never overlay sharpness. Nearest-neighbour halves the upscale
cost against the bilinear default:

| method | scale-stage cost | total (incl. decode) |
|---|---|---|
| bilinear (previous default) | ~25 ms/frame | ~27 ms/frame |
| nearest-neighbour (shipped) | ~12 ms/frame | ~14 ms/frame |

Content has no detail beyond 360 lines to begin with, so nearest-neighbour's
blockiness is not expected to be visible at normal viewing distance. No
other trade-off was found — it's a one-property change.

**Considered: fetch and render at 720p, making `videoscale` a passthrough.**
Cheaper decode-plus-scale than expected, but decode is *not* free at 720p —
the Pi 5 has no H.264 hardware decoder (only an HEVC decode block,
`rpi-hevc-dec`), so 720p H.264 is software-decoded via `openh264dec`.
Measured with `filesrc ! decodebin ! fakesink`, same content re-encoded at
both sizes:

| source | decode cost | scale-stage cost (passthrough at 720p) | total |
|---|---|---|---|
| 640x360 (current) | ~1.9 ms/frame | ~25 ms/frame (upscale) | ~27 ms/frame |
| 1280x720 | ~8.1 ms/frame | ~10.9 ms/frame | ~19 ms/frame |

That 10.9 ms at "passthrough" is not zero — `videoconvert` and overlay
compositing both still cost more on a bigger frame, so this is a real but
smaller win than "decode is cheaper than upscaling" suggests on its own.

It is also not currently reachable in production: `kbox/ytdlp.py` requests
`bestvideo[height<={video_max_resolution}]`, but on this deployment's yt-dlp,
YouTube's DASH formats above 360p require a JS-runtime-based signature/PO
token solver that isn't installed. Tested across `android`, `web`, `tv`, and
`web_safari` player clients — all fell back to the legacy progressive 360p
stream regardless of the configured cap. Raising `video_max_resolution` to
720 would silently do nothing today; making it work is a yt-dlp/JS-runtime
dependency question, unrelated to the pipeline itself. Worth revisiting if
that gets fixed, since it would also raise the picture's actual detail.

**Deferred: V4L2 M2M hardware scaler (`pispbe`).** Mapping the devices into
the container is trivial (`--device=/dev/video*` in `docker-compose.yml`,
not currently there) — the real question is what's behind them, and that
turned out more capable than a first pass suggested. `pispbe`
(`platform:1000880000.pisp_be`, `/dev/video20`–`/dev/video27`) is the Pi 5
ISP backend, and `media-ctl -d /dev/media1 -p` shows it as a genuine
general-purpose engine: one input node (`pispbe-input`, a plain V4L2
*output*-multiplanar queue any source can write YUV into, not
camera-specific) feeds a subdev that produces **two independently-sized**
capture nodes (`pispbe-output0`, `pispbe-output1`) — exactly the "scale to
one size for overlays, keep the other at source res" shape this problem
wants, and in hardware. `libpisp1` and the kernel UAPI headers
(`pisp_be_config.h`) are installed on the host, so the config-buffer format
libcamera uses to drive it is available without reverse-engineering.

What's missing is any GStreamer glue. `GST_DEBUG=v4l2:5 gst-inspect-1.0
video4linux2` shows the plugin *does* dynamically probe `/dev/video*` for
M2M devices — it found and registered `v4l2slh265dec` against
`rpi-hevc-dec` this way — but it explicitly skips `pispbe`'s nodes. That
tracks: `rpi-hevc-dec` is a single-node stateless-codec M2M device, the
shape GStreamer's v4l2 plugin knows how to drive. `pispbe` is a
multi-node, media-controller-linked device that also needs a per-frame
config buffer pushed through a seventh node (`pispbe-config`,
`/dev/video27`) — a different, more involved protocol with no existing
GStreamer element on either side of it.

Net: the hardware is real and well-suited to this problem, but using it
means writing a new element (media-ctl link setup at startup, libpisp-driven
config construction per frame, multi-plane buffer queuing across the input/
config/output nodes, ideally DMA-BUF-shared with `decodebin`'s output to
stay zero-copy) rather than configuring one that exists. That's a
substantial, undocumented-territory systems project — bigger than the DRM
plane option below, not a quick win. Worth a dedicated follow-up if a future
need for hardware scaling outgrows what nearest-neighbour buys; not
attempted in this pass.

**Deferred: overlays on their own DRM plane.** `kmssink` takes a `plane-id`
property, and the Pi has spare planes free at runtime (checked via
`/sys/kernel/debug/dri/*/state` — the video currently claims one plane,
several more sit unused). Moving overlays to a second plane would decouple
overlay sharpness from render size, unlocking a 360p render (video-only
scale-stage cost ~2.7 ms/frame, passthrough) without softening the QR or
text. This is real headroom, but it means a second sink/pipeline and
plane-level compositing — the highest-complexity option here, and not
attempted in this pass. Worth a dedicated follow-up if the nearest-neighbour
change turns out not to be enough.

## Measuring changes

The display pipeline is on the hot path for every frame.
`test/test_pipeline_benchmark.py` measures the stages, building its pipelines
from the same `kbox.streaming.elements` factory functions the app uses (see
the file's docstring) so it can't quietly drift from what actually runs:

```bash
# on the Pi, inside the container — the only numbers that mean anything
docker-compose exec kbox python3 -m pytest test/test_pipeline_benchmark.py -m benchmark -s

# on a macOS dev machine
uv run pytest test/test_pipeline_benchmark.py -m benchmark -s

# run it directly for custom sizes, framerates, or a longer audio run
uv run python test/test_pipeline_benchmark.py --source 640x480 --screen 1920x1080 --fps 25
```

Its headline metric is the frame rate a configuration could sustain, not CPU
percentage, and that choice is deliberate — see the trap about it below.

## Traps

Each of these cost at least one round of deploy-and-look-at-the-screen.

### NULL versus READY makes no difference to the sink

`GstBaseSink.stop()` runs on the **PAUSED→READY** transition, not on NULL. A
sink inside playbin is torn down either way, so dropping to READY instead of
NULL to "keep the sink alive" does nothing at all. The sink's lifetime is what
matters, which is why it lives in its own pipeline.

### NULL versus READY also makes no difference to format renegotiation

`load_file()` used to drop `playbin` all the way to NULL before swapping `uri`, on the
theory that READY would leave the internal `uridecodebin` graph (demuxer, decoder)
wired for the old file's format and risk a bad renegotiation on the next song. That
turned out to be inherited habit from the original per-song pipeline design, predating
the persistent-`playbin` refactor (`git log -S` traces the NULL call to the WIP commit
that introduced the persistent pipeline) — not something chosen for a specific reason
at the time.

Tested directly: cycled one `playbin` between two on-disk files with deliberately
mismatched containers, codecs, resolutions, framerates, sample rates and channel counts
(h264/aac, 320x240, 30fps, in mp4 vs. vp8/vorbis mono, 640x480, 25fps, in webm),
swapping `uri` and going back to PLAYING from both READY and NULL, several cycles each.
Every transition succeeded, no bus errors either way — `uridecodebin` rebuilds its
dynamic element graph on a `uri` change regardless of which state it dropped from.

(Tested on macOS with `autoaudiosink`/`autovideosink`, not `alsasink`/`kmssink` on the
Pi.) On that basis `load_file()` now drops to READY instead of NULL — cheaper, and
matches `stop_playback()`. This is flagged for the next pre-release burn-in pass:
watch specifically for anything on song transitions between very different source
formats (resolution, codec, channel layout) that didn't happen before this change.

### `intervideosrc`'s `timeout` does not hold the last frame

Its description — *"Timeout after which to start outputting black frames"* —
reads like a hold duration. It is not. Measured hold length is identical at
1 second, 5 seconds and a year: `intervideosrc` serves a received buffer a
couple of times and then generates black regardless. The property governs
waiting for a *first* buffer.

Consequence: **something must keep feeding the bridge** or the screen goes
black. That is the entire reason interstitials have their own `imagefreeze`
pipeline.

### playbin does not insert `imagefreeze` for still images

A still image decoded on its own yields one buffer and then EOS. Playing a PNG
through playbin therefore starves anything downstream.

This worked in the first generation only by accident: kmssink was inside
playbin and went on scanning out its last framebuffer after EOS. That
persistence disappeared the moment the sink moved to its own pipeline, and the
idle screen started blanking to black a second after every stop.

### `inter` elements only pair up inside one process

`intervideosink` and `intervideosrc` find each other through a process-global
registry. Two separate `gst-launch-1.0` processes **never connect** — both
pipelines run happily and exit 0, the receiver quietly emitting black frames
the whole time.

Any test of the bridge has to build both pipelines in a single process.
Otherwise it will appear to pass while testing nothing.

### kmssink already scales in hardware

It is easy to assume the sink needs to be handed screen-sized frames. It does
not. With plain defaults on a Pi 5 (`vc4-drm`), given a 640x360 frame on a
1920x1080 screen, kmssink logs:

```
drmModeSetPlane at (0,0) 1920x1080 sourcing at (0,0) 640x360
```

Upscaling in software before the sink duplicates work the display controller
does for free, at ~46 ms/frame. Only the *aspect ratio* of the frame needs
fixing, not its resolution.

Useful when investigating: `GST_DEBUG=kmssink:7` prints the plane rectangle it
actually programs, which beats guessing from what the screen looks like.

### `kmssink` cannot run two instances against one DRM device

The Pi 5 has two micro-HDMI ports and the hardware fully supports driving
both independently — the DRM connectors enumerate fine in `/sys/class/drm/`,
each at its own connector ID. It is tempting to route a `tee` into two
`kmssink`s, one per `connector-id`, to show different overlays on each
display. This fails:

```
drmModeSetPlane failed: Permission denied (13)
```

or a "failed to configure video mode" error, depending on the exact
combination of properties tried. Each `kmssink` instance tries to become DRM
master, and they conflict — this held across privileged Docker mode, adding
the `video`/`render` groups, and toggling `force-modesetting`, so it is not a
permissions issue to work around.

Two real options if independent per-display overlays are ever needed: switch
to a Wayland/Weston compositor and `waylandsink` (significant pipeline
changes), or drive both outputs with identical content via OS-level display
mirroring (`wlr-randr`/`xrandr`), which needs no pipeline changes at all. For
same-content-on-both-screens (the common karaoke case), a plain HDMI
splitter downstream of a single `kmssink` sidesteps the problem entirely and
is what kbox uses today.

### `textoverlay` already scales its own font

`auto-resize` is on by default and scales the font relative to a 640-pixel-wide
frame. Setting a font size derived from the display scales it a second time —
on a 1080p screen that turned `Sans 9` into roughly 66pt of text across most of
the width.

Leave the font alone and let `auto-resize` do it. `xpad`/`ypad` are raw pixels
that `auto-resize` does *not* touch, so those do need scaling by hand.

### CPU percentage hides frame drops

A configuration that cannot keep up does not report 100% CPU — it drops frames
and reports something comfortable. This branch sat at ~52% while dropping a
fifth of its frames, and the deficit was invisible on screen because karaoke
content is mostly a lyric highlight sweeping over a low-detail background.

Measure sustained frame rate, not CPU share. Count buffers reaching the sink
against a realtime clock over a fixed interval.

### The display pipeline needs its own bus watch, and a watch means polling

It is a separate `GstPipeline`, so it has a separate bus. Without something
reading that bus, a failure is completely silent: the app logs a clean startup,
every component reports success, and the screen shows the console.

`bus.add_signal_watch()` plus `bus.connect("message::error", ...)` looks like
the obvious way to get that, and it is what the code did for a long time — but
it never delivered a single message. A signal watch dispatches from a GLib main
loop, and kbox does not run one (there is no `GLib.MainLoop` anywhere in the
tree; the app's event loop is uvicorn's). The handlers were installed, looked
right in review, and were dead code. EOS and errors on *playbin's* bus still
worked, which hid the problem, because those came from a separate polling
thread rather than from the signal watch.

So both buses are drained by polling threads instead (`BusWatcher` in
`kbox/streaming/bus.py`, started by `StreamingController._start_bus_polling()`):
one for playbin's bus, one for the display pipeline's, each doing
`bus.timed_pop(100ms)` and routing EOS, ERROR and WARNING to the handlers.
`BusWatcher` owns only the thread; what a message means is the dispatch
callable it is given — `_dispatch_playbin_message` on the controller,
`DisplayPipeline.dispatch_bus_message` for the display pipeline's.
The `add_signal_watch()` / `connect()` calls are gone, deliberately — leaving
them in implied a delivery path that does not exist. If you add another
pipeline with a bus worth watching, give it a poller; do not reach for a signal
watch unless you are also introducing a main loop, which is a much larger
change to how the app runs.

`_stop_bus_polling()` stops and joins every watcher, and each loop re-reads
its bus every pass so that teardown setting `self.playbin = None` ends the loop
rather than crashing it.

**It also does not currently fire.** `bus.add_signal_watch()` delivers
messages through a GLib main loop, and kbox never runs one — there is no
`MainLoop` anywhere in `kbox/` or `test/`. playbin's bus is covered regardless
by `_start_bus_polling()`, a thread doing `timed_pop`, but that thread handles
only EOS and ERROR, so `_on_warning` and its audio-device check are dead code
too. The display pipeline has no polling thread at all, so `_on_display_error`
never runs and the silent-failure mode this trap warns about is still live
despite the watch being installed. Fixing it means polling the display bus as
well, or running a real main loop.

## Running the GStreamer tests

The pipeline tests (`test/test_streaming.py`) are plain tests in the default
test suite (`uv run pytest`) -- there is no marker gating them. They do not
skip when GStreamer or one of its plugins is unavailable -- they fail loudly,
on the theory that a quietly-skipped test is worse than no test at all. So
they only belong on a machine that actually has GStreamer: a macOS dev machine
with the dev dependencies installed, or the Docker image.

```bash
# on a macOS dev machine
uv run pytest

# on the Pi, inside the container
docker-compose exec kbox python3 -m pytest
```

CI runs the full suite, including these tests, inside the Docker image on both
target architectures (see the matrixed `test` job in
`.github/workflows/ci.yml`) -- there's no separate bare-metal CI job that skips
them.

macOS needs PyGObject, which is declared as a dev dependency for
`sys_platform == 'darwin'` only — Linux and the Docker image use the system
`python3-gi` package instead. If `import gi` fails, run `uv sync --group dev`.
macOS also needs the native signalsmith pitch-shift plugin built once --
see `native/gst-signalsmith-pitch/README.md` -- or
`test_signalsmith_pitch_shift_actually_shifts_frequency` fails.

`test/test_streaming.py` and `test/test_pipeline_benchmark.py` both call
`configure_macos_gstreamer_env()` (in `kbox/platform.py`) before importing
`gi`, which pins everything to the Homebrew GStreamer — the same helper
`kbox/main.py` uses to run the app itself on macOS. A Mac can easily end up
with both that and the official `GStreamer.framework` from the binary
installer, and mixing libraries from one with plugins from the other fails in
confusing ways — so the setup requires `brew --prefix glib gstreamer` to
resolve and raises a clear error instead of silently falling through to
whatever `import gi` happens to find.

These tests cover pipeline *structure*, not hardware behaviour. macOS has no
`kmssink`, so anything involving the console, plane scaling or mode reporting
can only be verified on a Pi.
