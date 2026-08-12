# PulseWave — a live pulse waveform in Chrome

A PulseSensor on **GPIO 2** of an M5StickS3, streaming over BLE into a Chrome
page that draws the last **7 seconds** of the waveform. No app, no driver, no
pairing in System Settings.

```bash
./pulsewave/play.sh
```

That serves the page and opens Chrome. Click **Connect sensor**, pick the stick,
and the trace starts — streaming begins by itself on connect, so there is
nothing to arm or start.

## Files

| file | what it is |
| --- | --- |
| `pulsewave.py` | firmware — timer-driven sampling, batched BLE notifications |
| `index.html` | the page — 7 s scrolling trace plus live quality readout |
| `play.sh` | serves the folder and opens Chrome |

Deploy firmware with `./stick.sh deploy pulsewave/pulsewave.py`.

## Wire format

Nordic UART Service `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`, notifications on the
TX characteristic:

```
<uint32 first_index><uint16 count><uint16 rate_hz><uint32 t_us>
then count x uint16 samples   (12-bit ADC counts, little-endian)
```

* `first_index` is an **absolute** sample counter, so a gap is unambiguous
  packet loss rather than something to be inferred.
* `t_us` is `ticks_us` of the packet's first sample, taken **inside the sampling
  ISR**. The receiver therefore measures the true rate and jitter instead of
  trusting the nominal figure. MicroPython ticks wrap at 2^30 (~18 min), so a
  receiver must unwrap — the page does.

Writing `X` to the RX characteristic pauses streaming, `S` resumes it.

## Why 250 Hz and not the PulseSensor-standard 500

Because 500 Hz was measured to be a lie. On-device timestamps showed the 2 ms
soft-IRQ timer could not hold its period once BLE was busy:

| nominal | measured true rate | mean interval | samples lost |
| --- | --- | --- | --- |
| 500 Hz | **493.0 Hz** | 2028 µs (1.4 % slow, drifting) | 0 |
| 250 Hz | **250.00 Hz** | 4000.0 µs (exact) | 0 |

At 4 ms the long-term rate is exact. A pulse waveform's useful content sits
below ~25 Hz, so 250 Hz is still an order of magnitude of oversampling — the
honest lower rate is strictly better data than an aspirational higher one.

Sampling is driven by `machine.Timer`, never the main loop, so BLE work cannot
stretch the sample interval. The ISR does nothing but read the ADC, stamp the
time, and advance a ring-buffer index; batching and BLE happen in the main loop.

## Measured on hardware

12 s capture from a cold boot, over real BLE:

```
packets          : 188  (15.6/s)
samples received : 3008
index span       : 3008 -> missing 0 (0.000%)
TRUE rate        : 250.00 Hz   (from device timestamps)
sample interval  : mean 4000.0 us, sd 226.5 us, min 2791.8, max 5210.8
signal           : span 489 counts, mean 1952, sd 69
12-bit range ok  : True
```

Zero samples lost. The interval spread is real scheduler jitter, but every
sample carries its own timestamp, so the page plots on a true time axis rather
than assuming uniform spacing.

## Rendering

* The ring buffer holds 8.5 s at the advertised rate; the view shows exactly 7 s
  ending at the newest sample.
* Each screen column is drawn as the **min–max extent** of the samples that fall
  in it, so a fast systolic upstroke keeps its full height instead of being
  decimated away.
* The vertical scale auto-fits the visible window and eases toward its target,
  so the trace stays put instead of twitching, and the DC offset of whatever the
  sensor is doing does not matter.
* The readout shows true rate, jitter, dropped samples, packet rate and signal
  amplitude — so bad data looks obviously bad.

## macOS caches BLE device names

If the stick previously ran different firmware, macOS may keep showing the
**old** GAP name (this cost real debugging time: the device advertised
`PulseWave-1F00` while CoreBluetooth still reported `StickJump-1F00`). The page
therefore filters the chooser by **service UUID, not name** — a name filter can
hide a perfectly good sensor. The stale name may still appear in the picker.

## Not verified

The in-Chrome connection has not been exercised end to end by an automated
harness — the page is verified against synthetic packets in the exact wire
format above, and the firmware is verified over real BLE with a Python central.
Beat detection / BPM is deliberately absent; this shows the waveform only.
