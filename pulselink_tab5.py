# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Yury Gitman and World Famous Electronics
#
# pulselink_tab5.py — PulseLink remote display for the M5Stack Tab5 (ESP32-P4).
#
# A remote display for M5StickS3 sensors running PulseLink, scaled to 1280x720:
#
#   +--------------------------------------------------------------+
#   | PulseSensor.com              <3               .ill [=]        |
#   | Tab5 remote display                          2 LINKED         |
#   +--------------------------------------------------------------+
#   |                                              THR 550          |
#   |        live waveform, dotted threshold line                   |
#   |                                              LED BEAT         |
#   +----------------------------+---------------------------------+
#   | BPM       | IBI ms         | SIG                             |
#   |   121     |   550          |   QUALIFIED BEAT                |
#   |           |                |   ||||||||||||                  |
#   +----------------------------+---------------------------------+
#
# Display semantics, kept in step with the StickS3 app:
#   * waveform is YELLOW while acquiring, GREEN once the quality meter locks
#   * dotted threshold line drawn at the sensor's live adaptive threshold
#   * heart pulses red on every qualified beat
#   * change-driven redraw: only what changed is repainted, so no flicker
#
# The detector runs on the stick; this only renders what arrives over UDP.
#
# TWO RULES carried over from the stick, both learned the hard way:
#   1. The font is PROPORTIONAL - always measure with tw()/th().
#   2. lcd.print() paints an OPAQUE box - draw icons LAST.

import M5
import time

# ============================== CONFIG ==============================

# The P4 has NO radio of its own; Wi-Fi comes from an ESP32-C6 over SDIO
# (ESP-Hosted), and that build has no espnow ("no module named '_espnow'").
# So the Tab5 hosts a private SoftAP and sticks join it automatically.
# Zero-touch: SSID hardcoded both ends, no router, no user setup.
LINK_SSID = "PulseSensor-Link"
LINK_PSK = "pulse1234"
LINK_PORT = 5005
LINK_MAGIC = b"PS"
LINK_VER = 3                   # protocol version; anything else is rejected
PKT_LEN = 24                   # v3: 2 waveform samples + flags
STALE_MS = 2500

PKT_HZ_EXPECTED = 25           # link quality is measured against this
APP_NAME = "PulseSensor.com"
APP_SUB = "Tab5 remote display"

# --- palette ---
BG        = 0x000000
GRID      = 0x1E4A32           # graph grid
FRAME     = 0x37D871           # panel borders
TEXT      = 0xFFFFFF
LABEL     = 0x9BB8A6
GREEN     = 0x3BE86B           # locked waveform + meters
YELLOW    = 0xF5D016           # acquiring waveform
CYAN      = 0x5BE7FF
RED       = 0xE8272B           # heart
RED_DIM   = 0x5A0F11
DOT       = 0xB9C6BD           # dotted threshold line
METER_OFF = 0x123018

STATE_NAMES = ("NO SIGNAL", "HOLD STEADY", "SIGNAL SEARCH", "GOOD WAVE",
               "LOCKING", "SIGNAL LOST", "QUALIFIED BEAT", "RESYNC")
LOCKED_STATE = 6

# ============================== SETUP ==============================

M5.begin()

# ============================== LINK ==============================
# The AP comes up FIRST, before any drawing. Measuring the boot showed the
# layout pass below spends ~3.4s loading and measuring the vector fonts, and
# every second of that was a second the stick sat retrying against an AP that
# did not exist yet. Nothing here touches the LCD, so it is safe this early.

_sock = None
_ap = None
rx_bad = 0                     # packets rejected by parse(), for verification

def link_init():
    global _sock, _ap
    try:
        import network, socket
        _ap = network.WLAN(network.AP_IF)
        _ap.active(True)
        try:
            _ap.config(essid=LINK_SSID, password=LINK_PSK, authmode=3)
        except Exception:
            try:
                _ap.config(essid=LINK_SSID, password=LINK_PSK)
            except Exception:
                _ap.config(essid=LINK_SSID)
        _sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _sock.bind(("0.0.0.0", LINK_PORT))
        _sock.setblocking(False)
        try:
            ip = _ap.ifconfig()[0]
        except Exception:
            ip = "?"
        print("LINK: SoftAP '%s' at %s, udp %d" % (LINK_SSID, ip, LINK_PORT))
        return True
    except Exception as ex:
        print("LINK: failed (%s)" % ex)
        return False

