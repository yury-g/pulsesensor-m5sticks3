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
- [~] **5. Physical hardware checks** — partly done 2026-08-08:
      - [x] **Tab5 physical power cycle — PASSED.** Yury power-cycled the Tab5
            while the stick was running. It came back up and **the stick
            re-established the link on its own** (`rx` climbing, `linked=1`,
            `rate=25/s`). This is the first *physical* confirmation of the
            zombie-association fix from the previous session — that exact
            scenario used to leave the stick in permanent `ENOMEM` with `rx=0`.
      - [x] **Tab5 RESYNC banner render path** — exercised over the real link
            with `tools/resync_probe.py`, no crash. Note this proves the
            *receiver*, not the button.
      - [ ] **BtnA RESYNC / BtnB on the stick** — still needs Yury's hands.
      - [ ] **Stick physical power cycles** — still needs Yury's hands.
- [ ] **6. `docs/hackster/` text pack** (only after hardware passes)
- [x] **7. UI rework requested 2026-08-08** — implemented, deployed, and
      **visually signed off by Yury on 2026-08-08 ("it's much better")**.
      Geometry also verified numerically from the device's own boot log.
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

## Endurance soak — 30 min, 2026-08-08, commit `925c196`

Undisturbed, both devices, nothing attached to the ports but a passive reader.

| Measure | Result |
|---|---|
| Duration | 1798 s, uptime monotonic 90 s → 1886 s (**no reboot**) |
| Packets | 47,075 received, **`bad=0`** throughout |
| Rate | 44,903 packets over 1796 s = **25.0/s** against 25/s expected |
| Faults | **zero** — no `rst:`, traceback, stall, socket rebuild or AP restart |
| Free heap | drift **−4,516 bytes** over 30 min |

**No leak.** The heap drift is −0.019%, and the GC oscillation spread over the
same window is 12,880 bytes — the drift is well inside the noise. Judge it that
way rather than by eyeballing two numbers; a single low sample at the end of a
run reads like a leak and usually is not.

Note what this soak does **not** cover: it is a steady-state test with the link
already up. The stall below happens at *reboot*, so a soak that never restarts
the Tab5 will never see it, however long it runs.

## Receiver-side stall watchdog (2026-08-08)

**Reproducible but intermittent: roughly 1 in 6 Tab5 reboots.** After the Tab5
restarts, the stick associates (the AP reports the station) and the Tab5 then
receives either nothing or a few dozen packets and stops. Without a watchdog
this is **permanent** — observed once sitting at `rx=6, rate=0/s` for over
100 s, and only a second Tab5 reboot cleared it.

What the evidence supports, and what it does not:

- The stuck state is on the **receiver**. Resetting the stick does not clear
  it; resetting the Tab5 does.
- It is **not** a serial/tooling artefact. It reproduces with nothing attached
  to the port during boot (`rx=31, rate=0/s`).
- `rx` is often **non-zero** before it stops (31, 57, 6). The socket works
  briefly and *then* dies, which argues against a simple "bound before the
  interface was ready" story and suggests something reconfigures the netif
  shortly after the station associates.
- Rebuilding the socket clears **some** stalls outright and does nothing for
  others — one run took three rebuilds with no effect and only returned after
  the AP restart. So the socket is not always the wedged component.

**No root cause established.** What is in the tree is a recovery mechanism, not
a fix, and it should not be described as one. The asymmetry it closes is real
regardless: the sender has had a zombie detector since the ENOMEM stall, and
the receiver had nothing — a wedged link on this side had no path back.

One evidence-based improvement did land in `link_init()`: it now waits for the
AP netif to actually have an address before binding, instead of binding
straight after `config()`. That is a genuine race whatever else is going on.

**Do not read a run of clean reboots as proof.** At a ~1-in-6 rate, eight clean
runs in a row is entirely consistent with the fault still being there — and
eight clean runs is exactly what the current build produced. Cumulatively since
the `link_init()` change: **2 stalls in 18 reboots**, both recovered
automatically.

