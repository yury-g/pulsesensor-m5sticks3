# PulseSensor on M5StickS3

A heart-rate monitor for the **M5StickS3** (ESP32-S3-PICO-1), written in MicroPython
on the factory UIFlow2 firmware. A [pulsesensor.com](https://pulsesensor.com) analog
PPG sensor feeds G2; the 240x135 screen shows a live waveform, BPM, and a signal
coach with a confidence score.

**`v1-working` is the known-good state.** It is tagged, deployed, and verified on
real hardware. If anything breaks, `git checkout v1-working` and redeploy.

---

## Picking this up in a new chat

Paste this to a fresh Claude Code session:

> Clone `github.com/yury-g/pulsesensor-m5sticks3` (private) into `~/MStackSTICK-S3`,
> check out the `v1-working` tag, and read `README.md` and `ROADMAP.md` before
> doing anything. `pulse_cyd.py` is the app; it deploys to the stick as `main.py`
> via `./stick.sh deploy pulse_cyd.py`. Do not use Arduino/C on this chip.

To get a stick back to this exact known-good state:

```bash
git clone https://github.com/yury-g/pulsesensor-m5sticks3.git ~/MStackSTICK-S3
cd ~/MStackSTICK-S3
git checkout v1-working
./stick.sh deploy pulse_cyd.py     # stick already running UIFlow2
# or, for a blank/bricked stick (needs uiflow_sticks3.bin, see below):
./provision.sh
```

Verify it worked — you want `rearms=0` and **no** `rst:0x8` reboot lines:

```bash
./stick.sh watch 20
```

## Wiring

| PulseSensor | M5StickS3 |
|---|---|
| Signal (purple) | **G2** |
| VCC (red) | **3V3** |
| GND (black) | **GND** |

## Quick start

```bash
./stick.sh status              # what's connected, is the REPL alive
./stick.sh run pulse_cyd.py    # run from RAM in ~1s (does NOT persist)
./stick.sh deploy pulse_cyd.py # make it the boot app (persists)
./stick.sh watch 10            # stream serial output
```

> **`run` is not proof.** `mpremote resume` reuses the REPL namespace, so a script
> can pass under `run` and still crash on a cold boot. Always confirm with
> `deploy` + `watch`.

## Restoring a stick from scratch

`provision.sh` takes a blank or bricked stick to a finished device in one command:
verifies the chip, flashes UIFlow2, waits out the first-boot format, sets the NVS
boot option, deploys the app, and confirms it boots.

```bash
./provision.sh              # full: flash firmware + deploy
./provision.sh --no-flash   # skip the firmware step
```

It needs `uiflow_sticks3.bin`, which is **not committed** (it is M5Stack's 8 MB
binary). Fetch it once:

```bash
curl -s https://m5burner-api.m5stack.com/api/firmware | \
  python3 -c "import json,sys; [print(f['file'],f['name']) for f in json.load(sys.stdin) if 'StickS3' in f.get('name','')]"
# then:
curl -L -o uiflow_sticks3.bin https://m5burner.m5stack.com/firmware/<file-from-above>
```

The stick must be **in download mode**: unplug it, then plug it back in while
holding the side button.

---

## What's in here

| File | |
|---|---|
| **`pulse_cyd.py`** | **The app.** Deployed to the stick as `main.py`. |
| `pulse_mono.py` | Rollback: earlier plain white/monochrome build. |
| `pulse.py` | First simple version, kept for reference. |
| `pulse-mock.html` | Browser mock running the *same* detector against a synthesized PPG. Open it locally to iterate on the UI without hardware. |
| `stick.sh` | Dev loop: run / deploy / watch / status. |
| `provision.sh` | One-command bring-up of a new stick. |
| `ROADMAP.md` | Parked features (IMU motion gating, beat chime) with full hardware findings. |
| `probe_imu.py`, `calib_motion.py`, `imu_check.py` | Hardware investigation tools. |

## Buttons

| Button | |
|---|---|
| **BtnA** (front, blue) | **RESYNC** — "look at THIS waveform, now." Retunes the threshold to the live signal, clears the stale interval gate and amplitude, and opens a 6 s fast-lock window so a clean wave locks in two beats instead of four. The coach flashes `RESYNC` to confirm the press. |
| **BtnB** (side) | Full cold reset of the detector. |

Use RESYNC when the screen shows an obviously good pulse wave but the coach will
not engage. That happens when a bad detection leaves `ibi_ms` large — the detector
gates beats at 3/5 of it, so real beats get discarded — or leaves `amp` stale-low,
so `qualify()` rejects everything. A plain re-arm did not clear either.

## The screen

One colour language — waveform, heart, coach and tile borders always agree:

| | |
|---|---|
| **Blue** | collecting; nothing trustworthy yet |
| **Yellow** | locking on |
| **Green** | full confidence |

Yellow annotations over the graph (`THR`, beat ticks) are labels, not state.

Beat detection is the PulseSensor/CYD algorithm: adaptive threshold midway between
the running peak and trough, rising-edge detection behind a 250 ms refractory plus a
3/5-of-last-IBI gate, plausibility qualification (40-180 BPM, 333-1500 ms IBI,
minimum amplitude), a ±3/−1 confidence counter that must reach 10/12 to lock, and
classic `rate[]` averaging over the last 10 intervals for the displayed BPM.

---

## Hard-won gotchas

These cost real time. Read before debugging.

- **Do NOT use Arduino/C on this chip.** Current cores (`m5stack:esp32@3.2.5`,
  `esp32:esp32@3.3.10`) produce firmware that double-faults the instant any
  interrupt fires on core 1 — even an empty sketch. MicroPython on factory UIFlow2
  is the proven path.
- **Boot hijack.** UIFlow2 runs its launcher instead of `main.py` unless NVS
  `uiflow`/`boot_option` is `0`. Deploying `main.py` alone does nothing.
  `provision.sh` sets it; `"Skip sync"` in the boot log confirms it.
- **The LCD font is PROPORTIONAL, not a 6x8 grid.** At size 1 it is **15 px tall**
  and `"PulseSensor"` is **92 px** wide, so size-4 digits are **60 px tall**. Lay
  out with `lcd.textWidth()` / `lcd.fontHeight()` at runtime and keep a 5 px safe
  edge. Never assume character cells.
- **`lcd.print()` paints an opaque background box.** Anything drawn beforehand in
  the same place is wiped out. Draw icons *after* text.
- **Watchdog crash-loop** (`rst:0x8 TG1WDT_SYS_RST` repeating) means the main loop
  never slept — if a frame costs ≥ `SAMPLE_MS`, an `if wait > 0: sleep` branch never
  runs, the task WDT starves, and the board reboots forever. It *looks* like a
  frozen UI. Always `time.sleep_ms(1)` unconditionally each iteration.
- **esptool:** `~/Library/Arduino15` is a symlink to an external volume that is
  often unmounted. Use `/usr/bin/python3 -m esptool` (v4.11), which takes
  *underscore* syntax (`no_reset`, `watchdog_reset`, `flash_id`, `write_flash`).
- **Serial is twitchy.** Opening the port can reset the stick; it then vanishes and
  re-enumerates in 2–15 s. Poll before assuming it died. Never open the port from
  two processes at once. In zsh, never glob `/dev/cu.usbmodem*` (nomatch aborts the
  script; with `null_glob` it silently lists the *current directory*) — use
  `ls /dev/ | grep '^cu\.usbmodem'`.
- **Rescuing a crash-looping stick:** Ctrl-C over USB CDC *blocks* while the board
  resets. Instead run esptool with a default reset to drop it into download mode,
  then reflash.

## Hardware reference

ESP32-S3-PICO-1 (LGA56) rev v0.2 · 8 MB flash · 8 MB PSRAM (AP_3v3 quad)

| Peripheral | |
|---|---|
| Display | ST7789P3, 135x240, landscape at `setRotation(1)` |
| IMU | BMI270 @ I2C `0x68` (`M5.Imu.getAccel()`, 56 µs/call) |
| Audio | ES8311 codec `0x18` + AW8737 amp + 8 Ω 1 W speaker |
| Power | M5PM1 `0x6e` (`M5.Power.getBatteryLevel()`, `isCharging()`) |
| I2C bus | SDA `G47`, SCL `G48` |

Audio note: `M5.Speaker.setPA(True)` is **required** or `tone()` is silent. The amp
must be off to use the IR receiver.
