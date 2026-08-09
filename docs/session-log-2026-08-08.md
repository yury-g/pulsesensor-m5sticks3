# Session log — 2026-08-08

Branch `tab5-remote-display`, `d94bfd1` → `b63a622`. Nine commits,
19 files, +2677 / −281.

This is the chronological record: what was done, what was **measured**, and
what was learned. `HANDOFF.md` is the forward-looking state and the traps;
`ROADMAP.md` is per-item status. This file is the narrative, and it keeps the
things that would otherwise be lost — the wrong turns and the reasons.

Hardware throughout: StickS3 `70041dd5513c` on `/dev/cu.usbmodem31201`,
Tab5 `80f1b2d16bf1` on `/dev/cu.usbmodem31101`.

---

## 1. UI rework — `4d664cb`

The inherited task list. Flicker, a split bottom row, bigger BPM/IBI.

**Flicker was not subtle and not one thing.** `draw_header()` blanked the whole
1280×90 bar and redrew it every 500 ms whether or not anything had changed; the
BPM tile inverted a 561×180 panel 120 times a minute; the heart blanked a box
around itself twice per beat; every tile update refilled its whole panel. All
of it was erase-then-redraw. The change-driven primitives to fix it
(`changed()` / `field()` / `_clear_outside()`) already existed in the tree and
had never been wired up.

Now there is **no repaint timer in the main loop at all**. The heart keeps one
fixed shape and only changes colour, so the pulse costs no erase.

**A trap found while rewriting:** the sweep erases full-height columns, so it
wipes the `THR` / `LED BEAT` text as it passes under them. Before, they came
back on the next value change; under change-driven painting they would have
stayed gone until the sweep wrapped. Any region-wiping function now
`forget()`s the fields it destroyed.

**Geometry, from the device's own boot log** — printed at boot precisely
because the app cannot see its own screen:

```
LAYOUT: vitals x=20 w=561 half=280 | sig x=601 w=659
LAYOUT: value face h=104  '8888' w=248  fits 254  (y=584)
LAYOUT: coach face h=52   longest w=498  fits 631 x 70
```

The handoff's suggested `_avail` arithmetic was wrong — it assumed the old
three-panel row and would have wasted 20 px. Two panels take **one** gap.

**Battery.** The gauge reports one cell of the 2S pack every few seconds
(`100%(8393mV)` / `0%(~4360mV)`). A median does **not** fix this: the fault is
a slow square wave, so any window spanning it flips whenever the sample counts
tip. `batt_sample()` takes the max over an 8-sample 1 Hz window. Verified
against the live fault — `raw=` flaps, `batt=` does not.

## 2. Tooling and hardware verification — `e11a438`

`stick.sh deploy` to the Tab5 was documented as a 45-second hang needing
`pkill`. The cause was `mpremote exec machine.reset()` waiting for an exec to
return on a board that immediately spews serial. Resetting over raw pyserial
instead: **~4 s, exits cleanly.**

`tools/malformed_probe.py` — written in a previous session, never run. Run:
`bad` went **0 → 20** across 5 rounds, exactly 4 rejects per round, with the
valid packet in each round accepted (`LINK: stick aabbcc joined`).

`tools/resync_probe.py` — new. Pressing BtnA needs a human, but the half that
can go wrong is the *receiver's*, and that half can now be driven on demand.

**A reboot I chased that was not a fault.** `rx` restarted at 192 and read as a
link failure; it was Yury power-cycling the board. That is why `up=` and
`free=` are now in the stat line — a reset scrolls past unseen in a passive
log, and the uptime counter makes it a one-glance answer. The power cycle was
itself a useful result: the stick re-established the link on its own, the first
*physical* confirmation of the zombie-association fix.

## 3. Roadmap 1–7 — `d235c76`, `22de935`

Three structural pieces had to land first: `layout()` (recomputes every derived
constant on demand — auto-rotate is impossible while geometry is fixed at
boot), touch (`M5.update()` was never called; taps fire on **release**, because
acting on touch-down makes a stray brush fire a navigation you cannot undo),
and the screen registry.

Measured on hardware: **512-point FFT = 220 ms on the P4.** That is why it runs
at most every 1500 ms and only while its screen is showing. 512 and not 256:
256 lands at 11.7 BPM per bin, too coarse to separate a resting heart rate from
its neighbours.

### The simulator was not optional

Every sub-screen is reachable only by tapping the panel. Working remotely they
were unverifiable — the only available evidence would have been "it compiled".
`tools/sim_tab5.py` stubs the M5 API and runs the real source unmodified up to
its main loop. **Measurement is device-accurate** (it reproduces the measured
panel font metrics, so `fit()` picks the faces the device picks);
**rendering is approximate**.

It immediately caught a zero-width-field crash and an axis label running off
the bottom of the panel.

**It also lied to me twice, and both lies were its own.** The spectrum reported
146 BPM for a 72 BPM signal. First a phase discontinuity inside the FFT window;
then, after fixing that, one phase counter shared across two sticks — which
halved each stick's effective sample rate and doubled the apparent frequency.
Both looked exactly like an app bug. What separated them was an FFT self-test
on pure tones, run *before* trusting anything the analyzer said on screen.

