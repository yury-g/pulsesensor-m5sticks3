#!/usr/bin/python3
"""Passive dual-serial timing harness for PulseLink.

Resets one or both devices over the wire (Ctrl-C + machine.reset()), then
reads BOTH serial ports passively, timestamping every line against a common
host clock t0 = the first reset issued.

Never uses mpremote after t0 -- attaching would stop the running app.
"""
import serial, time, sys, os

STICK = os.environ.get("STICK_PORT", "/dev/cu.usbmodem31201")
TAB5 = os.environ.get("TAB5_PORT", "/dev/cu.usbmodem31101")


class Dev:
    def __init__(self, name, port):
        self.name, self.port = name, port
        self.ser = None
        self.buf = b""
        self.lines = []          # (t_rel, text)
        self.reopen_at = 0.0

    def open(self):
        try:
            self.ser = serial.Serial(self.port, 115200, timeout=0)
            return True
        except Exception:
            self.ser = None
            return False

    def reset(self):
        """Interrupt whatever is running and hard-reset. Returns host time."""
        self.ser.write(b"\x03\x03")
        time.sleep(0.35)
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        self.ser.write(b"\r\nimport machine\r\n")
        time.sleep(0.15)
        self.ser.write(b"machine.reset()\r\n")
        try:
            self.ser.flush()
        except Exception:
            pass
        return time.time()

    def pump(self, t0):
        """Read whatever is available; survive USB re-enumeration."""
        now = time.time()
        if self.ser is None:
            if now >= self.reopen_at:
                if not self.open():
                    self.reopen_at = now + 0.05
            return
        try:
            n = self.ser.in_waiting
            d = self.ser.read(n if n else 1024)
        except Exception:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
            self.reopen_at = now + 0.05
            return
        if not d:
            return
        self.buf += d
        while b"\n" in self.buf:
            raw, self.buf = self.buf.split(b"\n", 1)
            txt = raw.decode("utf-8", "replace").strip("\r\n\x00 ")
            if txt:
                self.lines.append((now - t0, txt))


def run(scenario, gap, duration):
    stick, tab5 = Dev("STICK", STICK), Dev("TAB5", TAB5)
    for d in (stick, tab5):
        if not d.open():
            print("cannot open %s (%s)" % (d.name, d.port))
            return None

    order = {
        "both":       [(stick, 0.0), (tab5, 0.0)],
        "tab5_first": [(tab5, 0.0), (stick, gap)],
        "stick_first": [(stick, 0.0), (tab5, gap)],
        "stick_only": [(stick, 0.0)],
        "tab5_only":  [(tab5, 0.0)],
    }[scenario]

    # "both" must be as simultaneous as possible: arm both at the REPL first,
    # then fire the two reset lines back to back.
    if scenario == "both":
        for d in (stick, tab5):
            d.ser.write(b"\x03\x03")
        time.sleep(0.35)
        for d in (stick, tab5):
            try:
                d.ser.reset_input_buffer()
            except Exception:
                pass
            d.ser.write(b"\r\nimport machine\r\n")
        time.sleep(0.15)
        t0 = time.time()
        for d in (stick, tab5):
            d.ser.write(b"machine.reset()\r\n")
        pending = []
    else:
        t0 = None
        pending = list(order)

    fired = []
    t_start = time.time()
    while True:
        now = time.time()
        if pending and (t0 is None or now - t0 >= pending[0][1]):
            d, off = pending.pop(0)
            if d.ser is None:
                d.open()
            if d.ser is not None:
                tf = d.reset()
                if t0 is None:
                    t0 = tf
                    t_start = tf
                fired.append((d.name, tf - t0))
        if t0 is not None and now - t0 >= duration:
            break
        if t0 is None and now - t_start > 10:
            break
        stick.pump(t0 if t0 else t_start)
        tab5.pump(t0 if t0 else t_start)
        time.sleep(0.002)

    for d in (stick, tab5):
        if d.ser:
            try:
                d.ser.close()
            except Exception:
                pass
    return t0, fired, stick, tab5


def first(lines, needle):
    for t, s in lines:
        if needle in s:
            return t, s
    return None, None


def stick_boot_origin(lines):
    """Host time at which the stick's MicroPython clock read 0.

    The stick stamps every LINK line with ticks_ms(), so its own clock is
    recoverable even though the S3's ROM banner never reaches the USB CDC and
    early output is lost to re-enumeration.
    """
    import re
    best = None
    for t, s in lines:
        m = re.match(r"^.*?\[\s*(\d+)\] LINK:", s)
        if m:
            o = t - int(m.group(1)) / 1000.0
            if best is None or o < best:
                best = o
    return best


def analyse(scenario, t0, fired, stick, tab5):
    print("=== scenario=%s" % scenario)
    print("resets issued: " + ", ".join("%s@%+.3f" % f for f in fired))
    merged = ([("STICK", t, s) for t, s in stick.lines]
              + [("TAB5", t, s) for t, s in tab5.lines])
    merged.sort(key=lambda x: x[1])
    for who, t, s in merged:
        print("%8.3f %-5s | %s" % (t, who, s))

    t_ap, _ = first(tab5.lines, "LINK: SoftAP")
    t_join, _ = first(tab5.lines, "joined")
    t_up, up_s = first(stick.lines, "LINK: up")
    t_app = stick_boot_origin(stick.lines)
    t_fw, _ = first(tab5.lines, "Skip sync")

    print("--- ANALYSIS (host clock, t0 = first reset issued)")
    def show(k, v):
        print("  %-28s %s" % (k, "%.3f s" % v if v is not None else "NOT SEEN"))
    show("stick app start", t_app)
    show("tab5 firmware ready", t_fw)
    show("tab5 AP active", t_ap)
    show("stick associated", t_up)
    show("tab5 first valid packet", t_join)
    if t_ap is not None and t_up is not None:
        show(">> AP-up -> stick assoc", t_up - t_ap)
    if t_ap is not None and t_join is not None:
        show(">> AP-up -> first packet", t_join - t_ap)
    if t_app is not None and t_up is not None:
        show("   stick assoc, stick clock", t_up - t_app)
    if t_app is not None and t_ap is not None:
        who = "stick" if t_app < t_ap else "tab5 AP"
        print("  %-28s %s first by %.3f s"
              % ("effective app-level order", who, abs(t_ap - t_app)))


def main():
    scenario = sys.argv[1]
    gap = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    duration = float(sys.argv[3]) if len(sys.argv) > 3 else 25.0
    r = run(scenario, gap, duration)
    if r is None:
        sys.exit(1)
    t0, fired, stick, tab5 = r
    analyse(scenario, t0, fired, stick, tab5)


if __name__ == "__main__":
    main()