def parse(d):
    """Strictly validate before trusting a single byte of the payload.

    EXACT length and an explicit version check - a short packet would index
    past the end, an over-long one is not a v3 packet at all, and a future
    version would be silently misread field-for-field.
    """
    if len(d) != PKT_LEN or d[0:2] != LINK_MAGIC or d[2] != LINK_VER:
        return None
    def u16(i):
        return (d[i] << 8) | d[i + 1]
    return {"dev": bytes(d[3:6]), "bpm": d[6], "quality": d[7], "state": d[8],
            "beat": bool(d[9] & 1), "resync": bool(d[9] & 2),
            "samples": (u16(10), u16(12)),
            "smin": u16(14), "smax": u16(16), "thresh": u16(18),
            "amp": u16(20), "ibi": u16(22)}

def link_poll():
    global rx_bad
    out = []
    if _sock is None:
        return out
    for _ in range(16):
        try:
            data, _addr = _sock.recvfrom(64)
        except Exception:
            break
        if not data:
            break
        p = parse(data)
        if p:
            out.append(p)
        else:
            rx_bad += 1
    return out

link_init()

# ============================== DISPLAY ==============================

lcd = M5.Lcd
try:                                   # 720x1280 at rot 0, 1280x720 at rot 1
    lcd.setRotation(1)
    if lcd.width() < lcd.height():
        lcd.setRotation(0)
except Exception:
    pass
W, H = lcd.width(), lcd.height()
lcd.fillScreen(BG)

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def mapv(v, a, b, c, d):
    if b == a: return c
    return c + (v - a) * (d - c) // (b - a)

# setTextSize(N) integer-scales a tiny bitmap font, which looks blocky and
# blurry at the sizes this screen needs. Use the real vector fonts instead and
# keep textSize at 1 - they render at native resolution and stay crisp.
_F = lcd.FONTS
# A "face" is (font, integer_scale). The font NAMES ARE NOT PIXEL HEIGHTS -
# measured on this panel: DejaVu72 is 52px tall, DejaVu56 49px, DejaVu40 44px,
# DejaVu9 15px. 52px is the largest real glyph in the build, which is far too
# small for a headline number on a 720px screen. So the big readouts scale the
# largest face: 2x gives 104px, readable across a room. Scaling is chunky at
# the edges, but a crisp number nobody can read from the couch is worse.
FACE_STACK = ((_F.DejaVu72, 3), (_F.DejaVu72, 2),
              (_F.DejaVu72, 1), (_F.DejaVu56, 1), (_F.DejaVu40, 1),
              (_F.DejaVu24, 1), (_F.DejaVu18, 1), (_F.DejaVu12, 1),
              (_F.DejaVu9, 1))

def use_face(face):
    lcd.setFont(face[0])
    lcd.setTextSize(face[1])

def tw(s, f=None):
    if f is not None:
        use_face(f)
    return lcd.textWidth(s)

def th(f=None):
    if f is not None:
        use_face(f)
    return lcd.fontHeight()

def text_at(x, y, s, f, fg, bg):
    use_face(f)
    lcd.setTextColor(fg, bg)
    lcd.setCursor(int(x), int(y))
    lcd.print(s)

def fit(s, max_w, max_h, start_idx=0):
    """Biggest face that fits the box. Never assume character cells."""
    for f in FACE_STACK[start_idx:]:
        if tw(s, f) <= max_w and th(f) <= max_h:
            return f
    return FACE_STACK[-1]

# --------------------- flicker-free dynamic painting ---------------------
# The old dashboard repainted whole regions on a timer: draw_header() blanked
# the full header bar and redrew it twice a second whether or not anything had
# changed, and every tile update refilled the entire tile. That full-rect
# erase-then-redraw is exactly what the eye sees as flicker.
#
# Instead: paint static chrome ONCE, then repaint a value only when it really
# changed, and let lcd.print()'s opaque background box overwrite the glyphs in
# place. Only a string that SHRANK needs erasing, and only the strip it no
# longer covers.

_fields = {}                   # key -> last painted (x, y, w, h)
_shown = {}                    # key -> last painted value

def changed(key, val):
    """True (and remembers val) only when this field actually needs redrawing."""
    if _shown.get(key) == val:
        return False
    _shown[key] = val
    return True

def forget(*keys):
    """Drop cached state for regions that were wiped by a full repaint."""
    for k in keys:
        _shown.pop(k, None)
        _fields.pop(k, None)