`link_watchdog()` fires only when **`_ap.status("stations")` reports a station
actually associated** — an idle desk with no stick is silent for a correct
reason, and rebuilding the socket because of that would be its own bug. Then:

| Strike | After | Action |
|---|---|---|
| 1–2 | 5 s each | rebuild the UDP socket |
| 3 | 5 s | restart the AP (`link_init()`) |
| 4+ | — | stop escalating, stop logging |

5 s, not 12 s: normal traffic is 25 packets/s, so five seconds of total silence
from an *associated* station is already deeply abnormal, and the wait is dead
screen time. Escalation at strike 3 rather than 4 because rebuilding the socket
has already been observed to fail twice in a row — there is no point spending a
third window on a remedy that is not working. Worst case is now ~15 s of blank
display instead of the 24–36 s measured at the old settings, or forever with no
watchdog at all.

Any received packet resets the strike count. Counters are on the developer
dashboard as `link recovery`, because a silent recovery is as hard to diagnose
as a silent failure.

**Known benign false positive:** `stick.sh run <probe>` leaves the stick at the
REPL with its STA *still associated*, so the Tab5 correctly sees a station and
no traffic and starts rebuilding. Observed and expected. It is bounded (stops
after strike 4) and clears the moment real traffic returns. If you see
`socket rebuilt` in a log, check whether the stick was simply parked at the
REPL before treating it as a fault.

**Verified on hardware by creating the trigger condition** — a station that
associates and then deliberately sends nothing. All four stages fired in order,
and the link came back at `rate=25/s` afterwards.

That test caught a bug in the escalation that would have been worse than the
problem: `link_init()` bound port 5005 while the previous socket still held it,
failed `EADDRINUSE`, and left the app with **no socket at all**. The recovery
path caused the outage it exists to clear. `link_init()` is now idempotent —
it closes any existing socket first and only publishes the new one once it is
fully bound. **Any future recovery path must be tested by actually triggering
it; this one passed review and code-read fine.**

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

**Looked at and signed off** by Yury on 2026-08-08 — "it's much better". 104 px
was judged big enough; if that ever changes, `VAL_MAX_W` is the binding
constraint (248 px of 254 px used) and the vitals window would have to grow at
SIG's expense before a larger face can be selected.

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

## Roadmap build-out 2026-08-08 (commit `d235c76`)

Roadmap items 1–7 are implemented. See `ROADMAP.md` for per-item status and
`docs/sim/` for what each screen looks like. Three structural things landed
first, because nothing else could work without them:

- **`layout()`** — every derived constant is recomputed on demand instead of
  once at import. Auto-rotate is impossible until this moves: a fixed-at-boot
  geometry is wrong the instant the screen turns. **Nothing outside `layout()`
  may cache geometry.**
- **Touch** — `M5.update()` was never called by this app; nothing polled the
  panel or the buttons. It now runs exactly once per loop pass (twice eats
  button edges). Taps fire on **release**, not touch-down.
- **Screen registry** — a screen is `(enter, draw, tap)` registered by name.
  `go()` clears the whole field cache, because the change-driven painters
  compare against what they last drew and after a screen change that memory
  describes pixels that no longer exist.

Measured on hardware: **512-point FFT = 220 ms** on the P4. That is why it runs
at most every 1500 ms and only while the spectrum screen is showing.

## The simulator — how the touch screens got tested at all

`tools/sim_tab5.py` stubs the M5 API, runs the real `pulselink_tab5.py` source
unmodified up to its main loop, and renders every screen to PNG.

**This is not a nicety.** Every sub-screen is reachable only by tapping the
panel, so working remotely they were unverifiable — the only available evidence
would have been "it compiled". It has already caught a zero-width-field crash
and an axis label running off the bottom of the panel.

Fidelity, and the two halves differ:

- **Measurement is device-accurate** — it reproduces the measured panel metrics
  (see FONT FACTS), so `fit()` picks exactly the face the device picks.
  `DejaVu18`/`DejaVu12` are interpolated and the tool says so on every run.
