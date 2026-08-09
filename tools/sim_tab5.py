#!/usr/bin/python3
"""Host-side simulator for pulselink_tab5.py.

WHY THIS EXISTS. The Tab5 app can only be reached over serial, and half of it
is now behind a touchscreen. Nobody working remotely can tap a sub-screen, so
the developer / power / spectrum / sensors screens were unreachable and
untestable - the only evidence they worked would have been "it compiled".

This stubs the M5 API, runs the real app source unmodified up to (not
including) its main loop, then drives every screen and renders what the panel
would show.

TWO FIDELITY RULES, and the difference between them matters:

  * MEASUREMENT is device-accurate. textWidth()/fontHeight() reproduce the
    metrics measured on the real panel (DejaVu72 is 52px tall and "8888" is
    124px wide, and so on), so fit() selects exactly the face the device
    selects and every layout decision here is the real one.

  * RENDERING is an approximation. Glyphs are drawn with Pillow and DejaVuSans
    at a size chosen to match the metric model. Shapes and positions are
    right; letterforms and antialiasing are not the panel's.

So: trust this for "does it fit, does it overlap, does it throw". Do not trust
it for "is this the exact pixel the device lights". The device still prints its
own measured geometry at boot, and that remains the authority.

  ./tools/sim_tab5.py [outdir]
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "pulselink_tab5.py")

# ---------------------------------------------------------------- metrics
# Measured on the real Tab5 panel. The font NAMES ARE NOT PIXEL HEIGHTS.
#   face          height   "888"   "8888"
#   DejaVu72        52       93      124
#   DejaVu56        49       84      112
#   DejaVu40        44       78      104
#   DejaVu24        27       45       60
#   DejaVu9         15       24       32
# digit width = "8888"/4. DejaVu18 and DejaVu12 were never measured on the
# device; they are interpolated and flagged as such.
FACE_METRICS = {
    "DejaVu72": (52, 31), "DejaVu56": (49, 28), "DejaVu40": (44, 26),
    "DejaVu24": (27, 15), "DejaVu18": (21, 11), "DejaVu12": (17, 9),
    "DejaVu9": (15, 8),
    "Montserrat48": (52, 31), "Montserrat24": (27, 15), "ASCII7": (8, 5),
}
INTERPOLATED = ("DejaVu18", "DejaVu12")

_NARROW = "IJ1lij.,:;'|![]()"
_WIDE = "MW@%"

def _char_factor(c):
    if c in _NARROW:
        return 0.5
    if c == " ":
        return 0.5
    if c in _WIDE:
        return 1.35
    if c.islower():
        return 0.82
    if c.isupper() or c.isdigit():
        return 1.0
    return 0.9

def text_width(s, digit_w):
    return int(round(sum(_char_factor(c) for c in s) * digit_w))


class Font:
    def __init__(self, name):
        self.name = name
        self.h, self.dw = FACE_METRICS[name]

    def __repr__(self):
        return "<%s>" % self.name


class Fonts:
    pass


# ---------------------------------------------------------------- fake LCD
from PIL import Image, ImageDraw, ImageFont           # noqa: E402

FONT_PATHS = (
    "/Users/mininarwhal/.gemini/antigravity-ide/scratch/waveshare_repo/"
    "AMOLED-Products/ESP32-S3-Touch-AMOLED-1.8/examples/Arduino-v3.1.0/"
    "libraries/lvgl/scripts/built_in_font/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)

_ttf_cache = {}

def _ttf(px):
    if px in _ttf_cache:
        return _ttf_cache[px]
    f = None
    for p in FONT_PATHS:
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, px)
                break
            except Exception:
                pass
    if f is None:
        f = ImageFont.load_default()
    _ttf_cache[px] = f
    return f


def rgb(c):
    return ((c >> 16) & 255, (c >> 8) & 255, c & 255)


class FakeLcd:
    def __init__(self, w=1280, h=720):
        self.FONTS = Fonts()
        for n in FACE_METRICS:
            setattr(self.FONTS, n, Font(n))
        self._rot = 1
        self._nat = (w, h)
        self.oob = []                 # out-of-bounds draw calls
        self._new(w, h)
        self.face = self.FONTS.DejaVu24
        self.size = 1
        self.fg = (255, 255, 255)
        self.bg = (0, 0, 0)
        self.cx = self.cy = 0

    # -- geometry ---------------------------------------------------------
    def _new(self, w, h):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w, h), (0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def width(self):
        return self.w

    def height(self):
        return self.h

    def getRotation(self):
        return self._rot

    def setRotation(self, r):
        self._rot = r
        nw, nh = self._nat
        if r % 2 == 0:                      # portrait
            self._new(min(nw, nh), max(nw, nh))
        else:
            self._new(max(nw, nh), min(nw, nh))

    def _chk(self, op, x, y, w=0, h=0):
        if x < 0 or y < 0 or x + w > self.w or y + h > self.h:
            self.oob.append((op, x, y, w, h, self.w, self.h))

    # -- primitives -------------------------------------------------------
    def fillScreen(self, c):
        self.d.rectangle([0, 0, self.w, self.h], fill=rgb(c))

    def fillRect(self, x, y, w, h, c):
        self._chk("fillRect", x, y, w, h)
        if w > 0 and h > 0:
            self.d.rectangle([x, y, x + w - 1, y + h - 1], fill=rgb(c))

    def drawRect(self, x, y, w, h, c):
        self._chk("drawRect", x, y, w, h)
        self.d.rectangle([x, y, x + w - 1, y + h - 1], outline=rgb(c))

    def fillRoundRect(self, x, y, w, h, r, c):
        self._chk("fillRoundRect", x, y, w, h)
        self.d.rounded_rectangle([x, y, x + w - 1, y + h - 1], r, fill=rgb(c))

    def drawRoundRect(self, x, y, w, h, r, c):
        self._chk("drawRoundRect", x, y, w, h)
        self.d.rounded_rectangle([x, y, x + w - 1, y + h - 1], r,
                                 outline=rgb(c))

    def drawLine(self, x0, y0, x1, y1, c):
        self.d.line([x0, y0, x1, y1], fill=rgb(c))

    def drawPixel(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.img.putpixel((int(x), int(y)), rgb(c))

    def fillCircle(self, x, y, r, c):
        self._chk("fillCircle", x - r, y - r, 2 * r, 2 * r)
        self.d.ellipse([x - r, y - r, x + r, y + r], fill=rgb(c))

    def fillTriangle(self, x0, y0, x1, y1, x2, y2, c):
        self.d.polygon([(x0, y0), (x1, y1), (x2, y2)], fill=rgb(c))

    def fillArc(self, *a):
        pass

    def drawArc(self, *a):
        pass

    # -- text -------------------------------------------------------------
    def setFont(self, f):
        self.face = f

    def setTextSize(self, n):
        self.size = int(n)

    def setTextColor(self, fg, bg=None):
        self.fg = rgb(fg)
        if bg is not None:
            self.bg = rgb(bg)

    def setCursor(self, x, y):
        self.cx, self.cy = int(x), int(y)

    def fontHeight(self):
        return self.face.h * self.size

    def textWidth(self, s):
        return text_width(s, self.face.dw) * self.size

    def print(self, s):
        s = str(s)
        w = self.textWidth(s)
        h = self.fontHeight()
        self._chk("print:%r" % s[:18], self.cx, self.cy, w, h)
        # lcd.print() paints an OPAQUE background box - reproduce that, it is
        # what makes flicker-free in-place repainting work on the device.
        self.d.rectangle([self.cx, self.cy, self.cx + w - 1, self.cy + h - 1],
                         fill=self.bg)
        px = max(6, int(h * 0.76))
        self.d.text((self.cx, self.cy + int(h * 0.10)), s,
                    font=_ttf(px), fill=self.fg)
        self.cx += w


# ---------------------------------------------------------------- fake M5
class FakeTouch:
    def __init__(self):
        self.queue = []
        self.cur = None

    def tap(self, x, y):
        """Queue a press then a release, which is what poll_tap expects."""
        self.queue.append((x, y))
        self.queue.append(None)

    def _step(self):
        if self.queue:
            self.cur = self.queue.pop(0)
        else:
            self.cur = None

    def getCount(self):
        return 1 if self.cur else 0

    def getX(self):
        return self.cur[0] if self.cur else -1

    def getY(self):
        return self.cur[1] if self.cur else -1


class FakeImu:
    accel = (1.0, 0.0, 0.0)

    @classmethod
    def getAccel(cls):
        return cls.accel

    @classmethod
    def getGyro(cls):
        return (0.0, 0.0, 0.0)


class FakePower:
    _n = [0]

    @classmethod
    def getBatteryLevel(cls):
        cls._n[0] += 1
        return 100 if (cls._n[0] // 5) % 2 == 0 else 0   # the real gauge flaps

    @classmethod
    def getBatteryVoltage(cls):
        return 8393 if (cls._n[0] // 5) % 2 == 0 else 4362

    @classmethod
    def getBatteryCurrent(cls):
        return -420

    @classmethod
    def isCharging(cls):
        return False

    @classmethod
    def getVBUSVoltage(cls):
        return 5100


class FakeBtn:
    @staticmethod
    def wasClicked():
        return False

    @staticmethod
    def wasPressed():
        return False


def build_m5(lcd, touch):
    m5 = types.ModuleType("M5")
    m5.Lcd = lcd
    m5.Display = lcd
    m5.Touch = touch
    m5.Imu = FakeImu
    m5.Power = FakePower
    m5.BtnA = m5.BtnB = m5.BtnC = m5.BtnPWR = FakeBtn
    m5.begin = lambda *a, **k: None
    m5.update = touch._step
    return m5


# ------------------------------------------------------------- fake stdlib
CLOCK = [1000]

def build_time():
    t = types.ModuleType("time")
    t.ticks_ms = lambda: CLOCK[0]
    t.ticks_diff = lambda a, b: a - b
    t.ticks_add = lambda a, b: a + b
    t.sleep_ms = lambda ms: None
    t.sleep = lambda s: None
    t.time = lambda: CLOCK[0] / 1000.0
    return t


def build_gc():
    g = types.ModuleType("gc")
    g.mem_free = lambda: 23_700_000
    g.collect = lambda: None
    return g


def build_net():
    n = types.ModuleType("network")
    n.AP_IF = 1
    n.STA_IF = 0

    class WLAN:
        def __init__(self, *a):
            pass

        def active(self, *a):
            return True

        def config(self, **k):
            return None

        def ifconfig(self):
            return ("192.168.4.1", "255.255.255.0", "192.168.4.1", "0.0.0.0")

        def status(self, *a):
            return []
    n.WLAN = WLAN

    s = types.ModuleType("socket")
    s.AF_INET = 2
    s.SOCK_DGRAM = 2

    class Sock:
        def __init__(self, *a):
            pass

        def bind(self, *a):
            pass

        def setblocking(self, *a):
            pass

        def recvfrom(self, n):
            raise OSError(11)          # EAGAIN: nothing queued
    s.socket = Sock
    return n, s


# ---------------------------------------------------------------- loading
def load_app(lcd, touch):
    """Exec the real app source up to, but not including, its main loop."""
    src = open(APP).read()
    marker = "\nwhile True:"
    i = src.index(marker)
    prelude = src[:i]

    net, sock = build_net()
    saved = {}
    for name, mod in (("M5", build_m5(lcd, touch)), ("time", build_time()),
                      ("gc", build_gc()), ("network", net), ("socket", sock)):
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    ns = {"__name__": "pulselink_tab5"}
    try:
        exec(compile(prelude, "pulselink_tab5.py", "exec"), ns)
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    return ns


def feed(ns, n_sticks=2, samples=700):
    """Give the app plausible live data: n sticks, one with a full PPG history."""
    import math as _m
    for k in range(n_sticks):
        dev = bytes((0xAA, 0xBB, 0xC0 + k))
        st = ns["stick_for"](dev)
        st.bpm = 72 + 9 * k
        st.ibi = 833 - 40 * k
        st.quality = 12
        st.state = 6 if k == 0 else 4
        st.smin, st.smax = 300, 800
        st.thresh = 551
        st.amp = 120
        st.rx = 1200 - 300 * k
        st.last = CLOCK[0]
        st.hist = []
        hr_hz = st.bpm / 60.0
        for i in range(samples):
            t = i / 50.0                        # see _phase note in tick_sticks
            v = (_m.sin(2 * _m.pi * hr_hz * t) * 0.75
                 + 0.25 * _m.sin(4 * _m.pi * hr_hz * t)      # dicrotic notch
                 + 0.05 * _m.sin(2 * _m.pi * 0.2 * t))       # baseline wander
            st.hist.append(int(550 + 180 * v))
        st.wave = [(st.hist[-2], True), (st.hist[-1], False)]
        # tick_sticks() must CONTINUE this waveform, not restart it: a phase
        # discontinuity inside the FFT window splatters the spectrum.
        _phase[dev] = samples
    ns["pkt_rate"] = 25
    ns["rx_count"] = 1500
    ns["batt_level"] = 100
    ns["batt_v"] = 8393
    ns["batt_raw"] = (0, 4362)
    ns["t_boot"] = CLOCK[0] - 725_000
    for i in range(40):                       # some power history to graph
        ns["pwr_hist"].append((CLOCK[0] - (40 - i) * 5000,
                               100 - i // 3, 8393 - i * 4, -420))


def fft_selftest(ns):
    """Prove the transform itself before trusting anything it says on screen.

    A spectrum analyzer that is confidently wrong is worse than none: it looks
    authoritative. Feed it pure tones at known frequencies and check the peak
    lands in the right bin.
    """
    import math as _m
    n = ns["N_FFT"]
    sr = ns["SAMPLE_HZ"]
    ok = True
    for hz in (0.8, 1.2, 2.0, 3.0):
        sig = [int(512 + 200 * _m.sin(2 * _m.pi * hz * (i / float(sr))))
               for i in range(n)]
        bins = ns["fft_mag"](sig)
        lo = int(0.5 * n / sr)
        pk, pv = lo, 0.0
        for i in range(lo, len(bins)):
            if bins[i] > pv:
                pv, pk = bins[i], i
        got = pk * float(sr) / n
        err = abs(got - hz)
        tol = float(sr) / n                      # one bin
        flag = "ok" if err <= tol else "WRONG"
        if err > tol:
            ok = False
        print("   %.2f Hz in -> %.2f Hz out (bin %d, +/-%.2f)  %s"
              % (hz, got, pk, tol, flag))
    return ok


# Phase is PER STICK. A single shared counter advanced once per stick per
# tick, so with two sticks each one's samples stepped 2 units of time apart
# while still being read as a 50Hz series - the apparent frequency doubled and
# the spectrum analyzer dutifully reported 146 BPM for a 72 BPM signal. The
# transform was right; the stimulus was wrong.
_phase = {}

def tick_sticks(ns):
    """Keep every stick live and push two fresh PPG samples, as the real link
    does at 25Hz. Without this the app correctly decides nothing is connected."""
    import math as _m
    for st in ns["sticks"]:
        st.last = CLOCK[0]
        hr_hz = (st.bpm or 72) / 60.0
        ph = _phase.get(st.dev, 0)
        for _ in range(2):
            ph += 1
            t = ph / 50.0
            v = (_m.sin(2 * _m.pi * hr_hz * t) * 0.75
                 + 0.25 * _m.sin(4 * _m.pi * hr_hz * t))
            samp = int(550 + 180 * v)
            beat = (ph % int(50 / hr_hz)) == 0
            st.wave.append((samp, beat))
            st.hist.append(samp)
            st.beat = beat
        _phase[st.dev] = ph
        if len(st.hist) > ns["HIST_MAX"]:
            del st.hist[0:len(st.hist) - ns["HIST_MAX"]]


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        HERE, "..", "docs", "sim")
    os.makedirs(outdir, exist_ok=True)

    lcd = FakeLcd()
    touch = FakeTouch()
    print("loading app ...")
    ns = load_app(lcd, touch)
    print("  layout: %dx%d  vitals w=%d  sig w=%d"
          % (ns["W"], ns["H"], ns["VIT_W"], ns["SIG_W"]))
    print("  value face h=%d  '8888'=%dpx  budget=%dpx"
          % (ns["th"](ns["F_VAL"]), ns["tw"]("8888", ns["F_VAL"]),
             ns["VAL_MAX_W"]))
    feed(ns)

    failures = []
    print("fft self-test (pure tones, expect peak within one bin):")
    if not fft_selftest(ns):
        failures.append(("fft", "self-test failed: transform is wrong"))
    order = ("main", "menu", "sensors", "fft", "dev", "power")
    for name in order:
        lcd.oob = []
        try:
            ns["go"](name)
            for step in range(60):            # let gated painters actually run
                # Advance in small steps and keep the sticks fresh. Jumping
                # 2s at a time pushed every stick past STALE_MS, so the first
                # version of this rendered the "waiting for stick" state and
                # called it the dashboard.
                CLOCK[0] += 40
                tick_sticks(ns)
                ns["SCREENS"][name][1](CLOCK[0])
        except Exception as ex:
            import traceback
            failures.append((name, repr(ex)))
            print("  !! %-8s RAISED %r" % (name, ex))
            traceback.print_exc()
            continue
        path = os.path.join(outdir, "tab5-%s.png" % name)
        lcd.img.save(path)
        oob = lcd.oob
        print("  %-8s ok   %-3d hits  %s"
              % (name, len(ns["_hits"]),
                 "CLEAN" if not oob else "%d OUT OF BOUNDS" % len(oob)))
        for o in oob[:6]:
            print("        OOB %s at x=%s y=%s w=%s h=%s (panel %sx%s)" % o)
            failures.append((name, "oob %s" % (o,)))

    # tap routing: every hit region on the main screen must resolve
    ns["go"]("main")
    print("tap routing from main:")
    for hx, hy, hw, hh, action in list(ns["_hits"]):
        target = ns["hit_test"](hx + hw // 2, hy + hh // 2)
        ok = target == action and action in ns["SCREENS"]
        print("   (%4d,%4d) -> %-8s %s" % (hx + hw // 2, hy + hh // 2,
                                           target, "ok" if ok else "MISMATCH"))
        if not ok:
            failures.append(("routing", action))

    # rotation: every orientation must lay out and paint without throwing
    print("rotation:")
    for rot in (1, 3, 0, 2):
        lcd.oob = []
        try:
            ns["apply_rotation"](rot)
            ns["go"]("main")
            CLOCK[0] += 2000
            ns["SCREENS"]["main"][1](CLOCK[0])
            lcd.img.save(os.path.join(outdir, "tab5-rot%d.png" % rot))
            print("   rot %d  %dx%d  %s" % (rot, ns["W"], ns["H"],
                                            "CLEAN" if not lcd.oob
                                            else "%d OOB" % len(lcd.oob)))
            if lcd.oob:
                failures.append(("rot%d" % rot, "%d oob" % len(lcd.oob)))
        except Exception as ex:
            failures.append(("rot%d" % rot, repr(ex)))
            print("   rot %d RAISED %r" % (rot, ex))

    print("\nwrote PNGs to %s" % os.path.abspath(outdir))
    if INTERPOLATED:
        print("NOTE: %s metrics are interpolated, never measured on the panel."
              % ", ".join(INTERPOLATED))
    if failures:
        print("\n%d PROBLEM(S):" % len(failures))
        for f in failures:
            print("  %s: %s" % f)
        sys.exit(1)
    print("\nall screens rendered clean")


if __name__ == "__main__":
    main()
