# PulseLink Tab5 — implementation handoff

All work below is **approved** by Yury. Branch `tab5-remote-display`.

## STATUS

- [x] **1. Merge + rename + purge CYD** — commit `9436ac8`, pushed.
- [x] **2. Sender hardening** — `a6e10cc`, plus two real bugs found by
      measuring it (below) and fixed this session. Deployed and verified.
- [x] **3. Tab5 strict validation, waveform FIFO, capped renderer** — done,
      deployed, verified receiving at 25/s with `bad=0`.
- [x] **4. Deploy + verify both devices** — done for link behaviour and
      malformed-packet rejection paths. **Layout NOT visually confirmed.**
- [ ] **5. Physical cold-power cycles + BtnA RESYNC / BtnB checks** (needs
      Yury's hands — never done)
- [ ] **6. `docs/hackster/` text pack** (only after hardware passes)
- [ ] **7. UI rework requested 2026-08-08** — see "NEXT UP" below. Partly done.

Repo will eventually move to the World Famous Electronics org. Only the README
clone command hardcodes `yury-g`.

## What changed this session

### Two sender bugs, both found by actually measuring task 2

Task 2 was code-complete but unmeasured. Measuring it exposed two defects that
the previous methodology could not have seen:

1. **Zombie association / permanent ENOMEM stall.** When the Tab5 reboots, its
   SoftAP vanishes *without deauthenticating* the stick. `isconnected()` keeps
   returning `True`, and every `sendto()` fails `OSError: ENOMEM` **forever** —
   measured `rx=0` on the Tab5 across a full 26 s window, never recovering.
   Rebuilding the *socket* cannot fix this because the socket is not what is
   broken. Fix: `LINK_ZOMBIE_MS = 2000` — after 2 s of solid send failures,
   drop the association and rejoin. **This is the real cause of the "Tab5
   reception stall" that the old handoff attributed to the receiver.**
2. **`_link_fatal` latched the link dead.** Status 202/203/204 were treated as
   terminal ("wrong password, stop hammering"). They are **transient** here:
   the Tab5's AP refuses associations for a moment right after it starts. One
   unlucky retry permanently killed the link for the rest of the session; the
   only reason it ever recovered was the IDF driver's own internal retry.
   Fix: removed `_link_fatal` entirely, retry on the normal backoff.
3. **Retry ceiling 3000 ms → 1000 ms.** Directly observed: the AP came up at
   t=8.288 s while the stick sat mid-backoff and did not retry until 9.803 s —
   **1.5 s of pure dead time** — then association itself cost 1.84 s.

### Tab5

- `parse()` requires **exact 24 bytes and version == 3** (was `len >=` + magic).
- Bounded waveform FIFO (`WAVE_FIFO_MAX`), drained on a capped cadence
  (`RENDER_MS = 33`, `MAX_PER_FRAME = 16`) consuming every queued sample.
  Connected-line partial drawing preserved via `last_gy`. Beat edge is marked
  once per packet rather than smeared across every sample.
- AP startup moved to immediately after `M5.begin()`. **This produced no
  measurable gain** — the ~3.4 s before the AP appears is inside `M5.begin()`
  itself, not the font/layout pass. Correct ordering, but do not expect time.
- `rx_bad` counter; stat line is now `rx=N bad=N rate=N/s ...`.

## Measurement — READ THIS BEFORE COMPARING TO THE OLD BASELINE

**The old baseline table is not comparable and should not be trusted as a
target.** It claims "Tab5 AP active 0.747 s", which is unreachable from a real
reset: the Tab5's firmware alone takes ~4.9 s to boot, and `M5.begin()` adds
~3.4 s before the AP can come up. The old numbers were almost certainly taken
from a warm start (`mpremote run`-style), not a cold boot.

Harness: `scratchpad/timeit.py` (copy it forward — it is not in the repo).
It resets devices over the wire and reads **both** serial ports passively,
never attaching mpremote after t0. The stick's `[ms]` prefixes let its own
clock be recovered even though the S3's early output is lost to USB
re-enumeration.

Metric that actually means something: **AP-up → first valid packet**, since
that is the only window the sender controls.

| Scenario | 3 s ceiling (n=4) | 1 s ceiling (n=2) |
|---|---|---|
| both together | 3.06 / 4.11 / 5.51 | 3.40 / 3.51 / 3.51 |
| stick first | 2.99 / 3.40 / 3.51 | 1.46 / 3.13 / 3.13 |
| tab5 first | 2.17 / 2.36 / 2.43 | 2.05 / 2.53 / 2.53 |
| tab5 only (reboot recovery) | 1.89 / 4.88 / 4.97 | 3.56 / 3.68 / 3.68 |
| stick only (assoc, stick clock) | 2.79 / 2.87 / 2.89 | 3.01 / 3.42 / 3.42 |

(min / median / max.)

**Honest reading:** the 1 s ceiling bounds the tail (both-together worst case
5.51 s → 3.51 s) but medians are **within noise at n=2**. The dominant cost is
the ~1.8 s association itself, not the retry gap. If you want a real verdict,
run n≥10 per scenario. The unambiguous win this session is that `tab5_only`
recovers **at all** (`rx=0` forever → `rx=400+` every run).

## NEXT UP — UI rework requested 2026-08-08

Requested: consolidate the signal coach into the SIG tile and make SIG the
largest of the set; put BPM and IBI together in one window; **larger** BPM/IBI
text; remove screen flicker.

**Done so far:**
- Font layer generalised to a "face" = `(font, integer_scale)`, `FACE_STACK`.
- `TILE_H` 144 → 180, so BPM/IBI now select `(DejaVu72, 2)` = **104 px**,
  double the previous 52 px. Verified numerically, **not visually.**
- Flicker-free primitives written and ready but **NOT YET WIRED UP**:
  `changed()`, `forget()`, `field()`, `_clear_outside()`.

**Still to do:**
- Wire `field()`/`changed()` through `draw_header`, `draw_annotations`,
  `draw_tile`, `draw_signal`, `draw_battery`. **The main flicker source is
  `draw_header()`, which blanks the whole header bar with `fillRect` and
  redraws it every 500 ms whether or not anything changed.** Second worst is
  the BPM tile's beat flash, which inverts the entire tile — recommend
  dropping it and keeping the heart pulse + waveform beat marker.
- Split the bottom row into **two** panels: `[BPM | IBI]` in one window, and a
  wider SIG panel carrying the coach text (currently in the header). Worked-out
  geometry: `VIT_W = (_avail * 46) // 100`, `SIG_W = _avail - VIT_W`; that
  keeps SIG the larger panel while still leaving `VIT_HALF - 26 = 254 px`,
  enough for `"8888"` at `(DejaVu72, 2)` = 248 px. Do the arithmetic before
  trusting it — it is tight.
- Static chrome (borders, fixed labels, title) should be painted **once** at
  boot, not on every update.

## FONT FACTS — measured on the Tab5, do not guess

**The font names are NOT pixel heights.** Measured `fontHeight()`:

| Face | h | `"888"` w | `"8888"` w |
|---|---:|---:|---:|
| DejaVu72 | **52** | 93 | 124 |
| DejaVu56 | 49 | 84 | 112 |
| DejaVu40 | 44 | 78 | 104 |
| DejaVu24 | 27 | 45 | 60 |
| DejaVu9 | 15 | 24 | 32 |

52 px is the **largest real glyph in the build**. Anything bigger requires
`setTextSize(N)`. Also available: `Montserrat12..48` (48 → h=52, same ceiling),
`ASCII7`, and CJK faces. Full list in the git history of this file's session.

## Hardware / environment facts

- StickS3 `/dev/cu.usbmodem31201`, chip id `70041dd5513c`, ESP32-S3-PICO-1,
  UIFlow2 **2.4.9**. Sensor on **G2/GPIO2 at 3V3**.
- Tab5 `/dev/cu.usbmodem31101`, chip id `80f1b2d16bf1`, ESP32-P4, UIFlow2
  **2.5.0**, 1280×720. Ports swap — identify by chip id, use `STICK_PORT=`.
- Both MicroPython **1.27.0**.
- Link = **UDP over SoftAP**, `PulseSensor-Link` / `pulse1234`,
  `192.168.4.1:5005`. **NOT ESP-NOW** — the P4 has no radio.
- Boot budget, measured: Tab5 firmware ready ≈ 4.9 s after reset, AP up
  ≈ 8.3 s. The ~3.4 s gap is inside `M5.begin()`.
- **Battery gauge is flaky and now MATTERS.** It alternates between
  `100 (8393 mV)` and `0 (4362 mV)` every ~5 s. The old note that it always
  read 0 is out of date — a battery is present now. The indicator will visibly
  flip between a full bar and the "EXT" pill. Needs debouncing or a
  median-of-N before the power dashboard (roadmap item 3) is worth building.
- Link bars come from **measured packet rate**; the AP exposes no RSSI.

## Test tooling

- `tools/malformed_probe.py` — **written, never run.** Runs on the stick
  (`stick.sh run tools/malformed_probe.py`), joins the AP and sends short /
  long / bad-magic / wrong-version / valid packets over the real link.
  Expect `bad=` +4 and `rx=` +1 per round on the Tab5. **Run this.**
- `scratchpad/timeit.py` — dual-serial timing harness (see above).

## Traps that have already cost real time

- Main loop must `time.sleep_ms()` **unconditionally** every iteration, or the
  task watchdog starves and the board reboots forever (`rst:0x8 TG1WDT_SYS_RST`).
- **No broad `except: pass` on the send path.** It has hidden three real bugs
  now: a `NameError` on `ibi`, `Wifi Internal State Error` aborting
  `link_init()`, and the ENOMEM stall above.
- `./stick.sh run` reuses REPL globals; code can pass there and still
  `NameError` on a cold boot. **Verify with `deploy` + a real reset.**
- `stick.sh deploy` to the **Tab5 appears to hang** — mpremote does not exit
  cleanly while the app spews serial. The copy and reset DO complete. Background
  it, `pkill -f mpremote` after ~45 s, then verify passively.
- `lcd.print()` paints an **opaque background box** — draw icons **after** text.
  This is also what makes flicker-free in-place repainting possible.
- `mpremote` attaching **stops the running app**. Read serial **passively**
  with pyserial to check a running device.
- Use `/usr/bin/python3` (Homebrew python3 lacks pyserial/mpremote).
  `timeout` is **not installed** on this Mac — do not use it in scripts.
- UIFlow2 NVS `boot_option` must be `0` or the launcher hijacks `main.py`.

## Test matrix before claiming done

Malformed packets (short, long, bad magic, wrong version) — via
`tools/malformed_probe.py`. All five restart orders, n≥10 for a real verdict.
Several **physical** power cycles. BtnA RESYNC and BtnB. Verify passively, and
look at the screen for anything layout-related.
