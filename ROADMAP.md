# PulseSensor on M5StickS3 — roadmap

## Public roadmap — planned for PulseLink / Tab5

Requested 2026-08-08. Items 1–7 were **built the same day** (commit `d235c76`).

**Status honestly stated:** all seven render correctly in `tools/sim_tab5.py`,
and every screen has been painted once on the real panel without raising (the
`SCREEN_SELFTEST` pass). What has **not** happened is a human tapping the
screen or physically turning the device — see "Needs hands" below.

1. **Auto-rotate.** ✅ Built. Gravity from `M5.Imu.getAccel()` picks the
   rotation; `layout()` recomputes every derived constant and the current
   screen is rebuilt. Needs 8 agreeing samples (~1.6 s) before it turns, and
   ignores the device lying flat. **The gravity→rotation table is calibrated
   from a single measured reading** — if the screen comes up upside down,
   `ROT_FROM_GRAVITY` is the one thing to change.
2. **Wireless icon → developer dashboard.** ✅ Built. Packet rate against
   expected, accepted/rejected counters, per-stick device ids and last-seen
   ages, protocol version, endpoint, uptime, free heap, rotation. RSSI is
   shown as *unavailable, with the reason* — a SoftAP exposes association
   state, not signal strength.
3. **Battery icon → power-management dashboard.** ✅ Built. Charge, pack and
   per-cell voltage, current, USB rail, a 15-minute state-of-charge graph, and
   a runtime estimate **from the measured SOC slope** rather than a datasheet
   capacity nobody has told us. Reads through `batt_sample()`, so it does not
   inherit the gauge's half-scale flapping; the raw value is shown beside the
   filtered one so the filter can be seen working.
4. **Multiple sensors.** ✅ Built **and verified over the real link** with
   `tools/multi_sensor_probe.py`: `linked=3` sustained at `rate=66/s`, `bad=0`,
   three populated rows. The roster is capped at 8 and evicts only stale
   entries — a 12-id burst displaced stale ids instead of growing.
   *Caveat: one physical stick presenting as several device ids proves the
   receiver. It does not prove two radios sharing the air.*
5. **App menu.** ✅ Built. Reaches every screen; the header icons are also
   direct tap targets.
6. **FFT spectrum analyzer.** ✅ Built. 512-point radix-2, Hann-windowed,
   harmonics marked `f`/`2x`/`3x`, x-axis in BPM and the fundamental reported
   in both Hz and BPM. **Measured at 220 ms on the P4**, recomputed at most
   every 1.5 s and only while that screen is showing.
7. **Room to grow.** ✅ The screen registry is this: a screen is three
   functions registered by name, and `layout()` means a new one inherits
   correct geometry in any orientation.
8. **Consider a ground-up rewrite** once the current build has survived a week
   of real user testing. — not started, and the screen framework above is a
   deliberate argument against needing one.

### Needs hands before any of this is "done"

- Tap each header icon and each menu row on the real panel.
- Physically rotate the device through all four orientations.
- Run a second stick to exercise the multi-sensor path for real.

---

# Parked work

Two features were built, verified on hardware, then deliberately removed from the
shipping app (`pulselink.py`) on 2026-07-16. Everything needed to reinstate them is
here. Both were removed for product reasons, not because they failed.

---

## 1. Motion gating with the onboard IMU — PARKED, UNCALIBRATED

### Why it exists
Motion is the dominant noise source for a PPG sensor. The idea is to detect motion
with the accelerometer and refuse to trust beats detected during it.

### The honest limitation — read this before reviving it
The IMU measures motion **of the stick**. The PulseSensor is on a flying lead to G2.
So gating only helps when **the stick and the sensor move together** — both held in
one hand, or both strapped to the same limb.

If the stick sits on a desk while the finger shifts on the sensor, the dominant
artifact is **contact-pressure variation**, which an accelerometer on a different
object physically cannot observe. Gating does nothing in that setup. Decide which
case you are building for before spending effort here.

### Hardware facts (all verified on-device 2026-07-16)
- IMU chip: **BMI270**, 6-axis, I2C address **0x68**, bus SDA `G47` / SCL `G48`
  (shared with ES8311 codec `0x18` and M5PM1 power chip `0x6e`).
- Raw register check: `CHIP_ID` at `0x00` reads **0x24**. `PWR_CTRL` at `0x7D`,
  write `0x04` to enable the accelerometer. Accel data at `0x0C` (6 bytes,
  X/Y/Z LSB-first, signed 16-bit, ±2 g default → divide by 16384.0 for g).
- UIFlow2 exposes **`M5.Imu`** with `getAccel()`, `getGyro()`, `getMag()`,
  `getType()`, `isEnabled()`. `getAccel()` returned `(0.0002, -0.0154, 1.0019)` at
  rest — correct 1 g on Z. **There is no `M5.Imu.update()` method.**
- **Cost: 56 µs per `getAccel()` call**, against a 20,000 µs sample budget at 50 Hz.
  Reading it every cycle is free. (ADC read for comparison: 67 µs.)

### THE OPEN QUESTION — resolve this first
Calibration ran three 10 s phases (still / gentle / hard shake) and **all three
returned an identical ~0.003 g spread**. Two possible causes, never disambiguated
because the session ended:

