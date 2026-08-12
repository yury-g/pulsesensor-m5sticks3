# StickJump — the M5StickS3 as a browser controller

An M5StickS3 acting as a wireless one-button controller for a Chrome page.
Press **BtnA**, the runner jumps. No app, no driver, no pairing in System
Settings — the page talks to the stick directly.

```bash
./stickjump/play.sh
```

That serves the page and opens Chrome. Click **+ Add a stick**, pick
`StickJump-XXXX` from Chrome's picker, and press the button.

## Why BLE and not "Bluetooth serial"

The ESP32-S3 has **no Bluetooth Classic**, so there is no SPP/RFCOMM serial
port to open — and Chrome could not speak it anyway. The equivalent is the
**Nordic UART Service** over BLE, which is what Chrome's Web Bluetooth talks
to. That is what the firmware advertises.

Web Bluetooth only exists in a **secure context**, so the page must be served
from `http://localhost` (or https). Opening `index.html` as a `file://` URL
silently leaves `navigator.bluetooth` undefined — `play.sh` exists to avoid
exactly that.

## Files

| file | what it is |
| --- | --- |
| `stickjump.py` | firmware — BLE peripheral, button, LCD, speed-test modes |
| `index.html` | the game — connects sticks, one runner each |
| `speed.html` | link speed test — latency and throughput, in the browser |
| `play.sh` | serves the folder and opens Chrome |

Deploy firmware with `./stick.sh deploy stickjump/stickjump.py`.

## Protocol

Nordic UART Service `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`.

Stick → browser (TX, notify):

| bytes | meaning |
| --- | --- |
| `1` | BtnA pressed |
| `0` | BtnA released |
| `P` | ping echo |
| `NNNNNNNN....` | burst packet, 8-digit sequence + padding |

Browser → stick (RX, write without response):

| bytes | meaning |
| --- | --- |
| `T` | fire a synthetic press/release (test without touching the hardware) |
| `P` | ping |
| `S<size>` | start a throughput burst of `<size>`-byte packets (9–244) |
| `X` | stop the burst |

Both button edges are sent, so the page can do variable-height jumps: tap for
a short hop, hold for a full one.

## Measured link performance

Measured against this Mac (CoreBluetooth), MTU negotiated to 247, **0 % packet
loss at every size**:

| packet size | packets/s | kB/s | kbit/s |
| --- | --- | --- | --- |
| 20 B | 342 | 6.8 | 55 |
| 60 B | 244 | 14.7 | 117 |
| 120 B | 213 | 25.6 | 205 |
| 180 B | 153 | 27.6 | 220 |
| 244 B | 146 | 35.5 | 284 |

Round trip (write → notify back) is **38–48 ms median**, i.e. roughly 20–25 ms
one way. The limit is the connection interval macOS grants, not the stick: the
firmware asks for a faster interval on connect, but the central decides. For a
jump button that is about 1.5 frames at 60 fps — comfortably playable.

Throughput scales with packet size rather than packet rate, so anything
bandwidth-hungry (sensor streaming) should batch into large notifications.

## Multiple sticks

The page is built for several sticks at once: click **+ Add a stick** again and
each one gets its own colour and its own runner. Every stick advertises a name
derived from its MAC (`StickJump-1F00`) so they are distinguishable in the
picker and on screen.

This is verified in simulation up to six runners; only one physical stick was
available, so the multi-stick path has not been exercised on real hardware.

## Firmware notes

* **BtnA is GPIO 11** on the StickS3 (found by probing). Driving it open-drain
  low is electrically identical to the switch, which is how the button path is
  tested without a finger.
* UIFlow's stock `boot.py` runs a startup menu and waits up to **60 s for WiFi**
  before `main.py`. It is replaced here with a two-line `boot.py` that imports
  `main` directly. **The factory original is preserved on the device as
  `boot_uiflow.py`** — restore it to get UIFlow's normal behaviour back.
* The burst loop still sleeps every iteration; a frame that never yields
  starves the task watchdog and the board reboot-loops.
