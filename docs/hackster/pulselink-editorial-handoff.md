# PulseLink — editorial handoff (PARTIAL)

**Status: this is a stub, not the requested full handoff.** It was written at the
very end of a session with almost no context budget left, immediately after a
"restore last known working state and stop" instruction. It records the facts
needed to start the article safely and flags two premises in the brief that do
not match the code. Sections 1–17 of the requested outline are **not** written.

---

## STOP — two corrections to the project brief before anything is published

**1. It is not ESP-NOW.** The brief says the StickS3 "sends live data through
ESP-NOW to an M5Stack Tab5". That is not what the hardware does. The Tab5 is an
**ESP32-P4, which has no radio of its own**; Wi-Fi comes from a companion
**ESP32-C6 over SDIO (ESP-Hosted)**, and that firmware build does not expose
ESP-NOW at all — `import espnow` fails with `no module named '_espnow'`
(verified on-device). The link that was built is **UDP over a private SoftAP**
the Tab5 hosts (`PulseSensor-Link`, gateway `192.168.4.1`, port `5005`), which
the stick joins automatically. It is still phone-free, router-free and
cloud-free, and still zero-touch — but any sentence naming ESP-NOW is factually
wrong. ESP-NOW *is* available and working on the StickS3 side alone.

**2. The Tab5 link is not currently working.** The brief describes a completed
project. As of this session's end the Tab5 app reports `rx=0 linked=0`
continuously. It did work earlier (`LINK: stick d5513c joined`, and a raw socket
test on the Tab5 received real packets: `RX 22 ('192.168.4.2', 58457) b'PS...'`),
and it broke when the packet format moved from v2 (22 bytes) to v3 (24 bytes,
2 batched samples + re-arm flag). The regression is un-diagnosed.

---

## Verified facts (safe to use)

| Fact | Evidence |
|---|---|
| StickS3 = ESP32-S3-PICO-1 rev v1.3, 8 MB flash, 8 MB PSRAM | esptool chip probe |
| Tab5 = ESP32-P4 rev v1.3, 16 MB flash, 1280×720 display | esptool + `ui_home: Create UI on display 1280x720` |
| Both run MicroPython on factory UIFlow2 (stick v2.4.9, Tab5 v2.5.0) | boot banners |
| PulseSensor analog signal → **G2**, VCC → 3V3, GND → GND | `SENSOR_PIN = 2`, `pulse_cyd.py` CONFIG |
| Sampling 50 Hz, ADC 12-bit right-shifted to 10-bit | `SAMPLE_MS = 20`; `adc.read() >> 2` |
| Beat detection = PulseSensor/CYD algorithm | `detect()` in `pulse_cyd.py` |
| Lock requires quality ≥ 10 of 12; ±3/−1 per beat | `Q_LOCK`, `Q_UP`, `Q_DOWN` |
| BPM smoothed over last 10 intervals | `smoothed_bpm()`, `BPM_AVERAGE_N = 10` |
| Blue button = **BtnA** = RESYNC (not a plain reset) | `resync()` in `pulse_cyd.py` |
| Stick display 240×135, `setRotation(3)` | SETUP block |
| Repo: github.com/yury-g/pulsesensor-m5sticks3 (**private**) | pushed this session |
| Tags: `v1-working`, `v1.1-resync`; branch `tab5-remote-display` | git |

**No license file exists in the repo yet** — required before publishing.

## Unknown / not verified — do not invent

Wrist mounting method; power arrangement (battery vs USB); measured latency,
packet loss, or battery life; Tab5 refresh rate; every human/lifestyle answer
(origin story, motivation, how it feels, audience) — the interview never
happened.

## Not done

Sections 1–17 of the requested handoff; the interview; all 14 screen renders and
`screen-renders/README.md`. **A faithful renderer already exists** and is the
shortest path: `pulse-mock.html` reproduces the stick UI with the real detector
and measured font metrics — it is the basis for the StickS3 renders. No
equivalent exists for the Tab5.

## Shortest next step

1. Diagnose the v3 packet regression (print `len(pkt)` once on the stick).
2. Add a LICENSE file.
3. Run the interview — the human story is the entire missing half.
