# pulsewave.py — M5StickS3 streams a PulseSensor waveform to Chrome over BLE.
#
# Sampling is driven by a hardware timer, not the main loop, so the sample
# interval does not wobble with whatever else the board is doing. The timer ISR
# only writes into a preallocated ring buffer; batching and BLE happen in the
# main loop.
#
# Every packet carries the absolute index of its first sample, so the browser
# can reconstruct exact timing and see precisely how many samples (if any) were
# ever lost.
#
#   packet = <uint32 first_index><uint16 count><uint16 rate_hz><uint32 t_us>
#            then count x uint16 samples (12-bit ADC counts, little-endian)
#
# t_us is ticks_us of the first sample in the packet. MicroPython ticks wrap at
# 2**30, so a receiver must unwrap it (about every 18 minutes).
#
# Streaming starts by itself the moment a central connects -- nothing to press.
import array
import bluetooth
import struct
import time

import M5
import machine
from machine import ADC, Pin, Timer

_UID = machine.unique_id()
NAME = "PulseWave-%02X%02X" % (_UID[4], _UID[5])

SENSOR_GPIO = 2
# 250 Hz, not the PulseSensor reference 500 Hz, and that is deliberate: at 2 ms
# the soft-IRQ timer cannot keep up once BLE is busy and the measured rate sags
# to ~493 Hz with the period creeping to 2028 us. At 4 ms the mean interval is
# exactly 4000 us with zero dropped samples. A pulse waveform's useful content
# sits below ~25 Hz, so this is still an order of magnitude of oversampling.
RATE = 250
CHUNK = 16                 # samples per BLE packet -> 64 ms of latency
RING = 1024                # must stay a power of two (index masking)
_MASK = RING - 1

_IRQ_CENTRAL_CONNECT = 1
_IRQ_CENTRAL_DISCONNECT = 2
_IRQ_GATTS_WRITE = 3

_F_READ = 0x0002
_F_WRITE_NR = 0x0004
_F_WRITE = 0x0008
_F_NOTIFY = 0x0010

