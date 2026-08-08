# PulseLink Tab5 — implementation handoff

All work below is **approved** by Yury. Branch `tab5-remote-display`.

## STATUS

- [x] **1. Merge + rename + purge CYD** — commit `9436ac8`, pushed.
      `pulse_cyd.py`→`pulselink.py`, `tab5_pulse.py`→`pulselink_tab5.py`
      (both git renames, history intact). Main's SPDX/MIT header and
      PulseSensorPlayground attribution preserved AND the branch link layer.
      All CYD naming gone from the tree. `stick.sh`, `provision.sh`,
      `ROADMAP.md` deliberately kept. `.gitignore` rules restored so the
      abandoned Arduino build trees are not committed.
- [x] **2. Sender hardening** — code complete, deployed to the stick, boots
      clean. Bounded `_sbuf` (caps at LINK_SAMPLES, no more 50/s growth while
      offline). Bounded backoff 300ms→3s replacing the fixed 3s retry.
      `_link_poll_connect()` drives association from **WLAN status**
      (STAT_CONNECTING=1001 is never restarted; 202/203/204 are terminal and
      reported once) instead of exceptions. Lost-link resets backoff for fast
      reconnect. Confirmed send failure rebuilds the socket, rate-limited.
      Timestamped `[ms] LINK:` diagnostics.
      **NOT YET MEASURED** against the timing baseline — that is the next job.
- [ ] **3. Tab5**: strict validation, waveform FIFO, capped renderer
- [ ] **4. Deploy + verify both devices**
- [ ] **5. Physical cold-power cycles + button tests** (needs Yury)
- [ ] **6. docs/hackster text pack**

Repo will eventually move to the World Famous Electronics org. Only the README
clone command hardcodes `yury-g`.

## Approved plan (Approach 2)

Bounded link state machine + waveform FIFO + capped partial renderer.
Do **not** change protocol v3 (24 bytes, 2 samples/packet, ~25/s) or the
standalone detector. No acknowledgements, no protocol redesign.

## Task list

