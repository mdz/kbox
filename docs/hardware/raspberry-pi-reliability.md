# Raspberry Pi Reliability Guide

kbox is designed so the technology gets out of the way — songs auto-advance, the next
singer is announced before the current one ends, and nobody should be stuck on IT duty
mid-party. A Raspberry Pi that reboots, blanks its HDMI output, or loses its filesystem
during a song breaks all of that in one shot: the room stalls, the performer is
embarrassed, and whoever's running the show is now debugging hardware instead of
hosting.

This guide exists so that the next hardware failure gets diagnosed in minutes at home,
not improvised during a live event. It covers the three things that actually bring a Pi
down mid-show — power, storage, and heat — plus a diagnostics toolkit and a checklist to
run before trusting new hardware at a real party.

This guide is Raspberry Pi-specific. For audio wiring and mixing setups (interfaces,
mixers, HDMI audio injectors), see [hardware-setup.md](hardware-setup.md).

## Power

The Pi is unforgiving about power quality. A supply that's "close enough" can run fine
for hours and then fail exactly when it matters — under sustained load, once everything
has warmed up, or the moment a peripheral draws a bit more current.

**Use the genuine official supply for your board.** On Pi 5 specifically, the official
27W USB-C supply negotiates a 5V/5A profile via USB-PD; a supply that doesn't negotiate
this will silently cap the current budget shared across all USB ports, which can starve
attached storage under load. See the
[official Raspberry Pi power documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#power-supply)
for the spec details.

**Symptoms of under-voltage:**
- HDMI output blanking (the firmware disables HDMI to shed load before a full brownout)
- Reboot loops with no obvious software cause
- On Pi 5, a boot-time message about USB boot requiring a "high current 5V/5A" supply

**Diagnose it directly:**
```bash
vcgencmd get_throttled
```
The result is a bitmask — `0x0` means clean. Key bits:

| Bit | Meaning |
|-----|---------|
| 0x1 | Under-voltage detected (right now) |
| 0x2 | ARM frequency capped (right now) |
| 0x4 | Currently throttled |
| 0x8 | Soft temperature limit active (right now) |
| 0x10000 | Under-voltage has occurred since boot |
| 0x20000 | Frequency capping has occurred since boot |
| 0x40000 | Throttling has occurred since boot |
| 0x80000 | Soft temperature limit has occurred since boot |

**Keep a verified spare supply on hand.** Even a "solid for months" supply can fail
without warning — having a known-good spare turns a potential no-show into a two-minute
fix.

## Storage

**Avoid unbranded USB bridge chips.** A cheap or generic USB-to-SATA/NVMe bridge inside
a thumb drive is a common source of intermittent bus dropouts, `udev` timeouts, and even
kernel panics under sustained load — and these chips are often untraceable after the
fact. Sanity-check any USB storage device before trusting it:
```bash
smartctl -a /dev/sdX
```
If it reports `Unknown USB bridge` and won't return real SMART data even after trying
`-d sat`, `-d sat,12`, etc., treat that as a yellow flag — it means the device isn't
using a well-established, well-tested bridge chip.

**A reputable-brand bus-powered SSD is the low-effort reliable option.** Known brands
(SanDisk, WD, Samsung, Crucial, etc.) use controllers that have been through far more
real-world hours and compliance testing than an anonymous thumb-drive bridge chip, at
comparable cost.

**NVMe over PCIe removes USB from the equation entirely.** Pi 5 exposes a real PCIe
lane, and the official M.2 HAT+ (plus third-party equivalents) lets an NVMe drive talk
directly to the Pi's controller — no USB bridge chip in the path at all. Tradeoffs:
- Official HAT+ only supports **M.2 2230/2242** length drives, not the more common 2280
  — check this before buying a drive
- Favor lower-power drives (DRAM-less, or laptop-OEM parts designed for battery life)
  over high-performance "gaming" drives, which can exceed the HAT's power budget
- See the [official M.2 HAT+ documentation](https://www.raspberrypi.com/documentation/accessories/m2-hat-plus.html)
  and the [Pi forums thread on SSD reliability](https://forums.raspberrypi.com/viewtopic.php?t=371936)
  for current compatibility notes

**Plain microSD is the riskiest cheap option**, not because it's slow, but because a
dirty power loss (a crash, a pulled plug) can corrupt the filesystem outright. It's a
reasonable emergency fallback, not a long-term choice for the primary boot device.

## Cooling

Pi 5 can run genuinely hot under sustained load (continuous video decode, real-time
pitch shifting), but a passive heatsink case is not automatically insufficient — plenty
of setups run for hours with zero thermal issues on passive cooling alone. Don't assume
you need active cooling; check.

**The failure pattern to watch for is thermal soak**: a unit that's been rock-solid for
hours and then suddenly starts failing, with nothing else having changed. Case and board
thermal mass means temperature can climb slowly under sustained load and only cross the
throttle/shutdown threshold well into a long session — which is exactly the scenario a
short test at home won't catch.

**Diagnose directly:**
```bash
vcgencmd measure_temp
vcgencmd get_throttled   # bits 0x8 / 0x80000 indicate the temp limit specifically
```

See the [official Pi 5 thermal documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html)
for the specific throttle/shutdown temperatures for your board revision.

## Diagnostics toolkit

**Live monitoring**, run these in parallel (tmux/split terminal) while reproducing an
issue:
```bash
vcgencmd measure_temp
vcgencmd get_throttled
dmesg -w
journalctl -f
```

**Enable persistent journal storage before you need it** — by default, journald on
Raspberry Pi OS often keeps logs only in RAM (`/run/log/journal`), which are wiped on
every reboot, crash or not. This means the most interesting logs — the ones from right
before a crash — are exactly the ones you lose if you didn't set this up in advance:
```bash
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
```

**Reading logs from an offline root filesystem** (e.g. a drive that's failed and been
moved to another system, or mounted read-only for inspection):
```bash
sudo journalctl --root=/mnt --list-boots
sudo journalctl --root=/mnt -b -1
```

**Deliberately provoking an intermittent storage failure** for diagnosis — mount the
suspect drive as a *non-root* filesystem (so a crash is recoverable and observable
rather than fatal) and hammer it with realistic and/or synthetic load while watching
logs live:
```bash
sudo mount /dev/sdXn /mnt/test
# realistic: loop copying large files, matching the app's actual read/write pattern
# synthetic: stress-ng --iomix 4 --timeout 0
```
Give it real time — a unit that took hours to first fail under normal use may not
reproduce the failure in a five-minute synthetic test.

## Reference build

Current known-good hardware for this project's Pi 5 unit:

| Component | Choice | Why |
|-----------|--------|-----|
| Power supply | Official Raspberry Pi 27W USB-C | Verified 5V/5A negotiation |
| Case + HAT + cooling | GeeekPi Metal Case w/ Official M.2 HAT+ and Active Cooler | Pre-matched bundle, no fit-checking needed |
| Storage | Samsung PM991 M.2 2242 NVMe | Correct form factor for the HAT, reputable controller, modest power draw |
| Spare power supply | Same official 27W unit | On-hand replacement, no diagnosis needed if the primary fails |

The original USB thumb-drive SSD (unidentified bridge chip) has been retired from boot
duty and kept as a general-purpose thumb drive.

## Pre-event checklist

Before trusting new or changed hardware at a real event:

- [ ] Run a burn-in session of at least several hours under realistic load (actual video
      playback, not idle) — thermal soak failures don't show up in short tests
- [ ] Confirm persistent journal logging is enabled, so a failure during burn-in is
      actually diagnosable
- [ ] Check `vcgencmd get_throttled` after the burn-in — `0x0` means clean
- [ ] Confirm you have a tested fallback boot device ready (not just purchased —
      actually imaged and verified bootable)
- [ ] Confirm a spare power supply is packed