- **Rendering is approximate** — Pillow glyphs at a matched size. Trust it for
  *does it fit / overlap / throw*, not for *is this the exact pixel*.

**A simulator can only prove what it models.** It stubs the LCD, so a method
present in the stub but absent from this firmware would pass the sim and crash
on a real tap. `SCREEN_SELFTEST` (off by default, in `pulselink_tab5.py`)
exists for exactly that: flip it on and every screen is painted once on the
real panel at boot, with the result printed. It has been run and all six pass.

Two of the bugs the simulator "found" were its own — a phase discontinuity in
the FFT window, and one phase counter shared across sticks which halved each
stick's effective sample rate. Both made the analyzer report 146 BPM for a
72 BPM signal, and both looked exactly like an app bug. The FFT self-test on
pure tones is what separated them: it proved the transform before anything
trusted what it said. **Verify the stimulus before believing a measurement.**

## Test tooling

- `tools/malformed_probe.py` — **run 2026-08-08, PASSED.** Runs on the stick
  (`stick.sh run tools/malformed_probe.py`), joins the AP and sends short /
  long / bad-magic / wrong-version / valid packets over the real link.
  Result: `bad` went **0 → 20** across 5 rounds, exactly the predicted 4
  rejects per round, and `LINK: stick aabbcc joined` confirms the valid packet
  in each round *was* accepted and attributed to the probe's fake device id.
  No crash, no mis-parse. Redeploy `pulselink.py` to the stick afterwards — the
  probe leaves it sitting at the REPL.
- `tools/resync_probe.py` — **written and run 2026-08-08.** Runs on the stick,
  synthesises a valid v3 stream and drives state 7 / the resync flag on and off
  so the Tab5's banner-up and banner-down transitions run over the real link.
  Banner-down is the interesting one: it wipes the graph frame and takes the
  `THR` / `LED BEAT` annotations with it, which is the repaint path the
  change-driven rewrite touched. No crash, no leftover banner pixels.
  **This proves the receiver, not the button** — BtnA still needs a human.
- `tools/multi_sensor_probe.py` — **written and run 2026-08-08, PASSED.**
  There is only one physical stick here, so roadmap 4 would otherwise have
  shipped having never seen a second device id. The id is just three bytes in
  the packet, so one stick can present as several senders over the real radio.
  Results: **`linked=3` sustained at `rate=66/s` with `bad=0`**, and the
  12-distinct-id phase evicted stale entries instead of growing the roster
  (`LINK: evicting d5513c (stale 25165ms) for b20014`). Free heap dipped ~30KB
  for the extra histories and recovered.
  **This proves the RECEIVER handles multiple ids. It does not prove two
  physical sticks share the air — only two radios can prove that.**
- `tools/sim_tab5.py` — host-side simulator and screen renderer. See above.
- `tools/timing_matrix.py` — **written, never run.** Drives `timeit.py`'s
  primitives n times per scenario and aggregates min/median/max, which is what
  the "run n>=10 for a real verdict" note above actually needs. Roughly 25 min
  per arm at n=10; answering the 1s-vs-3s retry-ceiling question means running
  both arms (flip `LINK_RETRY_MAX_MS` in `pulselink.py` between them).
- `tools/timeit.py` — dual-serial timing harness (see above). It is in the repo
  now; the older note calling it `scratchpad/timeit.py` was stale.

## Reading the stat line

```
up=30s rx=690 bad=0 rate=26/s linked=1 bpm=127 state=4 \
    batt=100%(8392mV) raw=0%(4362mV) free=23690672
```

- `up=` **resetting is the reboot signal.** The reset itself scrolls past
  unseen in a passive log, and an `rx` counter that restarts is easy to
  misread as a link fault. One reboot was chased this session before it turned
  out to be Yury's own power cycle — `up=` makes that a one-glance answer.
- `raw=` is the unfiltered battery gauge and is *expected* to flap; `batt=` is
  the filtered value and should not.
- `free=` is `gc.mem_free()`; ~23.7 MB steady on the P4. A downward trend
  across minutes would be a leak.

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