1. Merge `origin/main`, rename to pulselink, purge CYD naming
2. Bound sender buffer, harden link state machine
3. Tab5 strict validation, waveform FIFO, capped renderer
4. Deploy and verify on both devices
5. Physical cold-power cycles + button tests (needs Yury's hands)
6. `docs/hackster/` text pack (only after hardware passes)

## Audit findings — ALL THREE VERIFIED IN SOURCE

1. **Unbounded `_sbuf`** (`pulse_cyd.py` `link_send`). It does
   `_sbuf.append(sig)`, then returns at the `isconnected()` check **without
   clearing**. Grows 50 entries/sec for as long as the link is down.
2. **No version validation** (`tab5_pulse.py` `parse`). Checks
   `len(d) < PKT_LEN` and magic only; `d[2]` (version) is never read and
   over-long packets are accepted. Must require **exact 24 bytes and
   version == 3**.
3. **Only the last packet renders** (`tab5_pulse.py` main loop). The
   `for p in link_poll()` loop overwrites `s.samples` per packet, then
   `draw_wave(s)` runs once — queued samples are silently dropped.

Also: fixed `LINK_RETRY_MS = 3000` is the main avoidable delay when the stick
starts first. Use immediate connect + short bounded backoff and
**`STAT_CONNECTING`** instead of exceptions for state. Add lost-link detection
and socket recovery after a confirmed send failure. Move AP startup to
immediately after `M5.begin()` (~80 ms, secondary).

## Measured baseline (soft reboots, NOT physical power cycles)

| Scenario | AP active | Stick associated | First valid packet |
|---|---:|---:|---:|
| Both together | 0.747 s | 2.080 s | 2.110 s |
| Tab5 first (stick +3.03 s) | 0.754 s | 5.099 s | 5.130 s |
| Stick first (Tab5 +3.05 s) | 3.800 s | 7.083 s | 7.184 s |

Stick-only restart: Wi-Fi 0.990 s, receiving by 1.686 s.
Tab5-only restart: AP 0.682 s, first packet 2.180 s, but reception **stalled at
23 packets through 10.8 s** and recovered by ~18 s. Not reliable yet.

## Merge reconciliation — READ BEFORE MERGING

`origin/main` is a **published** contest release. Never rewrite or force-push
it. Merge it **into** the branch without rewriting history.

`main` renamed `pulse_cyd.py` → `pulselink.py` **and edited it**. Preserve
those edits when reconciling — do not just take the branch copy:

- SPDX-License-Identifier: MIT header + copyright
- Attribution to PulseSensorPlayground (MIT) + `THIRD_PARTY_NOTICES.md`
- Already de-CYD'd: "the PulseSensor / CYD algorithm" → "the PulseSensor
  beat-detection algorithm"
- Comment no longer references `stick.sh`

The branch copy has everything `main`'s does **not**: the whole link layer,
the `link_init()` fix, RESYNC, coach reordering. **Result = branch behaviour +
main's headers/attribution/naming.**

`main` deleted these; restore only the first two, let the rest stay deleted:

**RESTORE:** `stick.sh`, `provision.sh` — the deploy/provision tooling.
An earlier merge attempt failed exactly here
(`stick.sh deleted in origin/main and modified in HEAD`).

**Let go:** `.claude/agents/stick-uploader.md`, `ROADMAP.md`,
`atom_pipeline.sh`, `balls*.py`, `calib_motion.py`, `deploy_balls.sh`,
`flash.sh`, `hello*.py`, `imu_check.py`, `probe_*.py`, `pulse.py`,
`pulse_mono.py`, `run.sh`, `stick3_pipeline.sh`.
(`ROADMAP.md` held the parked IMU-motion-gating and audio findings — they
survive in git history and in Claude's project memory. If you want them kept,
say so before the merge.)

Rename `tab5_pulse.py` → `pulselink_tab5.py` with `git mv`. Remove
`pulse_cyd.py` and all CYD naming from the final tree — **CYD is a different
board and is not part of this project.** Grep for `cyd`, `CYD`,
`pulse_cyd` across code, comments, docstrings, filenames and docs.
Note `tab5_pulse.py` header art and comments still say "CYD dashboard",
"CYD palette", "CYD semantics", "CYD green".

## Hardware / environment facts

- StickS3 `/dev/cu.usbmodem31201`, chip id `70041dd5513c`, ESP32-S3-PICO-1,
  UIFlow2 **2.4.9**. Sensor on **G2/GPIO2 at 3V3**.
- Tab5 `/dev/cu.usbmodem31101`, ESP32-P4, UIFlow2 **2.5.0**, 1280×720.
- Both MicroPython **1.27.0**.
- Link = **UDP over SoftAP**, `PulseSensor-Link` / `pulse1234`,
  `192.168.4.1:5005`. **NOT ESP-NOW** — the P4 has no radio; its hosted
  ESP32-C6 exposes no `_espnow`.
- Tab5 is running commit `b0f8edd`, **not** current `tab5_pulse.py` — the
  header battery/link-quality indicators were **never deployed or verified**.
- Tab5 `M5.Power.getBatteryLevel()` returns **0** while reading **5482 mV**
  (USB rail) → code shows an "EXT" pill rather than faking a level. Find the
  real Tab5 fuel-gauge API or leave it honest.
- Link bars come from **measured packet rate**, because the AP exposes station
  MACs but **no RSSI**.

## Traps that have already cost real time

- Main loop must `time.sleep_ms(1)` **unconditionally** every iteration, or the
  task watchdog starves and the board reboots forever
  (`rst:0x8 TG1WDT_SYS_RST`). It looks exactly like a frozen UI.
- **No broad `except: pass` on the send path.** It has hidden two real bugs: a
  `NameError` on `ibi`, and `Wifi Internal State Error` aborting `link_init()`
  before the socket was ever created — the link looked connected while sending
  nothing. Report the first failure.
- `./stick.sh run` reuses REPL globals; code can pass there and still
  `NameError` on a cold boot. **Verify with `deploy` + a real reset.**
- The LCD font is **proportional**: at size 1 it is 15 px tall and
  "PulseSensor" is 92 px. Always measure with `textWidth()`/`fontHeight()`.
  On Tab5 use `lcd.FONTS.DejaVu*`; `setTextSize(N)` integer-scales and is
  blocky/blurry.
- `lcd.print()` paints an **opaque background box** — draw icons **after** text.
- Ports swap between the two devices; identify by chip id, use `STICK_PORT=`.
- `mpremote` attaching **stops the running app**. To check a running device,
  read serial **passively** with pyserial.
- Use `/usr/bin/python3` (Homebrew python3 lacks pyserial/mpremote).
  esptool = `/usr/bin/python3 -m esptool` v4.11, **underscore** args
  (`no_reset`, `watchdog_reset`, `write_flash`). The Arduino15 esptool is a
  dead symlink to an unmounted volume.
- UIFlow2 NVS `boot_option` must be `0` or the launcher hijacks `main.py`.
- If a stick crash-loops, Ctrl-C over CDC **blocks**; use esptool with a default
  reset to drop it into download mode, then reflash.

## Test matrix before claiming done

Malformed packets (short, long, bad magic, wrong version). All five restart
orders. Time-to-first-packet vs the baseline above. Several **physical**
power cycles. BtnA RESYNC and BtnB reset. Verify passively.
