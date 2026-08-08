# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Yury Gitman and World Famous Electronics
#
# resync_probe.py - exercises the Tab5's RESYNC banner and stale-link paths.
#
# Runs ON THE STICK (stick.sh run tools/resync_probe.py). Joins the Tab5's
# SoftAP and synthesises a valid v3 stream with the resync flag and state 7
# driven on and off, so the receiver's banner-up / banner-down transitions run
# over the real link.
#
# Why this exists: pressing BtnA needs a human, but the half that can go wrong
# is the RECEIVER's. The banner draws over the graph, and clearing it wipes the
# graph frame and the THR / LED BEAT annotations with it - the exact repaint
# path that the change-driven rewrite touched. This drives that path on demand.
# The BtnA press itself still needs testing by hand; this only proves the Tab5
# renders what a resync packet asks for.
#
# Watch the Tab5's SCREEN, not its serial: the banner is not logged. Expect,
# twice: waveform running -> yellow RESYNC banner -> banner gone with the grid,
# THR and LED BEAT all restored, and no leftover banner pixels.

import network, socket, time

SSID, PSK = "PulseSensor-Link", "pulse1234"
DST = ("192.168.4.1", 5005)
DEV = (0xAA, 0xBB, 0xCE)       # distinct from malformed_probe's AA:BB:CC
HZ = 25
ROUNDS = 2

LOCKED, RESYNC_STATE = 6, 7
SMIN, SMAX, THRESH = 300, 800, 550


def pkt(state, resync, beat, s0, s1, bpm, ibi):
    flags = (1 if beat else 0) | (2 if resync else 0)
    b = bytearray(b"PS")
    b.append(3)
    b.extend(DEV)
    b.extend((bpm & 0xFF, 12, state, flags))
    for v in (s0, s1, SMIN, SMAX, THRESH, 120, ibi):
        b.append((v >> 8) & 0xFF)
        b.append(v & 0xFF)
    return bytes(b)


def phase(sock, label, secs, state, resync):
    """Stream a live-looking wave for `secs` in the given state."""
    global _t
    print("PROBE: %-22s state=%d resync=%d for %.1fs" %
          (label, state, resync, secs))
    n = int(secs * HZ)
    for i in range(n):
        # crude PPG-ish shape so the trace visibly moves under the banner
        ph = (_t % 25) / 25.0
        lvl = SMIN + int((SMAX - SMIN) * (0.25 + 0.7 * (1.0 - ph) ** 3))
        beat = (_t % 25) == 0
        sock.sendto(pkt(state, resync, beat, lvl, lvl, 72, 833), DST)
        _t += 1
        time.sleep_ms(1000 // HZ)


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

_t = 0
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for r in range(ROUNDS):
    print("PROBE: --- round %d ---" % (r + 1))
    phase(s, "running (locked)", 3.0, LOCKED, 0)
    phase(s, "RESYNC banner UP", 2.0, RESYNC_STATE, 1)
    phase(s, "banner DOWN", 3.0, LOCKED, 0)
print("PROBE: done - Tab5 should be back to a clean running trace.")
print("PROBE: it will go stale (~2.5s) and show WAITING FOR STICK next.")
