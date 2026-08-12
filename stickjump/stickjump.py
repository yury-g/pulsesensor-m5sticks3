# stickjump.py — M5StickS3 as a one-button BLE gamepad for the browser.
#
# Advertises a Nordic UART Service (the "BLE serial" profile Chrome's Web
# Bluetooth can talk to; the ESP32-S3 has no Bluetooth Classic/SPP at all).
# BtnA down -> notify b"1", BtnA up -> notify b"0". That's the whole protocol.
#
# A central may also WRITE to the RX characteristic:
#   b"T"  fire a synthetic press/release through the same path as the button,
#         so the link can be tested without a finger on the hardware
#   b"P"  ping -- notifies b"P" straight back, for round-trip latency
#   b"S"  start a throughput burst (sequence-numbered 20-byte packets)
#   b"X"  stop the burst
import bluetooth
import struct
import time

import M5
import machine
from machine import Pin

# Every stick advertises its own name so several can be told apart in Chrome's
# picker and on the page. The page matches on the "StickJump" prefix.
_UID = machine.unique_id()
NAME = "StickJump-%02X%02X" % (_UID[4], _UID[5])
BTN_GPIO = 11  # BtnA on the StickS3, found by probing (open-drain sim confirms)

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

PKT = 20          # bytes per burst packet: 8-digit sequence + padding
_PAD = b"." * 12


def _payload(flags=False, name=None, services=None):
    """Build an advertising / scan-response payload."""
    p = bytearray()

    def add(t, v):
        p.extend(struct.pack("BB", len(v) + 1, t) + v)

    if flags:
        add(0x01, struct.pack("B", 0x06))  # general discoverable, no BR/EDR
    if name:
        add(0x09, name)
    for u in services or ():
        b = bytes(u)
        # A 128-bit UUID is 18 bytes of payload; with the name it would blow the
        # 31-byte advertising budget, so it rides in the scan response instead.
        add(0x07 if len(b) == 16 else 0x03, b)
    return p


class JumpLink:
    def __init__(self):
        self._conns = set()
        self.presses = 0
        self.burst = False
        self.seq = 0
        self.pkt = PKT
        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.config(gap_name=NAME)
        try:
            self.ble.config(mtu=247)   # ask for a bigger MTU; harmless if refused
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
            print("BLE: connected", conn)
            # Ask for the fastest connection interval the central will grant;
            # macOS usually imposes its own, so this is best-effort only.
            try:
                self.ble.gap_conn_update(conn, 6, 12, 0, 200)
            except Exception as e:
                print("BLE: conn update unavailable:", e)
        elif event == _IRQ_CENTRAL_DISCONNECT:
            conn, _, _ = data
            self._conns.discard(conn)
            self.burst = False
            print("BLE: disconnected", conn)
            self._advertise()  # a dropped link must not end the session
        elif event == _IRQ_GATTS_WRITE:
            conn, handle = data
            if handle != self._rx:
                return
            cmd = self.ble.gatts_read(self._rx)
            head = cmd[:1]
            if head == b"T":
                print("BLE: test press requested")
                self.send(True)
                self.send(False)
            elif head == b"P":
                self._notify(b"P")
            elif head == b"S":
                try:
                    size = int(cmd[1:]) if len(cmd) > 1 else PKT
                except ValueError:
                    size = PKT
                # 3 bytes of the ATT MTU go to the notification header.
                self.pkt = max(9, min(size, 244))
                self.seq = 0
                self.burst = True
                print("BLE: burst start,", self.pkt, "byte packets")
            elif head == b"X":
                self.burst = False
                print("BLE: burst stop at seq", self.seq)

    @property
    def connected(self):
        return bool(self._conns)

    def _notify(self, payload):
        for c in tuple(self._conns):
            try:
                self.ble.gatts_notify(c, self._tx, payload)
            except OSError:
                self._conns.discard(c)

    def send(self, down):
        if down:
            self.presses += 1
        self._notify(b"1" if down else b"0")
        print("JUMP" if down else "release", "->", len(self._conns), "central(s)")

    def pump(self):
        """Push burst packets until the controller's buffer refuses more."""
        if not self.burst or not self._conns:
            return 0
        conn = next(iter(self._conns))
        n = 0
        pad = b"." * (self.pkt - 8)
        while n < 64:
            try:
                self.ble.gatts_notify(conn, self._tx, b"%08d" % self.seq + pad)
            except OSError:
                break          # queue full -- yield and refill next iteration
            self.seq += 1
            n += 1
        return n


def main():
    M5.begin()
    lcd = M5.Lcd
    lcd.setRotation(1)
    link = JumpLink()

    shown = None
    W, H = lcd.width(), lcd.height()

    def draw(state, presses, burst):
        lcd.fillScreen(0x000000)
        lcd.setTextColor(0xFFFFFF, 0x000000)
        # The UIFlow font is proportional -- always measure, never assume cells.
        # Show the full advertised name so this stick is identifiable in Chrome.
        lcd.setTextSize(2)
        if lcd.textWidth(NAME) > W - 10:
            lcd.setTextSize(1)
        lcd.setCursor((W - lcd.textWidth(NAME)) // 2, 8)
        lcd.print(NAME)
        lcd.setTextSize(3)
        if burst:
            msg, col = "SPEED", 0x00CCFF
        elif state:
            msg, col = "READY", 0x00FF66
        else:
            msg, col = "WAITING", 0xFFAA00
        lcd.setTextColor(col, 0x000000)
        lcd.setCursor((W - lcd.textWidth(msg)) // 2, 48)
        lcd.print(msg)
        lcd.setTextSize(2)
        lcd.setTextColor(0x8899AA, 0x000000)
        sub = "press A to jump" if state else "connect in Chrome"
        lcd.setCursor((W - lcd.textWidth(sub)) // 2, 92)
        lcd.print(sub)
        n = "jumps %d" % presses
        lcd.setCursor(W - lcd.textWidth(n) - 6, H - lcd.fontHeight() - 5)
        lcd.print(n)

    print("StickJump up: advertising as", NAME)
    while True:
        if link.burst:
            # Flat out: no LCD work, but still yield so the watchdog is fed.
            link.pump()
            time.sleep_ms(1)
            if shown is not None and shown[2] is False:
                draw(link.connected, link.presses, True)
                shown = (link.connected, link.presses, True)
            continue

        M5.update()
        if M5.BtnA.wasPressed():
            link.send(True)
        if M5.BtnA.wasReleased():
            link.send(False)
        now = (link.connected, link.presses, False)
        if now != shown:
            draw(*now)
            shown = now
        # Unconditional yield: a frame that never sleeps starves the task
        # watchdog and the board reboot-loops.
        time.sleep_ms(5)


main()
