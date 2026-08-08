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
- [x] **7. UI rework requested 2026-08-08** — implemented and deployed.
      **Geometry verified numerically from the device's own boot log; the
      screen itself still has not been looked at.**
- [x] **8. `tools/malformed_probe.py` actually run** — see below.
- [x] **9. Battery gauge de-glitched** — see below.

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

## UI rework requested 2026-08-08 — DONE (except the visual check)

Requested: consolidate the signal coach into the SIG tile and make SIG the
largest of the set; put BPM and IBI together in one window; **larger** BPM/IBI
text; remove screen flicker. All implemented and deployed.

**Flicker.** Every painter is now change-driven through `changed()`/`field()`,
and there is **no repaint timer left in the main loop**. What was removed:

- `draw_header()` — deleted. It blanked the whole 1280×90 bar and redrew it
  every 500 ms unconditionally; that was the single biggest flicker source.
  Its parts are now `draw_link_tag()`, `draw_link_bars()`, `draw_battery()`,
  each repainting only on a real change, plus static chrome.
- The BPM tile's **beat flash is gone** — inverting a 561×180 panel 120×/min
  was the second worst. The beat is still reported twice: the header heart and
  the yellow marker on the waveform.
- The **heart no longer changes size**. Shrinking it meant blanking a box
  around it twice per beat. It is now one fixed shape repainted in a different
  colour, so the pulse costs no erase at all.
- Static chrome (borders, divider, fixed labels, title block) is painted once
  in `draw_static()`.
- `draw_tile()`/`draw_tiles()` deleted — they refilled a whole panel per update.

**Layout.** Two panels, printed by the device at boot so the fit is checked
against measured metrics rather than assumed. Real numbers from the Tab5:

```
LAYOUT: vitals x=20 w=561 half=280 | sig x=601 w=659
LAYOUT: value face h=104  '8888' w=248  fits 254  (y=584)
LAYOUT: coach face h=52   longest w=498  fits 631 x 70
```

SIG (659) is wider than the vitals window (561), as asked. BPM/IBI are at the
**104 px** face with 6 px of width to spare — that margin is the whole budget,
so any font or padding change must be re-checked against these lines.

Two things worth knowing if you touch this:

- `_avail` uses **one** `TILE_GAP`, not two — there are two panels now. The
  geometry sketched in the previous handoff assumed the old three-panel
  `_avail` and would have wasted 20 px.
- The IBI unit rides on the **label** (`"IBI ms"`). At 104 px a four-digit IBI
  already consumes `VAL_MAX_W`; a suffix beside the number would have forced
  the face down to 52 px, undoing the whole point.

**The sweep used to eat the annotations.** `clear_column()` erases full-height
columns, so the sweep wiped `THR`/`LED BEAT` as it passed under them and they
only came back on the next wrap or value change. `draw_wave()` now notices when
the sweep is inside the annotation band (`ANN_GX`) and forces a repaint. Any
region-wiping function is now responsible for `forget()`-ing the fields it
destroyed — `draw_graph_frame()` does this.

**Not done: nobody has looked at the screen.** Everything above is arithmetic
and serial output. "Does it look right, and is 104 px big enough" is Yury's
call — that is the only open item on the UI.

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
- **Battery gauge is flaky — now filtered, but the fault is still there.** The
  raw gauge alternates between `100% (8393 mV)` and `0% (~4360 mV)` every ~5 s,
  i.e. it intermittently reports one cell of the 2S pack instead of the pack.
  `batt_sample()` takes the **max over an 8-sample, 1 Hz window**. A median
  does *not* work here and was not used: the fault is a slow square wave, so
  any window spanning it just flips whenever the sample counts tip. Max rejects
  the half-scale misread and still tracks a real discharge, lagging by at most
  the window. Measured on hardware after the fix — `raw=` flaps, `batt=` does
  not:
  ```
  rx=833  bad=0 rate=24/s linked=1 batt=100%(8393mV) raw=0%(4352mV)
  rx=958  bad=0 rate=25/s linked=1 batt=100%(8393mV) raw=100%(8393mV)
  rx=1208 bad=0 rate=25/s linked=1 batt=100%(8393mV) raw=0%(4370mV)
  ```
  **Anything reading power must go through `batt_sample()`, not `M5.Power`.**
- Link bars come from **measured packet rate**; the AP exposes no RSSI.

## Test tooling

- `tools/malformed_probe.py` — **run 2026-08-08, PASSED.** Runs on the stick
  (`stick.sh run tools/malformed_probe.py`), joins the AP and sends short /
  long / bad-magic / wrong-version / valid packets over the real link.
  Result: `bad` went **0 → 20** across 5 rounds, exactly the predicted 4
  rejects per round, and `LINK: stick aabbcc joined` confirms the valid packet
  in each round *was* accepted and attributed to the probe's fake device id.
  No crash, no mis-parse. Redeploy `pulselink.py` to the stick afterwards — the
  probe leaves it sitting at the REPL.
- `tools/timeit.py` — dual-serial timing harness (see above). It is in the repo
  now; the older note calling it `scratchpad/timeit.py` was stale.

## Traps that have already cost real time

- Main loop must `time.sleep_ms()` **unconditionally** every iteration, or the
  task watchdog starves and the board reboots forever (`rst:0x8 TG1WDT_SYS_RST`).
- **No broad `except: pass` on the send path.** It has hidden three real bugs
  now: a `NameError` on `ibi`, `Wifi Internal State Error` aborting
  `link_init()`, and the ENOMEM stall above.
- `./stick.sh run` reuses REPL globals; code can pass there and still
  `NameError` on a cold boot. **Verify with `deploy` + a real reset.**
- `stick.sh deploy` to the Tab5 used to look like a 45 s hang. **Fixed.** The
  cause was `mpremote exec machine.reset()` waiting for an exec to return on a
  board that immediately starts spewing serial. `stick.sh` now resets over raw
  pyserial (`hard_reset()`); a Tab5 deploy takes ~4 s and exits cleanly. There
  is also a `stick.sh reset` now. No more `pkill -f mpremote`.
- **Identifying the ports with `stick.sh status` kills whatever is running.**
  `status` interrupts the REPL, so probing both ports to work out which is
  which leaves both boards idle and the link dead — easy to misread as a link
  bug. `stick.sh reset` (or a redeploy) brings them back.
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
