# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Yury Gitman and World Famous Electronics
#
# multi_sensor_probe.py - exercises the Tab5's multi-sensor path (roadmap 4)
# and the bounded stick roster, over the real UDP link.
#
# Runs ON THE STICK (stick.sh run tools/multi_sensor_probe.py). There is only
# one physical stick here, so the multi-sensor code would otherwise ship having
# never seen a second device id. The device id is just three bytes in the
# packet, so one stick can present as several senders and drive the receiver's
# roster, its per-stick counters and its SENSORS screen with real traffic over
# the real radio.
#
# This does NOT prove two physical sticks share the air correctly - only two
# radios can prove that. It proves the receiver handles multiple ids.
#
# Two phases:
#   1. THREE ids at 25Hz each, different rates, for ~20s. Expect the Tab5 to
#      report linked=3 and the SENSORS screen to show three populated rows.
#   2. TWELVE ids, one packet each. The roster caps at STICK_MAX=8, so expect
#      "evicting ..." lines rather than unbounded growth.

import network, socket, time, math

SSID, PSK = "PulseSensor-Link", "pulse1234"
DST = ("192.168.4.1", 5005)

PHASE1_SECS = 20
SENSORS = ((b"\xa1\x00\x01", 62), (b"\xa1\x00\x02", 78), (b"\xa1\x00\x03", 95))
SMIN, SMAX, THRESH = 300, 800, 550


def pkt(dev, bpm, state, beat, s0, s1, ibi):
    b = bytearray(b"PS")
    b.append(3)
    b.extend(dev)
    b.extend((bpm & 0xFF, 12, state, 1 if beat else 0))
    for v in (s0, s1, SMIN, SMAX, THRESH, 140, ibi):
        b.append((v >> 8) & 0xFF)
        b.append(v & 0xFF)
    return bytes(b)


w = network.WLAN(network.STA_IF)
w.active(True)
if not w.isconnected():
    try:
        w.connect(SSID, PSK)
    except Exception:
        pass
    t = time.ticks_ms()
    while not w.isconnected() and time.ticks_diff(time.ticks_ms(), t) < 20000:
        time.sleep_ms(100)
print("PROBE: assoc=%s %s" % (w.isconnected(), w.ifconfig()[0]))
if not w.isconnected():
    raise SystemExit("PROBE: could not join %s" % SSID)

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

print("PROBE: phase 1 - %d sensors for %ds; expect linked=%d on the Tab5"
      % (len(SENSORS), PHASE1_SECS, len(SENSORS)))
n = PHASE1_SECS * 25
for i in range(n):
    for dev, bpm in SENSORS:
        hz = bpm / 60.0
        t = i / 25.0
        a = int(550 + 180 * math.sin(2 * math.pi * hz * t))
        b2 = int(550 + 180 * math.sin(2 * math.pi * hz * (t + 0.02)))
        beat = (i % max(1, int(25 / hz))) == 0
        s.sendto(pkt(dev, bpm, 6, beat, a, b2, int(60000 / bpm)), DST)
    time.sleep_ms(40)
    if i % 125 == 0:
        print("PROBE: %ds" % (i // 25))

print("PROBE: phase 2 - 12 distinct ids, expect eviction past STICK_MAX")
for k in range(12):
    dev = bytes((0xB2, 0x00, 0x10 + k))
    s.sendto(pkt(dev, 70 + k, 4, False, 600, 610, 850), DST)
    print("PROBE: sent as %s" % dev.hex())
    time.sleep_ms(300)

print("PROBE: done - redeploy pulselink.py to the stick")