def _clear_outside(old, new, bg):
    """Erase only the part of the previous paint the new one does not cover."""
    ox, oy, ow, oh = old
    nx, ny, nw, nh = new
    if ow <= 0 or oh <= 0:
        return
    if ox < nx:
        lcd.fillRect(ox, oy, min(ow, nx - ox), oh, bg)
    if ox + ow > nx + nw:
        lcd.fillRect(nx + nw, oy, (ox + ow) - (nx + nw), oh, bg)
    x0, x1 = max(ox, nx), min(ox + ow, nx + nw)
    if x1 > x0:
        if oy < ny:
            lcd.fillRect(x0, oy, x1 - x0, min(oh, ny - oy), bg)
        if oy + oh > ny + nh:
            lcd.fillRect(x0, ny + nh, x1 - x0, (oy + oh) - (ny + nh), bg)

def field(key, x, y, s, face, fg, bg=BG, right=None, center=None):
    """Paint a dynamic text field in place, with no erase-flash."""
    use_face(face)
    w, h = lcd.textWidth(s), lcd.fontHeight()
    if right is not None:
        x = right - w
    elif center is not None:
        x = center - w // 2
    x, y = int(x), int(y)
    lcd.setTextColor(fg, bg)
    lcd.setCursor(x, y)
    lcd.print(s)
    old = _fields.get(key)
    new = (x, y, w, h)
    if old:
        _clear_outside(old, new, bg)
    _fields[key] = new

def rrect(x, y, w, h, r, col, filled=False):
    """Rounded panel, with a plain-rect fallback if the build lacks it."""
    try:
        if filled:
            lcd.fillRoundRect(x, y, w, h, r, col)
        else:
            lcd.drawRoundRect(x, y, w, h, r, col)
    except Exception:
        if filled:
            lcd.fillRect(x, y, w, h, col)
        else:
            lcd.drawRect(x, y, w, h, col)

# ============================== LAYOUT ==============================
# Derived from the panel size, so this holds on any Tab5 rotation.