_UART_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_TX = (bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E"), _F_READ | _F_NOTIFY)
_RX = (bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E"), _F_WRITE | _F_WRITE_NR)
_SERVICE = (_UART_UUID, (_TX, _RX))

# --- acquisition ------------------------------------------------------------
_adc = ADC(Pin(SENSOR_GPIO))
_adc.atten(ADC.ATTN_11DB)          # full 0-3.3 V swing
_buf = array.array("H", bytearray(2 * RING))
_ts = array.array("I", bytearray(4 * RING))     # ticks_us per sample
_w = 0                             # total samples ever written (monotonic)


def _tick(_t):
    # ISR: no allocation. Stamping every sample costs one extra store and lets
    # the receiver measure the true rate and jitter instead of trusting RATE.
    global _w
    i = _w & _MASK
    _buf[i] = _adc.read_u16() >> 4              # 16-bit reading -> 12-bit counts
    _ts[i] = time.ticks_us()
    _w += 1


def _payload(flags=False, name=None, services=None):
    p = bytearray()

    def add(t, v):
        p.extend(struct.pack("BB", len(v) + 1, t) + v)

    if flags:
        add(0x01, struct.pack("B", 0x06))
    if name:
        add(0x09, name)
    for u in services or ():
        b = bytes(u)
        # A 128-bit UUID needs 18 bytes; with the name that would overflow the
        # 31-byte advertisement, so it rides in the scan response.
        add(0x07 if len(b) == 16 else 0x03, b)
    return p


class WaveLink:
    def __init__(self):
        self._conns = set()
        self.streaming = False
        self.dropped = 0
        self.sent = 0
        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.config(gap_name=NAME)
        try:
            self.ble.config(mtu=247)
        except Exception:
            pass
        ((self._tx, self._rx),) = self.ble.gatts_register_services((_SERVICE,))
        self.ble.irq(self._irq)
        self._advertise()

    def _advertise(self):
        self.ble.gap_advertise(
            100_000,
            adv_data=_payload(flags=True, name=NAME),
            resp_data=_payload(services=[_UART_UUID]),
        )

    def _irq(self, event, data):
        if event == _IRQ_CENTRAL_CONNECT:
            conn, _, _ = data
            self._conns.add(conn)
            self.streaming = True          # low friction: no start button
            print("BLE: connected", conn, "- streaming")
        elif event == _IRQ_CENTRAL_DISCONNECT:
            conn, _, _ = data
            self._conns.discard(conn)
            if not self._conns:
                self.streaming = False
            print("BLE: disconnected", conn)
            self._advertise()
        elif event == _IRQ_GATTS_WRITE:
            conn, handle = data
            if handle != self._rx:
                return
            cmd = self.ble.gatts_read(self._rx)[:1]
            if cmd == b"S":
                self.streaming = True
            elif cmd == b"X":
                self.streaming = False

    @property
    def connected(self):
        return bool(self._conns)

    def notify(self, payload):
        for c in tuple(self._conns):
            try:
                self.ble.gatts_notify(c, self._tx, payload)
            except OSError:
                return False
        return True


def main():
    global _t_first
    M5.begin()
    lcd = M5.Lcd
    lcd.setRotation(1)
    W, H = lcd.width(), lcd.height()

    link = WaveLink()

    _t_first = time.ticks_us()
    Timer(0).init(period=1000 // RATE, mode=Timer.PERIODIC, callback=_tick)
    print("PulseWave up:", NAME, "- sampling GPIO", SENSOR_GPIO, "at", RATE, "Hz")

    r = 0                       # next sample index to transmit
    shown = None
    last_ui = time.ticks_ms()
    pkt = bytearray(12 + 2 * CHUNK)

    while True:
        w = _w
        if link.streaming and (w - r) >= CHUNK:
            # If the link stalled long enough for the ring to lap us, skip to
            # the newest complete window and count exactly what was lost.
            if (w - r) > RING:
                lost = (w - r) - RING
                link.dropped += lost
                r = w - RING
                print("PulseWave: dropped", lost, "samples")
            struct.pack_into("<IHHI", pkt, 0, r, CHUNK, RATE, _ts[r & _MASK])
            o = 12
            for i in range(CHUNK):
                v = _buf[(r + i) & _MASK]
                pkt[o] = v & 0xFF
                pkt[o + 1] = v >> 8
                o += 2
            if link.notify(pkt):
                link.sent += 1
                r += CHUNK
        elif not link.streaming:
            r = _w              # idle: stay at the live edge, never backlog

        now = time.ticks_ms()
        if time.ticks_diff(now, last_ui) > 250:
            last_ui = now
            state = (link.connected, _w // RATE)
            if state != shown:
                shown = state
                lcd.fillScreen(0x000000)
                lcd.setTextColor(0xFFFFFF, 0x000000)
                # The UIFlow font is proportional -- always measure it.
                lcd.setTextSize(2)
                if lcd.textWidth(NAME) > W - 10:
                    lcd.setTextSize(1)
                lcd.setCursor((W - lcd.textWidth(NAME)) // 2, 8)
                lcd.print(NAME)
                lcd.setTextSize(3)
                msg, col = ("STREAM", 0x00FF66) if state[0] else ("WAITING", 0xFFAA00)
                lcd.setTextColor(col, 0x000000)
                lcd.setCursor((W - lcd.textWidth(msg)) // 2, 48)
                lcd.print(msg)
                lcd.setTextSize(2)
                lcd.setTextColor(0x8899AA, 0x000000)
                sub = "%d Hz  GPIO%d" % (RATE, SENSOR_GPIO)
                lcd.setCursor((W - lcd.textWidth(sub)) // 2, 92)
                lcd.print(sub)

        # Unconditional yield: a frame that never sleeps starves the task
        # watchdog and the board reboot-loops.
        time.sleep_ms(2)


main()