1. **The stick was never actually moved** during the phases (most likely — the
   prompts appear on the device screen and were missed).
2. **`M5.Imu.getAccel()` returns a cached sample that only refreshes on
   `M5.update()`.** Neither `probe_imu.py` nor `calib_motion.py` called `M5.update()`
   in their read loops. This is a very plausible M5Unified behaviour and would make
   the gate silently dead.

`imu_check.py` was written to settle this: it waits for BtnA, then captures 8 s
without `M5.update()` and 8 s with it, and reports whether the magnitude span
differs. **Run it and shake the stick vigorously while it captures.** If the
`M5.update()` run shows a much larger span, that call is required and belongs in
`motion_update()`.

Note: accel *magnitude* is rotation-invariant, so slow rotation registers as nothing.
Only genuine acceleration shows up. That is correct for artifact detection but means
"waving it around slowly" is not a valid test.

### The implementation that was removed
```python
MOTION_GATE   = 0.10   # g of deviation from slow mean = motion (NEVER CALIBRATED)
MOTION_HOLDOFF = 400   # ms after motion stops before beats count again
MOTION_ALPHA  = 8      # slow-mean smoothing divisor
```
- Track `mag = sqrt(ax²+ay²+az²)`; maintain a slow mean via
  `mean += (mag - mean) / MOTION_ALPHA`; motion metric is `abs(mag - mean)`.
- Measured resting noise floor was **0.001–0.003 g**, so 0.10 g is almost certainly
  far too high. Set the gate between the still-phase p99 and the gentle-phase p50
  once real calibration data exists. `calib_motion.py` prints exactly those numbers.
- **Key design decision worth preserving:** beats detected during motion were
  *neither credited nor penalised* — `quality` was left untouched rather than
  decremented. This keeps a good lock alive through a brief bump instead of
  collapsing it and forcing a re-acquire.
- **Non-obvious bug that was found and fixed:** the auto-re-arm must be suppressed
  while moving. A shaken sensor looks exactly like "alive signal without beat
  events", so without `not moving` in the re-arm condition it re-arms in a loop and
  never settles. Any revival must keep this.
- UI was a `MOT###` readout (milli-g) plus a `GATED#` counter in the header, and a
  `MOTION-HOLD STILL` coach state. BtnA toggled gating on/off for A/B testing in a
  single session.

### If you want to go further than gating
Adaptive noise cancellation (normalised LMS) using the three accel axes as a noise
reference, subtracting the motion component from the PPG. This is what commercial
wrist wearables do, and it beats gating when motion is *continuous* rather than
intermittent. At 50 Hz with a 3-axis reference it is computationally fine on a
240 MHz S3 even in MicroPython. Only worth it if the accelerometer genuinely
correlates with the artifact — see the limitation above.

### Files
- `probe_imu.py` — hardware probe: IMU bindings, I2C scan, speaker, timing budget.
- `calib_motion.py` — 3-phase calibration, prints p50/p90/p99/max deviation per phase.
- `imu_check.py` — button-paced test for the `M5.update()` question above.

---

## 2. Beat chime — PARKED (sound not wanted for now)

### Hardware facts (verified)
- **AW8737 power amplifier + 8 Ω 1 W cavity speaker**, driven through an **ES8311**
  codec (I2C `0x18`; I2S MCLK `G18`, DOUT `G14`, BCLK `G17`, LRCK `G15`, DIN `G16`).
- UIFlow2 exposes `M5.Speaker` with `begin()`, `tone()`, `setVolume()`, `playWav()`,
  `playWavFile()`, `playRaw()`, `stop()`, and **`setPA()`**.

### The gotcha that cost time
**`spk.setPA(True)` is REQUIRED or `tone()` is silent.** The original port called
`begin()` + `setVolume()` + `tone()` only, so the chime was almost certainly never
audible on this hardware — it failed silently inside a `try/except`.

Also from the M5 docs: **the amplifier must be OFF to use the IR receiver.** If IR is
ever added, `setPA(False)` first.

### The chime itself
Rising four-note arpeggio, played on each qualified beat:
```python
CHIME = ((262, 58), (392, 66), (523, 82), (659, 118))  # (freq Hz, ms) = C4 G4 C5 E5
```
It was played non-blockingly: `chime_start()` fired note 0 and set a deadline, and
`chime_update(now)` — called every loop iteration, including inside the sub-20 ms
wait — advanced to the next note when the deadline passed. Do not use blocking
`sleep` for this; it wrecks the 50 Hz sample cadence.

A volume level (0–10) was mapped to `setVolume(vol * 25)` and adjusted with
BtnA/BtnB, shown as `VOL#` in the header.

---

## Not yet done
- **Verify the new header on a real screen.** The layout was arithmetically checked
  (every element's right edge < 240 px) but never visually confirmed against
  hardware, because there is no camera access in the dev loop.
- **Confirm a live BPM reading on the redesigned build.** The detector logic is
  byte-for-byte the pre-existing one and read 75–78 BPM on a finger minutes before
  the redesign, but the redesigned build has only been observed in the no-finger
  state (steady ~770 signal, no crossings).