SAFE = max(10, W // 64)
L, R = SAFE, W - SAFE

HDR_H = max(70, H // 8)
F_TITLE = fit(APP_NAME, W // 3, HDR_H // 2, 2)
F_SUB = fit(APP_SUB, W // 3, HDR_H // 3, 5)
F_TAG = fit("88 LINKED", W // 4, HDR_H // 2, 2)
F_TILE_LBL = fit("BPM", 200, 60, 5)
F_ANNOT = fit("LED BEAT", 260, 50, 5)

# Title block centred in the bar rather than hung off HDR_H//2: with a 44px
# title in a 90px bar the old arithmetic put the first line at y = -1.
TITLE_Y = max(2, (HDR_H - 2 - (th(F_TITLE) + 4 + th(F_SUB))) // 2)
SUB_Y = TITLE_Y + th(F_TITLE) + 4

HEART_R = max(18, HDR_H // 3)
HEART_X = W // 2
HEART_Y = HDR_H // 2

# Header right cluster: battery + link bars on top, the LINKED tag underneath.
# The coach text used to sit here too; it now lives in the SIG panel, which
# leaves room for a much bigger tag.
BATT_W = max(34, W // 32)
BATT_H = max(16, HDR_H // 4)
BATT_X = R - BATT_W - 4
BATT_Y = 6
BAR_W = 4
BAR_GAP = 3
BARS = 4
BARS_X = BATT_X - 12 - (BAR_W + BAR_GAP) * BARS
BARS_Y = BATT_Y
TAG_Y = BATT_Y + BATT_H + 6

# --- bottom row: [BPM | IBI] in ONE window, a wider SIG panel beside it ---
# SIG carries the coach line that used to be in the header, so it has to be the
# larger of the two. The vitals window still has to hold "8888" at the 104px
# face, i.e. VIT_HALF - 26 >= 248px. Both are printed at boot so the fit can be
# checked against real measured metrics rather than assumed.
TILE_H = max(150, H // 4)                  # tall enough that BPM/IBI reach
TILE_Y = H - SAFE - TILE_H                 # the 104px (DejaVu72 x2) face
TILE_GAP = SAFE
_avail = R - L - TILE_GAP                  # ONE gap now, not two
VIT_W = (_avail * 46) // 100
SIG_W = _avail - VIT_W
VIT_X = L
SIGP_X = L + VIT_W + TILE_GAP
VIT_HALF = VIT_W // 2
VIT_CX = (VIT_X + VIT_HALF // 2, VIT_X + VIT_HALF + (VIT_W - VIT_HALF) // 2)

LBL_Y = TILE_Y + 8
VAL_MAX_W = VIT_HALF - 26
VAL_MAX_H = TILE_H - (8 + th(F_TILE_LBL) + 6) - 12
# Scaling is allowed here and nowhere else: this is THE number on the screen.
# One fixed face for both readouts so "72" and "--" do not change size.
F_VAL = fit("8888", VAL_MAX_W, VAL_MAX_H)
VAL_Y = TILE_Y + TILE_H - th(F_VAL) - 12

SEGS = 16                                  # SIG meter
SEG_H = TILE_H // 4
SEG_W = (SIG_W - 28) // SEGS
SEG_Y = TILE_Y + TILE_H - SEG_H - 14

COACH_Y = LBL_Y + th(F_TILE_LBL) + 8
COACH_MAX_W = SIG_W - 28
COACH_MAX_H = SEG_Y - 8 - COACH_Y
# One fixed face that fits EVERY coach string, so the line never changes size
# as the state changes - a field whose face moves cannot be repainted in place,
# and a headline that resizes under you is its own kind of flicker.
_COACH_STRINGS = STATE_NAMES + ("WAITING FOR STICK",)
F_COACH = FACE_STACK[-1]
for _f in FACE_STACK:
    if th(_f) <= COACH_MAX_H:
        _ok = True
        for _t in _COACH_STRINGS:
            if tw(_t, _f) > COACH_MAX_W:
                _ok = False
                break
        if _ok:
            F_COACH = _f
            break

GX = L + 2                                  # waveform window
GY = HDR_H + SAFE
GW = (R - 2) - GX
GH = TILE_Y - SAFE - GY
GRID_X = max(40, GW // 12)
GRID_Y = max(30, GH // 5)

# The sweep erases and redraws whole columns, so it wipes the THR / LED BEAT
# text as it passes under them. Remember where that zone starts (graph-relative)
# and force a repaint while the sweep is inside it.
ANN_W = max(tw("THR 8888", F_ANNOT), tw("LED BEAT", F_ANNOT)) + 16
ANN_GX = GW - ANN_W

# The stick samples at 50Hz and sends every 2nd sample, so packets arrive at
# 25Hz. One pixel per packet would span ~50s of history across this wide
# screen; scale x so the window holds exactly WAVE_SECONDS.
WAVE_SECONDS = 5
PKT_HZ = 25
PTS_PER_PKT = 2                            # stick batches 2 samples per packet
WAVE_SAMPLES = WAVE_SECONDS * PKT_HZ * PTS_PER_PKT
X_STEP = max(1, GW // WAVE_SAMPLES)        # ~5px: smooth, not angular

# Packets arrive faster than the panel can usefully be repainted, and several
# can land in a single poll. Queue their samples and drain the queue on a
# capped cadence, so no sample is silently dropped the way it was when the
# loop overwrote a 2-sample tuple per packet and drew once.
WAVE_FIFO_MAX = WAVE_SAMPLES               # one screenful; older is off-screen
RENDER_MS = 33                             # cap waveform repaints at ~30/s
MAX_PER_FRAME = 16                         # bound the work inside one frame

# ============================== STATE ==============================

class Stick:
    __slots__ = ("dev", "bpm", "ibi", "quality", "state", "beat", "resync",
                 "sig", "wave", "smin", "smax", "thresh", "amp", "last")

    def __init__(self, dev):
        self.dev = dev
        self.bpm = self.ibi = self.quality = self.amp = 0
        self.state = 2
        self.beat = False
        self.resync = False
        self.sig = 512
        self.wave = []                      # (sample, beat_edge) pending draw
        self.smin, self.smax = 0, 1023
        self.thresh = 550
        self.last = time.ticks_ms()

sticks = []
primary = None                              # the one shown full screen

def stick_for(dev):
    global primary
    for s in sticks:
        if s.dev == dev:
            return s
    s = Stick(dev)
    sticks.append(s)
    if primary is None:
        primary = s
    print("LINK: stick %s joined" % dev.hex())
    return s

gx = 0                                      # waveform sweep column
last_gy = GY + GH // 2

# ============================== DRAWING ==============================

def wave_color(s):
    """Yellow while acquiring, green once the quality meter locks."""
    return GREEN if (s and s.state == LOCKED_STATE) else YELLOW

def draw_heart(col):
    """Always the SAME geometry, only the colour changes.

    The heart used to shrink between beats, which meant blanking a box around
    it and redrawing - a visible erase flash 120 times a minute. Repainting the
    identical shape in a new colour overwrites every pixel it owns and leaves
    no residue, so the pulse costs no erase at all.
    """
    cx, cy, r = HEART_X, HEART_Y, HEART_R
    lcd.fillCircle(cx - r // 2, cy - r // 3, r // 2 + 1, col)
    lcd.fillCircle(cx + r // 2, cy - r // 3, r // 2 + 1, col)
    lcd.fillTriangle(cx - r, cy - r // 4, cx + r, cy - r // 4, cx, cy + r, col)

def draw_heart_area(s):
    beat = bool(s and s.beat)
    if changed("heart", beat):
        draw_heart(RED if beat else RED_DIM)

def draw_link_bars(rate):
    """Link-quality bars from the ACTUAL packet rate.

    The AP interface exposes connected station MACs but no RSSI, so radio
    signal strength is not available. Packet rate against the expected 25/s
    is a real, honest measure of link health - not a guess at RSSI.
    """
    frac = 0.0 if rate <= 0 else min(1.0, rate / float(PKT_HZ_EXPECTED))
    lit = int(frac * BARS + 0.5)
    col = GREEN if frac >= 0.8 else (YELLOW if frac >= 0.4 else CYAN)
    if not changed("bars", (lit, col)):
        return
    for i in range(BARS):
        h = 5 + i * ((BATT_H - 5) // (BARS - 1))
        x = BARS_X + i * (BAR_W + BAR_GAP)
        lcd.fillRect(x, BARS_Y, BAR_W, BATT_H - h, BG)
        lcd.fillRect(x, BARS_Y + BATT_H - h, BAR_W, h, col if i < lit else GRID)

def draw_battery(level, charging):
    """Level bar when the gauge reports one; an EXT pill when it does not.

    A level of 0 means the gauge sees no pack and the Tab5 is on the USB rail;
    drawing an empty battery there would be a lie, so external power gets its
    own explicit indicator. Bucketed to 5% so a one-count wobble is not a
    repaint.
    """
    if not changed("batt", (level // 5, bool(charging), level > 0)):
        return
    lcd.fillRect(BATT_X - 2, BATT_Y - 2, BATT_W + 8, BATT_H + 4, BG)
    lcd.drawRect(BATT_X, BATT_Y, BATT_W, BATT_H, LABEL)
    lcd.fillRect(BATT_X + BATT_W, BATT_Y + BATT_H // 3, 3, BATT_H // 3, LABEL)
    if level and level > 0:
        col = GREEN if level > 50 else (YELLOW if level > 20 else RED)
        fill = (min(100, level) * (BATT_W - 4)) // 100
        if fill:
            lcd.fillRect(BATT_X + 2, BATT_Y + 2, fill, BATT_H - 4,
                         CYAN if charging else col)
    else:
        f = fit("EXT", BATT_W - 6, BATT_H - 4, 6)
        text_at(BATT_X + (BATT_W - tw("EXT", f)) // 2,
                BATT_Y + (BATT_H - th(f)) // 2, "EXT", f, CYAN, BG)

# The Tab5 gauge alternates between a plausible pack reading and one at roughly
# half the voltage every ~5s (measured: 100 @ 8393mV / 0 @ 4362mV), i.e. it
# intermittently reports one cell of the 2S pack instead of the pack. A median
# does NOT fix this: the fault is a slow square wave, so a window that spans it
# just flips whenever the sample counts tip. Taking the MAX over the window
# rejects the half-scale misread and still tracks a real discharge, lagging by
# at most the window length.
BATT_WIN = 8                   # samples, 1/s -> 8s of history
_batt_hist = []

def read_power():
    try:
        return (M5.Power.getBatteryLevel(), M5.Power.getBatteryVoltage(),
                bool(M5.Power.isCharging()))
    except Exception:
        return (0, 0, False)

batt_raw = (0, 0)              # last unfiltered reading, for the stat line

def batt_sample():
    """Push one raw reading, return the de-glitched (level, volts, charging)."""
    global batt_raw
    lvl, mv, chg = read_power()
    batt_raw = (lvl, mv)
    _batt_hist.append((lvl, mv, chg))
    if len(_batt_hist) > BATT_WIN:
        del _batt_hist[0:len(_batt_hist) - BATT_WIN]
    best = _batt_hist[0]
    for r in _batt_hist:
        if r[0] > best[0] or (r[0] == best[0] and r[1] > best[1]):
            best = r
    return (best[0], best[1], chg)          # charging is not glitchy, use live

def draw_link_tag(nlinked):
    tag = "%d LINKED" % nlinked if nlinked else "NO LINK"
    if changed("tag", tag):
        field("tag", 0, TAG_Y, tag, F_TAG, GREEN if nlinked else LABEL,
              BG, right=R)

def draw_resync_banner(on):
    """Big confirmation when the stick's blue button is pressed, so the press
    is visible from across the room on the Tab5 as well as on the stick."""
    msg = "RESYNC"
    f = fit(msg, GW // 3, GH // 3)
    bw, bh = tw(msg, f) + 60, th(f) + 30
    bx, by = GX + (GW - bw) // 2, GY + (GH - bh) // 2
    if on:
        rrect(bx, by, bw, bh, 16, YELLOW, filled=True)
        rrect(bx, by, bw, bh, 16, TEXT)
        text_at(bx + 30, by + 15, msg, f, BG, YELLOW)
    return (bx, by, bw, bh)

def draw_graph_frame():
    rrect(GX - 2, GY - 2, GW + 4, GH + 4, 10, FRAME)
    lcd.fillRect(GX, GY, GW, GH, BG)
    for x in range(GRID_X, GW, GRID_X):
        lcd.fillRect(GX + x, GY, 1, GH, GRID)
    for y in range(GRID_Y, GH, GRID_Y):
        lcd.fillRect(GX, GY + y, GW, 1, GRID)
    forget("thr", "beattag")     # this wiped them; whoever erases, forgets

def thresh_y(s):
    lo, hi = (s.smin, s.smax) if s else (0, 1023)
    if hi <= lo:
        hi = lo + 1
    t = s.thresh if s else 550
    return clamp(mapv(t, lo, hi, GY + GH - 6, GY + 6), GY + 6, GY + GH - 6)

def draw_annotations(s, force=False):
    """THR readout + LED BEAT tag inside the graph.

    `force` bypasses the change check for the one case where the pixels went
    away without the value changing: the sweep column erasing straight through
    the text. Repainting is still an in-place opaque print, never an erase.
    """
    thr = "THR %d" % (s.thresh if s else 0)
    if force or changed("thr", thr):
        field("thr", 0, GY + 6, thr, F_ANNOT, TEXT, BG, right=GX + GW - 8)
    col = wave_color(s)
    if force or changed("beattag", col):
        field("beattag", 0, GY + GH - th(F_ANNOT) - 8, "LED BEAT", F_ANNOT,
              col, BG, right=GX + GW - 8)

def clear_column(cx, s):
    x = GX + cx
    lcd.fillRect(x, GY, 1, GH, GRID if (cx % GRID_X == 0) else BG)
    for y in range(GRID_Y, GH, GRID_Y):
        lcd.drawPixel(x, GY + y, GRID)
    if cx % 8 < 4:                          # dotted threshold line
        lcd.drawPixel(x, thresh_y(s), DOT)

def draw_wave(s, batch):
    """Draw every queued sample as a connected trace, so nothing is dropped.

    `batch` is a list of (sample, beat_edge) drained from the stick's FIFO.
    The trace stays connected across calls via last_gy, exactly as when this
    drew a single packet at a time.
    """
    global gx, last_gy
    lo, hi = s.smin, s.smax
    if hi <= lo:
        hi = lo + 1
    col = wave_color(s)
    crossed_annot = False
    for sample, beat in batch:
        y = clamp(mapv(sample, lo, hi, GY + GH - 6, GY + 6),
                  GY + 6, GY + GH - 6)
        for k in range(X_STEP * 2):             # erase the span ahead
            clear_column((gx + k) % GW, s)
        if gx:
            x0, x1 = GX + gx - X_STEP, GX + gx
            lcd.drawLine(x0, last_gy, x1, y, col)
            lcd.drawLine(x0, last_gy + 1, x1, y + 1, col)
            lcd.drawLine(x0, last_gy - 1, x1, y - 1, col)
        if beat:
            for yy in range(GY + 4, GY + GH - 4, 6):
                lcd.drawPixel(GX + gx, yy, YELLOW)
        if gx >= ANN_GX:
            crossed_annot = True
        last_gy = y
        gx += X_STEP
        if gx >= GW:
            gx = 0
            draw_graph_frame()
            crossed_annot = True
    if crossed_annot:
        draw_annotations(s, force=True)

# ------------------------- bottom row: vitals + SIG -------------------------
# Two panels, not three. [BPM | IBI] share one window with a hairline divider;
# SIG is the wider one and carries the coach line that used to be jammed into
# the header. Nothing here fills a panel rect: the borders and labels are
# static chrome painted once at boot, and every value repaints in place.
#
# The BPM tile's beat flash is gone on purpose. Inverting a 561x180 panel 120
# times a minute WAS the second-biggest flicker source on the screen, and the
# beat is already reported twice - the heart in the header and the yellow
# marker on the waveform.

def draw_vitals(s, stale):
    if s is None or stale:
        bpm, ibi, col = "--", "--", LABEL
    else:
        col = wave_color(s)
        bpm = str(s.bpm) if s.bpm else "--"
        ibi = str(s.ibi) if s.ibi else "--"
    if changed("bpm", (bpm, col)):
        field("bpm", 0, VAL_Y, bpm, F_VAL, col, BG, center=VIT_CX[0])
    if changed("ibi", (ibi, col)):
        field("ibi", 0, VAL_Y, ibi, F_VAL, col, BG, center=VIT_CX[1])

def draw_coach(s, stale):
    """The signal coach, now the widest readout on the screen."""
    if s is None or stale:
        name, col = "WAITING FOR STICK", CYAN
    else:
        name = STATE_NAMES[s.state] if s.state < len(STATE_NAMES) else "?"
        col = GREEN if s.state == LOCKED_STATE else YELLOW
    if changed("coach", (name, col)):
        field("coach", SIGP_X + 14, COACH_Y, name, F_COACH, col, BG)

def draw_meter(s, stale):
    if s is None or stale:
        pct, col = 0, LABEL
    else:
        col = wave_color(s)
        pct = clamp(s.smax - s.smin, 0, 600) * 100 // 600
    filled = clamp(pct * SEGS // 100, 0, SEGS)
    if not changed("meter", (filled, col)):
        return
    for k in range(SEGS):
        lcd.fillRect(SIGP_X + 14 + k * SEG_W, SEG_Y, SEG_W - 3, SEG_H,
                     col if k < filled else METER_OFF)

def draw_static():
    """Everything that never changes, painted exactly once.

    Borders, fixed labels and the title block used to be repainted on a timer
    along with the values they surround. They are chrome; they get one pass.
    """
    lcd.fillScreen(BG)
    lcd.fillRect(0, HDR_H - 2, W, 2, FRAME)
    text_at(L, TITLE_Y, APP_NAME, F_TITLE, TEXT, BG)
    text_at(L, SUB_Y, APP_SUB, F_SUB, LABEL, BG)
    draw_graph_frame()
    rrect(VIT_X, TILE_Y, VIT_W, TILE_H, 12, FRAME)
    rrect(SIGP_X, TILE_Y, SIG_W, TILE_H, 12, FRAME)
    lcd.fillRect(VIT_X + VIT_HALF, TILE_Y + 16, 1, TILE_H - 32, FRAME)
    text_at(VIT_X + 14, LBL_Y, "BPM", F_TILE_LBL, LABEL, BG)
    # The unit rides on the label: at 104px a four-digit IBI already uses
    # VAL_MAX_W, so there is no room for a suffix beside the number.
    text_at(VIT_X + VIT_HALF + 14, LBL_Y, "IBI ms", F_TILE_LBL, LABEL, BG)
    text_at(SIGP_X + 14, LBL_Y, "SIG", F_TILE_LBL, LABEL, BG)
    draw_heart(RED_DIM)
    forget("heart", "bars", "batt", "tag", "thr", "beattag",
           "bpm", "ibi", "coach", "meter")

# ============================== BOOT ==============================

draw_static()
batt_level, batt_v, batt_chg = batt_sample()
draw_link_tag(0)
draw_link_bars(0)
draw_battery(batt_level, batt_chg)
draw_annotations(None)
draw_vitals(None, True)
draw_coach(None, True)
draw_meter(None, True)
print("pulselink_tab5: %dx%d dashboard, waiting for sticks" % (W, H))
# Printed so the layout can be checked against MEASURED font metrics from the
# serial log alone - this app has no way to look at its own screen.
print("LAYOUT: vitals x=%d w=%d half=%d | sig x=%d w=%d" %
      (VIT_X, VIT_W, VIT_HALF, SIGP_X, SIG_W))
print("LAYOUT: value face h=%d  '8888' w=%d  fits %d  (y=%d)" %
      (th(F_VAL), tw("8888", F_VAL), VAL_MAX_W, VAL_Y))
print("LAYOUT: coach face h=%d  longest w=%d  fits %d x %d" %
      (th(F_COACH), max([tw(t, F_COACH) for t in _COACH_STRINGS]),
       COACH_MAX_W, COACH_MAX_H))

prev_resync = False
UI_MS = 50                      # text/meter refresh gate; see the main loop
last_ui = time.ticks_ms()
last_stat = time.ticks_ms()
last_wave = time.ticks_ms()
rx_count = 0
rate_mark = time.ticks_ms()     # packet-rate window for the signal bars
rate_base = 0
pkt_rate = 0
last_batt = time.ticks_ms()

while True:
    now = time.ticks_ms()

    for p in link_poll():
        rx_count += 1
        s = stick_for(p["dev"])
        s.bpm = p["bpm"]; s.ibi = p["ibi"]; s.quality = p["quality"]
        s.state = p["state"]; s.beat = p["beat"]
        sm = p["samples"]
        s.sig = sm[-1]
        # Queue rather than overwrite. The beat edge is marked once per packet
        # instead of on every sample, so one beat draws one marker.
        s.wave.append((sm[0], p["beat"]))
        s.wave.append((sm[1], False))
        if len(s.wave) > WAVE_FIFO_MAX:
            del s.wave[0:len(s.wave) - WAVE_FIFO_MAX]
        s.resync = p["resync"]
        s.smin = p["smin"]; s.smax = p["smax"]
        s.thresh = p["thresh"]; s.amp = p["amp"]
        s.last = now

    s = primary
    stale = (s is None) or time.ticks_diff(now, s.last) > STALE_MS
    nlinked = 0
    for x in sticks:
        if time.ticks_diff(now, x.last) <= STALE_MS:
            nlinked += 1

    # Drain the waveform FIFO on a capped cadence rather than once per packet.
    if s is not None and time.ticks_diff(now, last_wave) >= RENDER_MS:
        last_wave = now
        if stale:
            del s.wave[:]                    # nothing live to draw
        elif s.wave:
            if len(s.wave) > MAX_PER_FRAME:
                # Renderer is behind the link. Drop the oldest so the trace
                # stays live instead of lagging further behind every frame.
                del s.wave[0:len(s.wave) - MAX_PER_FRAME]
            batch = s.wave[:]
            del s.wave[:]
            draw_wave(s, batch)

    # Every painter below is change-driven: each compares its value against
    # what is actually on the panel and returns without touching a pixel when
    # nothing moved. There is no repaint timer any more - the old 500ms header
    # rebuild was itself the flicker.
    #
    # The heart is checked EVERY pass on purpose. A beat flag only lives for
    # the one packet that carries it (~40ms), so a gate here would drop beats.
    # It costs a dict lookup and a bool compare when nothing changed.
    draw_heart_area(None if stale else s)
    if time.ticks_diff(now, last_ui) >= UI_MS:
        last_ui = now
        draw_vitals(s, stale)
        draw_coach(s, stale)
        draw_meter(s, stale)
        draw_annotations(None if stale else s)
        draw_link_tag(nlinked)

    # RESYNC: the stick sets state 7 (and a flag) for ~900ms after BtnA
    rs = bool(s and not stale and (s.resync or s.state == 7))
    if rs != prev_resync:
        if rs:
            draw_resync_banner(True)
        else:
            draw_graph_frame()               # wipe the banner cleanly
        draw_annotations(None if stale else s, force=True)
        prev_resync = rs

    # measured packet rate over a 1s window -> signal bars
    if time.ticks_diff(now, rate_mark) >= 1000:
        pkt_rate = rx_count - rate_base
        rate_base = rx_count
        rate_mark = now
        draw_link_bars(pkt_rate)

    # Gauge sampled at 1Hz and de-glitched over 8 samples; see batt_sample().
    if time.ticks_diff(now, last_batt) >= 1000:
        last_batt = now
        batt_level, batt_v, batt_chg = batt_sample()
        draw_battery(batt_level, batt_chg)

    if time.ticks_diff(now, last_stat) >= 5000:
        last_stat = now
        # batt shows filtered/raw so the de-glitching can be seen working
        # rather than assumed - raw is expected to flap, filtered is not.
        print("rx=%d bad=%d rate=%d/s linked=%d bpm=%s state=%s "
              "batt=%d%%(%dmV) raw=%d%%(%dmV)"
              % (rx_count, rx_bad, pkt_rate, nlinked,
                 s.bpm if s else "-", s.state if s else "-",
                 batt_level, batt_v, batt_raw[0], batt_raw[1]))

    time.sleep_ms(5)          # always yield: never starve the task watchdog
