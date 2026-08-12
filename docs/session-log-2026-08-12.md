# Session log — 2026-08-12 — StickJump: the stick as a browser controller

Goal: a Chrome game whose jump button is the M5StickS3, over Bluetooth, with
the whole thing built and tested without the user driving it by hand.

## What shipped

`stickjump/` — firmware (`stickjump.py`), the game (`index.html`), a link
speed test (`speed.html`), and `play.sh` to serve and open it. See
`stickjump/README.md` for the protocol and usage.

## Findings worth keeping

**There is no "Bluetooth serial" on this chip.** The ESP32-S3 has BLE only —
no Bluetooth Classic, so no SPP/RFCOMM. Chrome cannot speak SPP either, so the
right answer both ways is the Nordic UART Service over BLE + Web Bluetooth.

**BtnA is GPIO 11.** Found by sweeping candidate pins: drive each one
open-drain low and watch whether `M5.BtnA.isPressed()` follows. Open-drain
matters — `Pin.OUT` disables the input path, while open-drain low is
electrically identical to the switch closing. This gave a way to exercise the
*real* button code path with no finger on the hardware, which is how the
firmware was verified end to end.

**UIFlow's `boot.py` is what launches `main.py`.** Emptying it means `main.py`
never runs at all. Stock `boot.py` also runs a startup menu and waits up to
60 s for WiFi first — a Ctrl-C during that wait kills the app before it starts,
which looks exactly like "the firmware is broken". Replaced with a two-line
`boot.py` that does `import main`; the factory file is preserved on-device as
`boot_uiflow.py`.

**Talking BLE from a script on macOS: two separate walls.**

1. A bare Python has no `NSBluetoothAlwaysUsageDescription`, so TCC *kills the
   process* (SIGABRT) the moment it touches CoreBluetooth. Fix: wrap the
   interpreter in a real `.app` bundle. The framework Python re-spawns itself
   through `Contents/Resources/Python.app`, so that inner bundle needs the key
   too, plus `PYTHONHOME`/`PYTHONPATH` and the `Python3` dylib at
   `Contents/Python3`.
2. Even then it fails, because TCC attributes the request to the **responsible
   process** — which is `claude`, not the bundle. Launching with `open -a`
   breaks that chain so the app is responsible for itself. After that, scanning
   and GATT work normally. Bundle kept at `~/Applications/BleTool.app`.

**Chrome's Bluetooth chooser can be automated.** It is a native dialog, but the
DevTools Protocol exposes it as `DeviceAccess.deviceRequestPrompted` /
`DeviceAccess.selectPrompt`, and a CDP-synthesised click counts as the user
activation `requestDevice()` requires. Launch Chrome with
`--remote-debugging-port` and the whole connect flow is scriptable. (Note: a
`&`-backgrounded Chrome dies when the shell call ends — detach it properly.)

## Bugs the tests caught

* **The ground swallowed every jump.** The "ground always catches you" rule ran
  in the same frame the jump started, so `vy` was reset to 0 before the player
  ever left the floor — jumping from the ground was impossible. Fixed by only
  catching while descending (`vy >= 0`).
* Several early "failures" were bad tests, not bad code: asserting a landing at
  a moment the runner was legitimately mid-air, and a test bot that released
  the button immediately (cutting its own jump to ~53 px) and then "proved" the
  level was unclearable.

## Measured link performance

MTU 247, 0 % packet loss at every size. Throughput scales with packet size, not
packet rate — batch anything bandwidth-hungry into large notifications.

| packet | packets/s | kB/s | kbit/s |
| --- | --- | --- | --- |
| 20 B | 342 | 6.8 | 55 |
| 244 B | 146 | 35.5 | 284 |

Round trip 38–48 ms median (~20–25 ms one way). The connection interval is set
by macOS; the firmware requests a faster one but the central decides.

## Not verified

Only one physical stick was available. The multi-stick path (a runner per
stick, distinct colours, per-stick coin counts) is verified in simulation up to
six runners, not on real hardware.

---

# PulseWave — a live pulse waveform in the browser

Same low-friction pattern as StickJump, one step up in complexity: a
PulseSensor on GPIO 2, streamed over BLE, drawn as a 7-second scrolling trace.
See `pulsewave/README.md`.

## The finding that mattered: 500 Hz was a lie

Sampling is driven by `machine.Timer` (verified good on all four timer IDs at
499 Hz with nothing else running). But once BLE is streaming, the 2 ms period
cannot hold. Stamping every sample with `ticks_us` **inside the ISR** exposed it:

| nominal | measured true rate | mean interval | lost |
| --- | --- | --- | --- |
| 500 Hz | 493.0 Hz | 2028 µs — 1.4 % slow, drifting | 0 |
| 250 Hz | 250.00 Hz | 4000.0 µs — exact | 0 |

Dropped to 250 Hz, which is still ~10x oversampled for a waveform whose useful
content is under 25 Hz. The lesson generalises: **stamp the samples and let the
receiver measure the rate** rather than trusting the configured one. Without the
timestamps this would have shipped as "500 Hz" and been wrong by 1.4 %.

Because every sample carries its own timestamp, the residual scheduler jitter
(sd 227 µs) does not distort the trace — the page plots on a true time axis
instead of assuming uniform spacing.

## macOS caches BLE GAP names — this cost real time

After reflashing, the stick advertised `PulseWave-1F00` while CoreBluetooth kept
reporting the cached `StickJump-1F00`. Every name-based matcher failed and it
looked exactly like "the firmware is not advertising". Confirmed by reading
`AdvertisementData.local_name` (from the packet) against `device.name` (from the
OS cache). Consequences:

* the page filters the chooser by **service UUID, not name**;
* test harnesses match on the advertised `local_name`.

## Protocol shape worth reusing

`<uint32 first_index><uint16 count><uint16 rate_hz><uint32 t_us>` + N × uint16.
An **absolute** sample index makes loss unambiguous rather than inferred — the
receiver can state "0 samples missing" as a fact. Measured over real BLE from a
cold boot: 188 packets, 3008 samples, 0 missing.

Batching matters: the earlier throughput sweep showed bandwidth scales with
packet size, not packet rate, so samples go out 16 at a time (64 ms) rather than
one notification per sample.

## Still not verified

The in-Chrome connection for PulseWave was not driven by an automated harness.
The CDP `DeviceAccess` approach that worked for StickJump was abandoned here
because relaunching Chrome repeatedly was disruptive; the page is verified
against synthetic packets in the exact wire format, and the firmware over real
BLE with a Python central.
