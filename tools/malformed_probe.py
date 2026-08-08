# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Yury Gitman and World Famous Electronics
#
# malformed_probe.py - end-to-end validation probe for the Tab5's parse().
#
# Runs ON THE STICK (stick.sh run tools/malformed_probe.py). Joins the Tab5's
# SoftAP and sends deliberately malformed packets over the real UDP link, so
# the receiver's real deployed parse() is exercised over the real transport --
# not a copy of it in a host-side unit test.
#
# Expected on the Tab5's serial: bad= rises by 4 per round, rx= by 1 per round.

import network, socket, time

SSID, PSK = "PulseSensor-Link", "pulse1234"
DST = ("192.168.4.1", 5005)
ROUNDS = 5

# A well-formed v3 packet: magic(2) ver(1) dev(3) bpm quality state flags
# s0(2) s1(2) smin(2) smax(2) thresh(2) amp(2) ibi(2) == 24 bytes exactly.
VALID = (b"PS" + bytes((3, 0xAA, 0xBB, 0xCC, 72, 12, 6, 1))
         + bytes((2, 10, 2, 20, 1, 0, 3, 0, 2, 38, 0, 100, 2, 30)))
assert len(VALID) == 24, len(VALID)

CASES = (
    ("short  (23 bytes)", VALID[:23]),
    ("long   (25 bytes)", VALID + b"\x00"),
    ("bad magic",         b"XX" + VALID[2:]),
    ("wrong version (2)", VALID[:2] + bytes((2,)) + VALID[3:]),
    ("VALID v3",          VALID),
)

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
for r in range(ROUNDS):
    for name, pkt in CASES:
        s.sendto(pkt, DST)
        print("PROBE: round %d sent %-18s len=%d" % (r + 1, name, len(pkt)))
        time.sleep_ms(150)
    time.sleep_ms(400)
print("PROBE: done - expect bad=%d rx=%d on the Tab5" % (ROUNDS * 4, ROUNDS))