> **Verify the stimulus before believing a measurement.**

**A simulator only proves what it models.** It stubs the LCD, so a method
present in the stub and absent from the firmware would pass and crash on a real
tap. `SCREEN_SELFTEST` paints every screen once on the real panel; all six
pass. That run caught something the simulator structurally could not: `main`
registered 4 hit regions on device against 5 in the sim, because the sensors
target was gated on `len(sticks) > 1` and `main_enter()` runs **once** — a
stick arriving later never got one.

## 4. Multiple sensors, for real — `50d6928`, `bb72308`

One physical stick, so roadmap 4 would have shipped never having seen a second
device id. The id is three bytes in the packet, so one stick can present as
several senders over the real radio: **`linked=3` sustained at `rate=66/s`,
`bad=0`**, and a 12-id burst evicted stale entries instead of growing the
roster.

Stated plainly wherever it appears: this proves the **receiver**. It does not
prove two physical sticks share the air.

The roster cap exists because the device id is chosen by the sender and the AP
accepts packets from anyone with the PSK — an id walk would mint `Stick`
objects, each with a 600-sample history, until the heap ran out.

## 5. The link stall — `b9bbeee`, `925c196`

The most useful sequence of the session, and the one still open.

A soak caught the Tab5 at `rx=6, rate=0/s` for 100+ seconds while a freshly
booted stick reported `link=1`. Resetting the stick did not clear it; resetting
the Tab5 did. Seven attempts failed to reproduce it.

So the response was a **recovery mechanism, not a fix** — but the asymmetry it
closed was real: the sender has had a zombie detector since the ENOMEM stall,
and the receiver had nothing.

**Testing it by actually creating the trigger is what mattered.** A station
that associates and then deliberately sends nothing. All four stages fired —
and the escalation had a bug worse than the problem it addressed:
`link_init()` bound port 5005 while the previous socket still held it, failed
`EADDRINUSE`, and left the app with **no socket at all**. The recovery path
causing the outage it exists to clear. It read fine on review; only running it
found this.

> **A recovery path that has never been triggered is not a recovery path.**

**Then the watchdog reproduced the stall, with a timestamp:**

```
[ 8.5] LINK: SoftAP up          →  rx=0 for 30s, station associated
[42.7] LINK: rx stalled ... socket rebuilt (#1, strike 1)
[48.7] rx=121  rate=26/s  linked=1
```

That upgraded it from "seen once" to **~1 in 6 Tab5 reboots**, and it is not a
tooling artefact — it reproduces with nothing attached to the serial port
during boot.

**Still no root cause**, and three findings cut against the obvious story:

- `rx` is often non-zero before it dies (31, 57, 6). The socket works *briefly*
  and then stops — not "bound before the interface was ready".
- Rebuilding the socket clears some stalls and does nothing for others. One run
  took three rebuilds with no effect and only returned after the AP restart.
- 8/8 clean reboots on the final build proves very little at a 1-in-6 rate.

What landed: `link_init()` waits for the AP netif to have an address before
binding (a real race regardless); `RX_STALL_MS` 12 s → 5 s; escalation at
strike 3, because a remedy that has failed twice does not deserve a third
window. **Worst case: permanent → 24–36 s → ~15 s.** Cumulative since the
`link_init()` change: 2 stalls in 18 reboots, both self-recovered.

## 6. Endurance soak — `b63a622`

30 minutes undisturbed on `925c196`:

| Measure | Result |
|---|---|
| Duration | 1798 s, uptime monotonic 90 → 1886 s (no reboot) |
| Packets | 47,075 received, `bad=0` throughout |
| Rate | 25.0/s measured against 25/s expected |
| Faults | zero — no reset, traceback, stall, rebuild or AP restart |
| Free heap | −4,516 bytes drift (−0.019%) vs 12,880-byte GC spread → no leak |

The heap was judged against the **GC oscillation spread**, not by comparing
endpoints; a single low sample at the end of a run reads like a leak and
usually is not.

**What this soak cannot see:** it is steady state with the link already up. The
stall happens at *reboot*. Running it longer would not change that.

---

## Open — needs hands

Nothing here is blocked on anything else.

- Tap each header icon and menu row on the panel.
- Rotate the device physically. `ROT_FROM_GRAVITY` is calibrated from a single
  measured reading and is the one constant to change if it comes up inverted.
- A second physical stick.
- BtnA RESYNC / BtnB on the stick.
- **The ~1-in-6 reboot stall has no root cause.** It self-recovers in ~15 s.
  Do not let the watchdog's success be mistaken for the bug being closed.

## Not done, deliberately

- `docs/hackster/` text pack — dropped on request mid-session.
- `tools/timing_matrix.py` is written but **never run**. It exists to answer
  the standing "run n≥10 for a real verdict" question about the 1 s vs 3 s
  retry ceiling; ~25 min per arm, and both arms are needed.
