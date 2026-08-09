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
import gc
import math
from array import array

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
    """Bring up the SoftAP and the receive socket. Safe to call again.

    Idempotence is not decoration: the stall watchdog calls this to recover a
    wedged link, and an earlier version bound port 5005 while the previous
    socket still held it. That failed EADDRINUSE and left the app with no
    socket at all - the recovery path CAUSING the outage it exists to clear.
    """
    global _sock, _ap
    try:
        import network, socket
        if _sock is not None:
            try:
                _sock.close()
            except Exception:
                pass
            _sock = None
        _ap = network.WLAN(network.AP_IF)
        _ap.active(True)
        try:
            _ap.config(essid=LINK_SSID, password=LINK_PSK, authmode=3)
        except Exception:
            try:
                _ap.config(essid=LINK_SSID, password=LINK_PSK)
            except Exception:
                _ap.config(essid=LINK_SSID)
        # Only publish the socket once it is fully bound and non-blocking; a
        # half-configured one reaching link_poll() is worse than none.
        _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _s.bind(("0.0.0.0", LINK_PORT))
        _s.setblocking(False)
        _sock = _s
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

# --- receiver-side stall watchdog -----------------------------------------
# The SENDER has had a zombie detector since the ENOMEM stall was found: after
# a couple of seconds of solid send failures it drops the association and
# rejoins. The RECEIVER had no equivalent, so if the socket on this side ever
# wedged there was nothing to recover it and the display sat there showing a
# dead link forever.
#
# Observed once, and not reproduced in seven attempts across both restart
# orders: rx frozen at 6 for over 100s while a freshly booted stick reported
# itself associated and healthy, and only a Tab5 reboot cleared it. That is
# thin evidence for a diagnosis, which is exactly why this is a recovery
# mechanism rather than a fix - it does not claim to know the cause.
#
# It fires ONLY when the AP says a station is actually associated. An idle desk
# with no stick powered on is silent for a correct reason, and rebuilding the
# socket every few seconds because of that would be its own bug.
RX_STALL_MS = 12000
last_rx = 0
sock_rebuilds = 0
ap_restarts = 0
_stall_strikes = 0

def ap_stations():
    """Number of associated stations, or -1 if the firmware will not say."""
    try:
        return len(_ap.status("stations"))
    except Exception:
        return -1

def link_watchdog(now):
    global _sock, last_rx, sock_rebuilds, ap_restarts, _stall_strikes
    if time.ticks_diff(now, last_rx) < RX_STALL_MS:
        return
    n = ap_stations()
    if n <= 0:
        return                      # nothing associated: silence is correct
    last_rx = now                   # one action per window, not per pass
    _stall_strikes += 1
    if _stall_strikes <= 3:
        try:
            import socket
            if _sock is not None:
                try:
                    _sock.close()
                except Exception:
                    pass
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("0.0.0.0", LINK_PORT))
            s.setblocking(False)
            _sock = s
            sock_rebuilds += 1
            print("LINK: rx stalled, %d station(s) associated - socket "
                  "rebuilt (#%d, strike %d)" % (n, sock_rebuilds,
                                                _stall_strikes))
        except Exception as ex:
            _sock = None
            print("LINK: socket rebuild FAILED (%s)" % ex)
    elif _stall_strikes == 4:
        # Bigger hammer, and a last resort: this drops every associated
        # station and makes them all rejoin.
        ap_restarts += 1
        print("LINK: rx still stalled after %d rebuilds - restarting AP (#%d)"
              % (sock_rebuilds, ap_restarts))
        link_init()
    # beyond strike 4, stop escalating and stop logging until traffic returns

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
    if w > 0:
        lcd.setTextColor(fg, bg)
        lcd.setCursor(x, y)
        lcd.print(s)
    # An empty string still has to erase what it replaced - the opaque print
    # box is what normally does the erasing, and a zero-width box erases
    # nothing. Recording a zero-size rect makes _clear_outside() wipe all of
    # the previous paint.
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
# Everything here is DERIVED from the current panel size and recomputed by
# layout(). It used to run once at import, which was fine while the rotation
# was fixed at boot - auto-rotate makes every one of these constants a lie the
# moment the screen turns. Nothing outside this function may cache geometry.
#
# The bare-global style is deliberate: the drawing code reads these names
# directly, and threading a namespace object through every call site would be a
# large mechanical change for no behavioural gain.

def layout():
    global W, H, SAFE, L, R, HDR_H
    global F_TITLE, F_SUB, F_TAG, F_TILE_LBL, F_ANNOT, F_VAL, F_COACH
    global TITLE_Y, SUB_Y, HEART_R, HEART_X, HEART_Y
    global BATT_W, BATT_H, BATT_X, BATT_Y, BAR_W, BAR_GAP, BARS
    global BARS_X, BARS_Y, TAG_Y
    global TILE_H, TILE_Y, TILE_GAP, VIT_W, SIG_W, VIT_X, SIGP_X
    global VIT_HALF, VIT_CX, LBL_Y, VAL_MAX_W, VAL_MAX_H, VAL_Y
    global SEGS, SEG_H, SEG_W, SEG_Y
    global COACH_Y, COACH_MAX_W, COACH_MAX_H
    global GX, GY, GW, GH, GRID_X, GRID_Y, ANN_W, ANN_GX
    global WAVE_SAMPLES, X_STEP, WAVE_FIFO_MAX
    global F_SECT, F_ROW_L, F_ROW_V, ROW_LBL_W
    global gx, last_gy

    W, H = lcd.width(), lcd.height()

    SAFE = max(10, W // 64)
    L, R = SAFE, W - SAFE

    HDR_H = max(70, H // 8)
    F_TITLE = fit(APP_NAME, W // 3, HDR_H // 2, 2)
    F_SUB = fit(APP_SUB, W // 3, HDR_H // 3, 5)
    F_TAG = fit("88 LINKED", W // 4, HDR_H // 2, 2)
    F_TILE_LBL = fit("BPM", 200, 60, 5)
    F_ANNOT = fit("LED BEAT", 260, 50, 5)

    # Sub-screen typography: a section heading and a label/value row. The
    # label column is sized from the widest label any screen uses, measured
    # rather than guessed, so no value ever starts on top of its own label.
    F_SECT = fit("TRANSPORT", W // 3, 46, 4)
    F_ROW_L = fit("pack voltage", W // 4, 34, 5)
    F_ROW_V = fit("n/a - SoftAP exposes no RSSI", W // 2, 34, 5)
    ROW_LBL_W = tw("pack voltage", F_ROW_L) + 24

    # Title block centred in the bar rather than hung off HDR_H//2: with a
    # 44px title in a 90px bar the old arithmetic put the first line at y = -1.
    TITLE_Y = max(2, (HDR_H - 2 - (th(F_TITLE) + 4 + th(F_SUB))) // 2)
    SUB_Y = TITLE_Y + th(F_TITLE) + 4

    HEART_R = max(18, HDR_H // 3)
    HEART_X = W // 2
    HEART_Y = HDR_H // 2

    # Header right cluster: battery + link bars on top, LINKED tag underneath.
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

    # --- bottom row: [BPM | IBI] in ONE window, a wider SIG panel beside it --
    # SIG carries the coach line that used to be in the header, so it has to be
    # the larger of the two. The vitals window still has to hold "8888" at the
    # 104px face, i.e. VIT_HALF - 26 >= 248px. Both are printed at boot so the
    # fit can be checked against real measured metrics rather than assumed.
    TILE_H = max(150, H // 4)              # tall enough that BPM/IBI reach
    TILE_Y = H - SAFE - TILE_H             # the 104px (DejaVu72 x2) face
    TILE_GAP = SAFE
    avail = R - L - TILE_GAP               # ONE gap now, not two
    VIT_W = (avail * 46) // 100
    SIG_W = avail - VIT_W
    VIT_X = L
    SIGP_X = L + VIT_W + TILE_GAP
    VIT_HALF = VIT_W // 2
    VIT_CX = (VIT_X + VIT_HALF // 2,
              VIT_X + VIT_HALF + (VIT_W - VIT_HALF) // 2)

    LBL_Y = TILE_Y + 8
    VAL_MAX_W = VIT_HALF - 26
    VAL_MAX_H = TILE_H - (8 + th(F_TILE_LBL) + 6) - 12
    # Scaling is allowed here and nowhere else: this is THE number on screen.
    # One fixed face for both readouts so "72" and "--" do not change size.
    F_VAL = fit("8888", VAL_MAX_W, VAL_MAX_H)
    VAL_Y = TILE_Y + TILE_H - th(F_VAL) - 12

    SEGS = 16                              # SIG meter
    SEG_H = TILE_H // 4
    SEG_W = (SIG_W - 28) // SEGS
    SEG_Y = TILE_Y + TILE_H - SEG_H - 14

    COACH_Y = LBL_Y + th(F_TILE_LBL) + 8
    COACH_MAX_W = SIG_W - 28
    COACH_MAX_H = SEG_Y - 8 - COACH_Y
    # One fixed face that fits EVERY coach string, so the line never changes
    # size as the state changes - a field whose face moves cannot be repainted
    # in place, and a headline that resizes under you is its own flicker.
    F_COACH = FACE_STACK[-1]
    for f in FACE_STACK:
        if th(f) <= COACH_MAX_H:
            ok = True
            for t in COACH_STRINGS:
                if tw(t, f) > COACH_MAX_W:
                    ok = False
                    break
            if ok:
                F_COACH = f
                break

    GX = L + 2                             # waveform window
    GY = HDR_H + SAFE
    GW = (R - 2) - GX
    GH = TILE_Y - SAFE - GY
    GRID_X = max(40, GW // 12)
    GRID_Y = max(30, GH // 5)

    # The sweep erases and redraws whole columns, so it wipes the THR/LED BEAT
    # text as it passes under them. Remember where that zone starts (graph-
    # relative) and force a repaint while the sweep is inside it.
    ANN_W = max(tw("THR 8888", F_ANNOT), tw("LED BEAT", F_ANNOT)) + 16
    ANN_GX = GW - ANN_W

    # One pixel per packet would span ~50s of history across this wide screen;
    # scale x so the window holds exactly WAVE_SECONDS.
    WAVE_SAMPLES = WAVE_SECONDS * PKT_HZ * PTS_PER_PKT
    X_STEP = max(1, GW // WAVE_SAMPLES)    # ~5px: smooth, not angular
    WAVE_FIFO_MAX = WAVE_SAMPLES           # one screenful; older is off-screen

    # The sweep column is an index into the OLD width; on a rotation it would
    # point off the new graph. Restart it.
    gx = 0
    last_gy = GY + GH // 2


# The stick samples at 50Hz and sends every 2nd sample, so packets arrive at
# 25Hz. These are properties of the link, not of the panel, so they do not
# belong inside layout().
WAVE_SECONDS = 5
PKT_HZ = 25
PTS_PER_PKT = 2                            # stick batches 2 samples per packet
COACH_STRINGS = STATE_NAMES + ("WAITING FOR STICK",)

# Packets arrive faster than the panel can usefully be repainted, and several
# can land in a single poll. Queue their samples and drain the queue on a
# capped cadence, so no sample is silently dropped the way it was when the
# loop overwrote a 2-sample tuple per packet and drew once.
RENDER_MS = 33                             # cap waveform repaints at ~30/s
MAX_PER_FRAME = 16                         # bound the work inside one frame

# ============================== STATE ==============================

HIST_SECONDS = 12                           # PPG history kept for the FFT
HIST_MAX = HIST_SECONDS * PKT_HZ * PTS_PER_PKT
SAMPLE_HZ = PKT_HZ * PTS_PER_PKT            # 50Hz effective sample rate

class Stick:
    __slots__ = ("dev", "bpm", "ibi", "quality", "state", "beat", "resync",
                 "sig", "wave", "smin", "smax", "thresh", "amp", "last",
                 "rx", "first_seen", "hist")

    def __init__(self, dev):
        self.dev = dev
        self.bpm = self.ibi = self.quality = self.amp = 0
        self.state = 2
        self.beat = False
        self.resync = False
        self.sig = 512
        self.wave = []                      # (sample, beat_edge) pending draw
        self.hist = []                       # raw samples, oldest first (FFT)
        self.smin, self.smax = 0, 1023
        self.thresh = 550
        self.rx = 0                          # packets from THIS stick
        self.first_seen = time.ticks_ms()
        self.last = time.ticks_ms()

sticks = []
primary = None                              # the one shown full screen

STICK_MAX = 8                  # hard cap on tracked device ids

def stick_for(dev):
    """Find or create the Stick for a device id, with a bounded roster.

    The device id is three bytes chosen by whatever is sending, and the AP
    takes packets from anyone holding the PSK. Unbounded, a sender walking
    through ids would mint a Stick each time - and each one carries a
    HIST_MAX-sample history - until the heap ran out. So the roster is capped,
    and a new id may only take the place of one that has already gone stale.
    """
    global primary
    for s in sticks:
        if s.dev == dev:
            return s
    if len(sticks) >= STICK_MAX:
        now = time.ticks_ms()
        victim, oldest = None, STALE_MS
        for s in sticks:
            age = time.ticks_diff(now, s.last)
            if age > oldest:
                victim, oldest = s, age
        if victim is None:
            return sticks[0]        # roster full of live sticks: ignore this id
        print("LINK: evicting %s (stale %dms) for %s"
              % (victim.dev.hex(), oldest, dev.hex()))
        if primary is victim:
            primary = None
        sticks.remove(victim)
    s = Stick(dev)
    sticks.append(s)
    if primary is None:
        primary = s
    print("LINK: stick %s joined" % dev.hex())
    return s

gx = 0                                      # waveform sweep column (layout())
last_gy = 0

# ============================== INPUT ==============================
# M5.update() was never called by this app - nothing polled the touch panel or
# the buttons. It has to run every loop pass now, and it is the ONLY place
# allowed to do so: calling it twice a pass eats button edges.

TAP_MAX_MS = 700               # longer than this is a hold, not a tap
TAP_SLOP = 40                  # px of drift still counted as a tap

_tap_down = None               # (x, y, t_ms) while a finger is down

def poll_tap(now):
    """Return (x, y) of a COMPLETED tap, else None.

    Deliberately edge-triggered on release rather than on touch-down: acting on
    touch-down makes a scroll or a stray brush fire a navigation, and there is
    no way to take that back once a screen has repainted.
    """
    global _tap_down
    try:
        n = M5.Touch.getCount()
    except Exception:
        return None
    if n > 0:
        if _tap_down is None:
            try:
                x, y = M5.Touch.getX(), M5.Touch.getY()
            except Exception:
                return None
            if x >= 0 and y >= 0:
                _tap_down = (x, y, now)
        return None
    if _tap_down is None:
        return None
    x, y, t = _tap_down
    _tap_down = None
    if time.ticks_diff(now, t) <= TAP_MAX_MS:
        return (x, y)
    return None

# Hit regions are rebuilt by whichever screen is showing; a stale region from
# the previous screen would fire on a tap that visually hit nothing.
_hits = []

def hit_clear():
    del _hits[:]

def hit(x, y, w, h, action):
    _hits.append((int(x), int(y), int(w), int(h), action))

def hit_test(x, y):
    for hx, hy, hw, hh, action in _hits:
        if hx <= x < hx + hw and hy <= y < hy + hh:
            return action
    return None

# ============================== ROTATION ==============================
# Roadmap 1: the Tab5 senses its own orientation and never presents upside
# down. Gravity tells us which way is down; the panel rotation that puts the
# screen upright follows from that.
#
# CALIBRATION: measured on this device sitting the way Yury reads it,
# M5.Imu.getAccel() returned x=+1.00, y=-0.01, z=+0.01 - i.e. at rotation 1
# gravity runs along +X. The other three follow from rotating in-plane. If the
# screen comes up upside down, this table is the ONE thing to change.
ROT_FROM_GRAVITY = ((0, 1, 1),      # (axis 0=x/1=y, sign, rotation)
                    (0, -1, 3),
                    (1, 1, 0),
                    (1, -1, 2))

AUTO_ROTATE = True
ROT_TILT_MIN = 0.55            # below this the device is flat: keep current
ROT_STABLE_N = 8               # agreeing samples needed before turning
ROT_POLL_MS = 200

_rot_votes = 0
_rot_want = None

def read_orientation():
    """Rotation the device is physically asking for, or None if undecided."""
    try:
        ax, ay, _az = M5.Imu.getAccel()
    except Exception:
        return None
    best = None
    mag = 0.0
    for axis, sign, rot in ROT_FROM_GRAVITY:
        v = (ax if axis == 0 else ay) * sign
        if v > mag:
            mag, best = v, rot
    if best is None or mag < ROT_TILT_MIN:
        return None                # lying flat, or ambiguous: do not guess
    return best

def apply_rotation(rot):
    """Turn the panel and rebuild every derived constant."""
    try:
        lcd.setRotation(rot)
    except Exception:
        return False
    layout()
    return True

# ============================== SCREENS ==============================
# Roadmap 7 ("room to grow") in concrete form: a screen is three functions,
# registered by name. enter() paints static chrome and registers hit regions,
# draw() repaints only what changed, tap() returns the name of the screen to go
# to (or None to stay).

SCREENS = {}
screen = None                  # name of the screen currently showing

# Runtime state shared by every screen. Packets are ingested and counters keep
# running no matter which screen is up, so a dashboard never shows numbers that
# stopped advancing while you were looking at something else.
UI_MS = 50                     # text/meter refresh gate for change-driven text
rx_count = 0
pkt_rate = 0
batt_level = 0
batt_v = 0
batt_chg = False
prev_resync = False
last_ui = 0
last_wave = 0
t_boot = 0

def nlinked_now(now):
    n = 0
    for x in sticks:
        if time.ticks_diff(now, x.last) <= STALE_MS:
            n += 1
    return n

def register(name, enter, draw, tap=None):
    SCREENS[name] = (enter, draw, tap)

def go(name):
    """Switch screens: drop all cached field state, repaint from scratch.

    forget-everything is not laziness - the change-driven painters compare
    against what they last drew, and after a screen change that memory
    describes pixels that no longer exist.
    """
    global screen
    screen = name
    _shown.clear()
    _fields.clear()
    hit_clear()
    lcd.fillScreen(BG)
    SCREENS[name][0]()

def back_chip(label="MAIN"):
    """Top-left return control, drawn identically on every sub-screen."""
    f = fit("< " + label, W // 4, HDR_H - 20, 4)
    w, h = tw("< " + label, f) + 24, th(f) + 12
    rrect(L, 8, w, h, 8, FRAME)
    text_at(L + 12, 8 + 6, "< " + label, f, CYAN, BG)
    hit(L - 6, 0, w + 20, h + 20, "main")
    return h + 16

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

def main_enter():
    """Everything that never changes, painted exactly once.

    Borders, fixed labels and the title block used to be repainted on a timer
    along with the values they surround. They are chrome; they get one pass.
    """
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

    # Tap targets. The icons ARE the buttons, as the roadmap asks: the link
    # bars open the developer dashboard and the battery opens the power one.
    # The regions are padded well beyond the drawn glyphs - a 4px-wide signal
    # bar is not a finger-sized target.
    hit(BARS_X - 14, 0, (BAR_W + BAR_GAP) * BARS + 28, BATT_Y + BATT_H + 14,
        "dev")
    hit(BATT_X - 14, 0, BATT_W + 34, BATT_Y + BATT_H + 14, "power")
    hit(L - 8, 0, tw(APP_NAME, F_TITLE) + 24, HDR_H - 4, "menu")
    hit(GX, GY, GW, GH, "fft")          # the waveform opens its spectrum
    # Registered unconditionally. Gating this on "more than one stick" meant
    # the target depended on how many sticks had joined at the instant this
    # screen was entered - so a stick that arrived later never got one.
    hit(VIT_X, TILE_Y, VIT_W, TILE_H, "sensors")

    # The FIFO kept filling while another screen was up; draining it here would
    # dump seconds of backlog across the graph in one frame.
    for st in sticks:
        del st.wave[:]
    forget("heart", "bars", "batt", "tag", "thr", "beattag",
           "bpm", "ibi", "coach", "meter")

def main_draw(now):
    global prev_resync, last_ui, last_wave
    s = primary
    stale = (s is None) or time.ticks_diff(now, s.last) > STALE_MS

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
        draw_link_tag(nlinked_now(now))
        draw_link_bars(pkt_rate)
        draw_battery(batt_level, batt_chg)

    # RESYNC: the stick sets state 7 (and a flag) for ~900ms after BtnA
    rs = bool(s and not stale and (s.resync or s.state == 7))
    if rs != prev_resync:
        if rs:
            draw_resync_banner(True)
        else:
            draw_graph_frame()               # wipe the banner cleanly
        draw_annotations(None if stale else s, force=True)
        prev_resync = rs

register("main", main_enter, main_draw)

# ====================== SHARED SUB-SCREEN CHROME ======================
# Every sub-screen is the same shape: a back chip, a title, and rows of
# label/value pairs that repaint in place like everything else on the main
# dashboard. Building that once keeps the screens themselves short enough to
# read, which is the point of roadmap item 7.

def sub_header(title, subtitle=""):
    """Back chip + title. Returns the y to start content at."""
    y = back_chip()
    f = fit(title, W - 2 * L, HDR_H // 2, 2)
    text_at(L, y, title, f, TEXT, BG)
    y += th(f) + 2
    if subtitle:
        fs = fit(subtitle, W - 2 * L, 40, 5)
        text_at(L, y, subtitle, fs, LABEL, BG)
        y += th(fs)
    y += 10
    lcd.fillRect(L, y, W - 2 * L, 2, FRAME)
    return y + 14

def row(key, x, y, label, value, col=TEXT, wlabel=None, f_l=None, f_v=None):
    """One label/value line. The label is static, the value repaints."""
    f_l = f_l or F_ROW_L
    f_v = f_v or F_ROW_V
    if changed("_lbl" + key, label):
        text_at(x, y, label, f_l, LABEL, BG)
    field(key, x + (wlabel or ROW_LBL_W), y, value, f_v, col, BG)

def bar(x, y, w, h, frac, col, bgcol=METER_OFF):
    frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
    fill = int(w * frac)
    if fill:
        lcd.fillRect(x, y, fill, h, col)
    if fill < w:
        lcd.fillRect(x + fill, y, w - fill, h, bgcol)

def fmt_age(ms):
    if ms < 1000:
        return "%dms" % ms
    if ms < 60000:
        return "%.1fs" % (ms / 1000.0)
    return "%dm%02ds" % (ms // 60000, (ms % 60000) // 1000)

def fmt_uptime(ms):
    s = ms // 1000
    if s < 3600:
        return "%dm %02ds" % (s // 60, s % 60)
    return "%dh %02dm" % (s // 3600, (s % 3600) // 60)

# ====================== ROADMAP 2: DEVELOPER DASHBOARD ======================
# "everything a developer or DIY user wants". The honesty rule from the main
# dashboard applies here too: where a number is not available, say so and say
# why. RSSI is the case that matters - a SoftAP exposes association state, not
# signal strength, so inventing a bar graph for it would be a lie.

DEV_COLS = 2

def dev_enter():
    global DEV_Y0, DEV_ROW_H, DEV_COL_W
    DEV_Y0 = sub_header("DEVELOPER", "link internals, live")
    DEV_ROW_H = th(F_ROW_V) + 10
    DEV_COL_W = (W - 2 * L) // DEV_COLS
    y = DEV_Y0
    # static half of the left column
    text_at(L, y, "TRANSPORT", F_SECT, CYAN, BG)
    y += th(F_SECT) + 8
    ip = "?"
    try:
        ip = _ap.ifconfig()[0]
    except Exception:
        pass
    for label, val in (("ssid", LINK_SSID),
                       ("endpoint", "%s:%d" % (ip, LINK_PORT)),
                       ("protocol", "v%d, %d byte frames" % (LINK_VER,
                                                             PKT_LEN)),
                       ("rssi", "n/a - SoftAP exposes no RSSI")):
        text_at(L, y, label, F_ROW_L, LABEL, BG)
        text_at(L + ROW_LBL_W, y, val, F_ROW_V, TEXT, BG)
        y += DEV_ROW_H
    global DEV_LIVE_Y
    DEV_LIVE_Y = y + 6
    text_at(L, DEV_LIVE_Y, "COUNTERS", F_SECT, CYAN, BG)
    global DEV_STICK_Y
    DEV_STICK_Y = DEV_Y0
    text_at(L + DEV_COL_W, DEV_Y0, "SENSORS", F_SECT, CYAN, BG)

def dev_draw(now):
    y = DEV_LIVE_Y + th(F_SECT) + 8
    rate_col = GREEN if pkt_rate >= PKT_HZ_EXPECTED - 3 else (
        YELLOW if pkt_rate else RED)
    row("d_rate", L, y, "packet rate", "%d/s  (expect %d/s)"
        % (pkt_rate, PKT_HZ_EXPECTED), rate_col)
    y += DEV_ROW_H
    row("d_rx", L, y, "accepted", "%d" % rx_count)
    y += DEV_ROW_H
    row("d_bad", L, y, "rejected", "%d" % rx_bad,
        RED if rx_bad else LABEL)
    y += DEV_ROW_H
    row("d_up", L, y, "uptime", fmt_uptime(time.ticks_diff(now, t_boot)))
    y += DEV_ROW_H
    row("d_mem", L, y, "free heap", "%d KB" % (gc.mem_free() // 1024))
    y += DEV_ROW_H
    row("d_rot", L, y, "rotation", "%d  (%s)"
        % (cur_rot, "auto" if AUTO_ROTATE else "fixed"))
    y += DEV_ROW_H
    n = ap_stations()
    row("d_sta", L, y, "ap stations",
        "?" if n < 0 else str(n), LABEL if n <= 0 else GREEN)
    y += DEV_ROW_H
    # A recovery nobody can see is as hard to diagnose as a silent failure.
    row("d_rec", L, y, "link recovery",
        "%d socket, %d ap restart" % (sock_rebuilds, ap_restarts),
        YELLOW if (sock_rebuilds or ap_restarts) else LABEL)

    # right column: one block per stick that has ever been seen
    y = DEV_STICK_Y + th(F_SECT) + 8
    x = L + DEV_COL_W
    for i, st in enumerate(sticks[:4]):
        age = time.ticks_diff(now, st.last)
        live = age <= STALE_MS
        row("s%dh" % i, x, y, "sensor %d" % (i + 1), st.dev.hex().upper(),
            GREEN if live else LABEL)
        y += DEV_ROW_H
        row("s%dr" % i, x, y, "  packets", "%d" % st.rx)
        y += DEV_ROW_H
        row("s%da" % i, x, y, "  last seen", fmt_age(age),
            GREEN if live else RED)
        y += DEV_ROW_H + 6
    if not sticks:
        row("s_none", x, y, "", "no sensors seen yet", LABEL)

register("dev", dev_enter, dev_draw)

# ====================== ROADMAP 3: POWER DASHBOARD ======================
# State of charge, charge/discharge rate, cell voltage, and an estimated
# runtime derived from the MEASURED slope of the state of charge rather than
# from a battery capacity nobody has told us.
#
# Every reading here goes through batt_sample()'s filter. The raw gauge on this
# board reports one cell of the 2S pack every few seconds; a power screen
# driven straight off M5.Power would swing between full and flat while you
# watched it.

PWR_HIST_MAX = 180             # 5s cadence -> 15 minutes of history
pwr_hist = []                  # (t_ms, soc, mv, ma)

def pwr_sample(now):
    ma = 0
    try:
        ma = int(M5.Power.getBatteryCurrent())
    except Exception:
        pass
    pwr_hist.append((now, batt_level, batt_v, ma))
    if len(pwr_hist) > PWR_HIST_MAX:
        del pwr_hist[0:len(pwr_hist) - PWR_HIST_MAX]

def pwr_runtime_estimate():
    """Hours remaining from the measured SOC slope, or None if not yet known.

    Deliberately NOT computed from a datasheet capacity: this pack's capacity
    is not reported by the gauge, and a made-up mAh figure would produce a
    confident, wrong number. The slope of the filtered SOC over the observed
    window is something we actually measured.
    """
    if len(pwr_hist) < 12:
        return None
    t0, soc0 = pwr_hist[0][0], pwr_hist[0][1]
    t1, soc1 = pwr_hist[-1][0], pwr_hist[-1][1]
    dt_h = time.ticks_diff(t1, t0) / 3600000.0
    if dt_h <= 0.01:
        return None
    drop = soc0 - soc1
    if drop <= 0:
        return None                # charging, or flat: no discharge to project
    return soc1 / (drop / dt_h)

def pwr_enter():
    global PWR_Y0, PWR_ROW_H, PWR_G
    PWR_Y0 = sub_header("POWER", "filtered gauge - raw reading flaps by design")
    PWR_ROW_H = th(F_ROW_V) + 10
    y = PWR_Y0
    text_at(L, y, "PACK", F_SECT, CYAN, BG)
    # graph box for the SOC history
    gw = W - 2 * L
    gh = max(90, (H - PWR_Y0) // 3)
    gy = H - SAFE - gh
    PWR_G = (L, gy, gw, gh)
    rrect(L - 2, gy - 2, gw + 4, gh + 4, 8, FRAME)
    text_at(L + 6, gy - th(F_ROW_L) - 6, "state of charge, last 15 min",
            F_ROW_L, LABEL, BG)

def pwr_draw(now):
    y = PWR_Y0 + th(F_SECT) + 8
    col = GREEN if batt_level > 50 else (YELLOW if batt_level > 20 else RED)
    row("p_soc", L, y, "charge", "%d%%" % batt_level, col)
    y += PWR_ROW_H
    row("p_v", L, y, "pack voltage", "%d mV  (%.2f V/cell)"
        % (batt_v, batt_v / 2000.0))
    y += PWR_ROW_H
    ma = pwr_hist[-1][3] if pwr_hist else 0
    row("p_i", L, y, "current",
        "%+d mA  %s" % (ma, "charging" if batt_chg else "discharging"),
        CYAN if batt_chg else TEXT)
    y += PWR_ROW_H
    vb = "?"
    try:
        v = M5.Power.getVBUSVoltage()
        vb = "not present" if v is None or v < 0 else "%d mV" % v
    except Exception:
        pass
    row("p_vbus", L, y, "usb rail", vb)
    y += PWR_ROW_H
    est = pwr_runtime_estimate()
    row("p_eta", L, y, "runtime left",
        "measuring..." if est is None else "%dh %02dm (measured slope)"
        % (int(est), int((est % 1) * 60)),
        LABEL if est is None else TEXT)
    y += PWR_ROW_H
    row("p_raw", L, y, "raw gauge", "%d%% (%d mV) - unfiltered"
        % (batt_raw[0], batt_raw[1]), LABEL)

    # SOC history graph, redrawn only when a new sample lands
    if changed("p_graph", len(pwr_hist)) and len(pwr_hist) > 1:
        gxx, gyy, gww, ghh = PWR_G
        lcd.fillRect(gxx, gyy, gww, ghh, BG)
        for k in range(1, 4):
            lcd.fillRect(gxx, gyy + ghh * k // 4, gww, 1, GRID)
        n = len(pwr_hist)
        px = py = None
        for i in range(n):
            xx = gxx + (i * (gww - 1)) // max(1, n - 1)
            yy = gyy + ghh - 1 - (pwr_hist[i][1] * (ghh - 2)) // 100
            if px is not None:
                lcd.drawLine(px, py, xx, yy, GREEN)
            px, py = xx, yy

register("power", pwr_enter, pwr_draw)

# ====================== ROADMAP 4: MULTIPLE SENSORS ======================
# 2-4 PulseSensors across multiple sticks. The roadmap asks specifically that a
# sensor's BPM and IBI sit together in one window, which is the same rule the
# main dashboard follows - so each sensor gets its own row shaped like the
# vitals panel, not a grid of loose numbers.

SENSOR_MAX = 4

def sensors_enter():
    global SEN_Y0, SEN_ROW_H, SEN_F_VAL, SEN_F_LBL
    SEN_Y0 = sub_header("SENSORS", "one row per stick, BPM and IBI together")
    avail = H - SAFE - SEN_Y0
    SEN_ROW_H = avail // SENSOR_MAX
    SEN_F_LBL = fit("BPM", 200, 40, 5)
    SEN_F_VAL = fit("8888", (W // 3) - 40, SEN_ROW_H - th(SEN_F_LBL) - 22, 1)
    for i in range(SENSOR_MAX):
        y = SEN_Y0 + i * SEN_ROW_H
        rrect(L, y, W - 2 * L, SEN_ROW_H - 10, 10, FRAME)

def sensors_draw(now):
    for i in range(SENSOR_MAX):
        y = SEN_Y0 + i * SEN_ROW_H
        st = sticks[i] if i < len(sticks) else None
        live = st is not None and time.ticks_diff(now, st.last) <= STALE_MS
        if st is None:
            if changed("sen%dn" % i, "empty"):
                field("sen%dn" % i, L + 16, y + 12, "-- no sensor --",
                      SEN_F_LBL, METER_OFF, BG)
                forget("sen%dv" % i)     # so it repaints if a stick arrives
            continue
        col = GREEN if (live and st.state == LOCKED_STATE) else (
            YELLOW if live else LABEL)
        name = "%s  %s" % (st.dev.hex().upper(),
                           STATE_NAMES[st.state]
                           if st.state < len(STATE_NAMES) else "?")
        if changed("sen%dn" % i, (name, col)):
            field("sen%dn" % i, L + 16, y + 10, name, SEN_F_LBL, col, BG)
        vy = y + 10 + th(SEN_F_LBL) + 4
        bpm = str(st.bpm) if (live and st.bpm) else "--"
        ibi = str(st.ibi) if (live and st.ibi) else "--"
        # Values are RIGHT-aligned to a fixed edge and the unit sits just past
        # it. Left-aligning them meant the unit had to be parked where a
        # four-digit number would end, so "72" left a visible hole - and
        # tracking the actual width instead would make the unit jitter every
        # time the value gained or lost a digit.
        vw = tw("8888", SEN_F_VAL)
        ly = vy + th(SEN_F_VAL) - th(SEN_F_LBL) - 4      # sit on the baseline
        xb = L + 30 + vw
        xi = L + (W - 2 * L) // 2 + vw
        if changed("sen%dv" % i, (bpm, ibi, col)):
            field("sen%dbp" % i, 0, vy, bpm, SEN_F_VAL, col, BG, right=xb)
            field("sen%dbl" % i, xb + 14, ly, "BPM", SEN_F_LBL, LABEL, BG)
            field("sen%dib" % i, 0, vy, ibi, SEN_F_VAL, col, BG, right=xi)
            field("sen%dil" % i, xi + 14, ly, "IBI ms", SEN_F_LBL, LABEL, BG)

register("sensors", sensors_enter, sensors_draw)

# ================== ROADMAP 6: FFT SPECTRUM ANALYZER ==================
# An FFT over the live PPG, with the repeating harmonics labelled and the axis
# marked in both Hz ("times per second") and BPM.
#
# N_FFT=512 at the 50Hz effective sample rate is 10.24s of signal and 0.098Hz
# of resolution - 5.9 BPM per bin. 256 would halve the cost but land at 11.7
# BPM per bin, which is too coarse to separate a resting heart rate from its
# own neighbours. The transform costs real time (thousands of butterflies in
# interpreted Python), so it runs at most every FFT_MS and ONLY while this
# screen is showing; nothing else on the device is time-critical here.

N_FFT = 512
FFT_MS = 1500
BPM_AXIS_MAX = 240             # 4Hz: above any plausible heart rate
FFT_SELFTEST = True            # time + verify one transform at boot
SCREEN_SELFTEST = False        # paint every screen once at boot; see BOOT

_tw_re = None
_tw_im = None

def fft_init():
    """Precompute twiddles once. Calling cos/sin inside the butterfly loop
    would mean thousands of trig calls per transform."""
    global _tw_re, _tw_im
    if _tw_re is not None:
        return
    half = N_FFT // 2
    _tw_re = array("f", [0.0] * half)
    _tw_im = array("f", [0.0] * half)
    for k in range(half):
        a = -2.0 * math.pi * k / N_FFT
        _tw_re[k] = math.cos(a)
        _tw_im[k] = math.sin(a)

def fft_mag(vals):
    """Magnitude spectrum of the newest N_FFT samples. Returns N_FFT//2 bins."""
    fft_init()
    n = N_FFT
    re = array("f", [0.0] * n)
    im = array("f", [0.0] * n)
    base = len(vals) - n
    mean = 0.0
    for i in range(n):
        mean += vals[base + i]
    mean /= n
    # Hann window: without it the DC/low-frequency leakage from a PPG's large
    # baseline wander smears across the whole spectrum and buries the peak.
    for i in range(n):
        w = 0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1))
        re[i] = (vals[base + i] - mean) * w

    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            re[i], re[j] = re[j], re[i]
            im[i], im[j] = im[j], im[i]

    size = 2
    while size <= n:
        half = size >> 1
        stride = n // size
        for i in range(0, n, size):
            k = 0
            for m in range(half):
                a = i + m
                b = a + half
                wr = _tw_re[k]
                wi = _tw_im[k]
                tr = wr * re[b] - wi * im[b]
                ti = wr * im[b] + wi * re[b]
                re[b] = re[a] - tr
                im[b] = im[a] - ti
                re[a] += tr
                im[a] += ti
                k += stride
        size <<= 1

    out = []
    for i in range(n >> 1):
        out.append(math.sqrt(re[i] * re[i] + im[i] * im[i]))
    return out

fft_last = 0
fft_bins = None
fft_peak = None                # (bin, hz, bpm)

def fft_enter():
    global FFT_G, FFT_INFO_Y
    # The x-axis unit lives in the subtitle, not under the axis: a "BPM" label
    # at the right-hand end would land on top of the 240 tick.
    y = sub_header("SPECTRUM",
                   "FFT of the live PPG - %d pt, %.2f Hz per bin, "
                   "x axis in BPM" % (N_FFT, float(SAMPLE_HZ) / N_FFT))
    FFT_INFO_Y = y
    gy = y + th(F_ROW_V) + 16
    # Leave exactly enough room below the graph for the tick marks and their
    # labels; deriving it the other way round ran the labels off the panel.
    gh = (H - SAFE) - (9 + th(F_ROW_L)) - gy
    FFT_G = (L, gy, W - 2 * L, gh)
    rrect(L - 2, gy - 2, W - 2 * L + 4, gh + 4, 8, FRAME)
    gx0, _gy, gw, _gh = FFT_G
    for b in range(0, BPM_AXIS_MAX + 1, 30):
        xx = gx0 + (b * (gw - 1)) // BPM_AXIS_MAX
        lcd.fillRect(xx, gy + gh + 2, 1, 6, LABEL)
        lab = str(b)
        lx = clamp(xx - tw(lab, F_ROW_L) // 2, gx0, gx0 + gw - tw(lab, F_ROW_L))
        text_at(lx, gy + gh + 9, lab, F_ROW_L, LABEL, BG)

def fft_draw(now):
    global fft_last, fft_bins, fft_peak
    s = primary
    have = s is not None and len(s.hist) >= N_FFT
    if not have:
        n = 0 if s is None else len(s.hist)
        row("f_wait", L, FFT_INFO_Y, "collecting",
            "%d / %d samples (%.1fs to go)"
            % (n, N_FFT, max(0.0, (N_FFT - n) / float(SAMPLE_HZ))), LABEL)
        return
    if time.ticks_diff(now, fft_last) >= FFT_MS:
        fft_last = now
        fft_bins = fft_mag(s.hist)
        # Search only the physiologically plausible band. Bin 0-2 is baseline
        # wander and would otherwise win every time.
        lo = int(0.5 * N_FFT / SAMPLE_HZ)
        hi = int((BPM_AXIS_MAX / 60.0) * N_FFT / SAMPLE_HZ)
        pk, pv = lo, 0.0
        for i in range(lo, min(hi, len(fft_bins))):
            if fft_bins[i] > pv:
                pv, pk = fft_bins[i], i
        hz = pk * float(SAMPLE_HZ) / N_FFT
        fft_peak = (pk, hz, hz * 60.0)
        draw_spectrum()
    if fft_peak:
        row("f_pk", L, FFT_INFO_Y, "fundamental",
            "%.2f Hz  =  %.0f BPM   (detector says %s)"
            % (fft_peak[1], fft_peak[2], s.bpm if s.bpm else "--"), GREEN)

def draw_spectrum():
    gx0, gy0, gw, gh = FFT_G
    lcd.fillRect(gx0, gy0, gw, gh, BG)
    if not fft_bins:
        return
    hi = int((BPM_AXIS_MAX / 60.0) * N_FFT / SAMPLE_HZ)
    hi = min(hi, len(fft_bins))
    lo = 1
    peak = 0.0
    for i in range(lo, hi):
        if fft_bins[i] > peak:
            peak = fft_bins[i]
    if peak <= 0:
        return
    for i in range(lo, hi):
        xx = gx0 + ((i - lo) * (gw - 1)) // max(1, hi - lo - 1)
        hgt = int((fft_bins[i] / peak) * (gh - 2))
        if hgt > 0:
            lcd.fillRect(xx, gy0 + gh - hgt, 2, hgt, GREEN)
    # Harmonics: a real pulse repeats, so its spectrum has evenly spaced
    # peaks. Marking them is how you tell a heartbeat from a noise spike -
    # noise has no harmonic series.
    if fft_peak:
        pk = fft_peak[0]
        for h in range(1, 5):
            b = pk * h
            if b >= hi:
                break
            xx = gx0 + ((b - lo) * (gw - 1)) // max(1, hi - lo - 1)
            col = CYAN if h == 1 else YELLOW
            for yy in range(gy0, gy0 + gh, 6):
                lcd.drawPixel(xx, yy, col)
            lab = "f" if h == 1 else "%dx" % h
            text_at(xx + 3, gy0 + 2, lab, F_ROW_L, col, BG)

register("fft", fft_enter, fft_draw)

# ====================== ROADMAP 5: APP MENU ======================

MENU_ITEMS = (("DASHBOARD", "live waveform, BPM, IBI, signal", "main"),
              ("SENSORS", "every stick, BPM + IBI together", "sensors"),
              ("SPECTRUM", "FFT of the PPG, harmonics labelled", "fft"),
              ("DEVELOPER", "packet rate, counters, per-stick MACs", "dev"),
              ("POWER", "charge, current, measured runtime", "power"))

def menu_enter():
    y = sub_header("APPS", "tap to open")
    n = len(MENU_ITEMS)
    avail = H - SAFE - y
    rh = avail // n
    f_t = fit("DASHBOARD", W // 2, rh - 30, 3)
    f_s = fit(MENU_ITEMS[3][1], W - 2 * L - 40, 34, 5)
    for i, (title, sub, target) in enumerate(MENU_ITEMS):
        ry = y + i * rh
        rrect(L, ry, W - 2 * L, rh - 8, 10, FRAME)
        text_at(L + 22, ry + 8, title, f_t, TEXT, BG)
        text_at(L + 22, ry + 8 + th(f_t), sub, f_s, LABEL, BG)
        hit(L, ry, W - 2 * L, rh - 8, target)

def menu_draw(now):
    pass

register("menu", menu_enter, menu_draw)

# ============================== BOOT ==============================

layout()
lcd.fillScreen(BG)
cur_rot = 1
try:
    cur_rot = lcd.getRotation()
except Exception:
    pass

batt_level, batt_v, batt_chg = batt_sample()
t_boot = time.ticks_ms()
last_ui = last_wave = last_rx = t_boot
go("main")

print("pulselink_tab5: %dx%d dashboard, waiting for sticks" % (W, H))
# Printed so the layout can be checked against MEASURED font metrics from the
# serial log alone - this app has no way to look at its own screen.
print("LAYOUT: vitals x=%d w=%d half=%d | sig x=%d w=%d" %
      (VIT_X, VIT_W, VIT_HALF, SIGP_X, SIG_W))
print("LAYOUT: value face h=%d  '8888' w=%d  fits %d  (y=%d)" %
      (th(F_VAL), tw("8888", F_VAL), VAL_MAX_W, VAL_Y))
print("LAYOUT: coach face h=%d  longest w=%d  fits %d x %d" %
      (th(F_COACH), max([tw(t, F_COACH) for t in COACH_STRINGS]),
       COACH_MAX_W, COACH_MAX_H))
print("SCREENS: %s   rot=%d auto=%s" %
      (",".join(sorted(SCREENS)), cur_rot, AUTO_ROTATE))

btn_escape = True
try:
    M5.BtnPWR.wasClicked()
except Exception as _ex:
    btn_escape = False
    print("INPUT: BtnPWR escape unavailable (%s) - back chip only" % _ex)

# Every sub-screen is reachable only by TAPPING the panel, so on a desk far
# from the device they are unverifiable. tools/sim_tab5.py covers them, but it
# stubs the LCD - a method that exists in the stub and not in this firmware
# would pass the simulator and crash the moment a user tapped. Flipping this on
# paints each screen once on the real panel and reports what happened.
# Left OFF by default: it flashes every screen at the user on every boot.
if SCREEN_SELFTEST:
    for _name in sorted(SCREENS):
        try:
            go(_name)
            SCREENS[_name][1](time.ticks_ms())
            print("SELFTEST: %-8s ok (%d hit regions)" % (_name, len(_hits)))
        except Exception as _ex:
            print("SELFTEST: %-8s RAISED %r" % (_name, _ex))
    go("main")

# One FFT at boot, timed. The spectrum screen is only reachable by TAPPING the
# panel, so without this nobody finds out how long the transform takes on this
# chip until a user is already looking at it - and a transform slow enough to
# starve the task watchdog would reboot the board rather than draw a graph.
# The self-test also proves the maths on the real hardware, not just on a host.
if FFT_SELFTEST:
    _t0 = time.ticks_ms()
    _sig = []
    for _i in range(N_FFT):
        _sig.append(int(512 + 200 * math.sin(2 * math.pi * 1.2 *
                                             (_i / float(SAMPLE_HZ)))))
    _b = fft_mag(_sig)
    _lo = int(0.5 * N_FFT / SAMPLE_HZ)
    _pk, _pv = _lo, 0.0
    for _i in range(_lo, len(_b)):
        if _b[_i] > _pv:
            _pv, _pk = _b[_i], _i
    _ms = time.ticks_diff(time.ticks_ms(), _t0)
    print("FFT: %d-pt in %dms, 1.20Hz -> %.2fHz (bin %d) %s"
          % (N_FFT, _ms, _pk * float(SAMPLE_HZ) / N_FFT, _pk,
             "OK" if abs(_pk * float(SAMPLE_HZ) / N_FFT - 1.2) <=
             float(SAMPLE_HZ) / N_FFT else "WRONG"))
    del _sig, _b

last_stat = time.ticks_ms()
rate_mark = time.ticks_ms()     # packet-rate window for the signal bars
rate_base = 0
last_batt = time.ticks_ms()
last_pwr = time.ticks_ms()
last_rot = time.ticks_ms()

while True:
    now = time.ticks_ms()
    M5.update()                 # the ONLY call: twice a pass eats button edges

    # --- ingest, on every screen. A counter that stops advancing because you
    # were looking at another screen is worse than no counter at all.
    for p in link_poll():
        rx_count += 1
        last_rx = now
        _stall_strikes = 0          # traffic is flowing: rearm the watchdog
        s = stick_for(p["dev"])
        s.rx += 1
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
        s.hist.append(sm[0])
        s.hist.append(sm[1])
        if len(s.hist) > HIST_MAX:
            del s.hist[0:len(s.hist) - HIST_MAX]
        s.resync = p["resync"]
        s.smin = p["smin"]; s.smax = p["smax"]
        s.thresh = p["thresh"]; s.amp = p["amp"]
        s.last = now

    # --- input
    tap = poll_tap(now)
    if tap is not None:
        act = hit_test(tap[0], tap[1])
        if act is not None and act in SCREENS and act != screen:
            print("TAP: %d,%d -> %s" % (tap[0], tap[1], act))
            go(act)
    # BtnPWR is the hardware escape hatch back to the dashboard, in case a
    # sub-screen ever paints over its own back chip. Probed once at boot:
    # raising AttributeError every 5ms pass would cost more than the feature.
    if btn_escape and screen != "main":
        try:
            if M5.BtnPWR.wasClicked():
                go("main")
        except Exception:
            btn_escape = False

    # --- auto-rotate (roadmap 1)
    if AUTO_ROTATE and time.ticks_diff(now, last_rot) >= ROT_POLL_MS:
        last_rot = now
        want = read_orientation()
        if want is None or want == cur_rot:
            _rot_votes = 0
        else:
            if want == _rot_want:
                _rot_votes += 1
            else:
                _rot_want, _rot_votes = want, 1
            # Hysteresis: a tablet being picked up swings through every
            # orientation on the way. Turning on the first sample would make
            # the screen spin in your hands.
            if _rot_votes >= ROT_STABLE_N:
                _rot_votes = 0
                if apply_rotation(want):
                    cur_rot = want
                    print("ROT: -> %d (%dx%d)" % (cur_rot, W, H))
                    go(screen)          # rebuild the current screen at new size

    # --- draw whichever screen is up
    SCREENS[screen][1](now)

    link_watchdog(now)

    # measured packet rate over a 1s window -> signal bars
    if time.ticks_diff(now, rate_mark) >= 1000:
        pkt_rate = rx_count - rate_base
        rate_base = rx_count
        rate_mark = now

    # Gauge sampled at 1Hz and de-glitched over 8 samples; see batt_sample().
    if time.ticks_diff(now, last_batt) >= 1000:
        last_batt = now
        batt_level, batt_v, batt_chg = batt_sample()

    if time.ticks_diff(now, last_pwr) >= 5000:
        last_pwr = now
        pwr_sample(now)

    if time.ticks_diff(now, last_stat) >= 5000:
        last_stat = now
        s = primary
        # batt shows filtered/raw so the de-glitching can be seen working
        # rather than assumed - raw is expected to flap, filtered is not.
        # up= and free= are here to make an unexplained reboot or a slow leak
        # legible from a passive serial log: uptime resetting IS the reboot
        # signal when the reset itself scrolls past unseen.
        print("up=%ds scr=%s rx=%d bad=%d rate=%d/s linked=%d bpm=%s "
              "state=%s batt=%d%%(%dmV) raw=%d%%(%dmV) free=%d"
              % (time.ticks_diff(now, t_boot) // 1000, screen,
                 rx_count, rx_bad, pkt_rate, nlinked_now(now),
                 s.bpm if s else "-", s.state if s else "-",
                 batt_level, batt_v, batt_raw[0], batt_raw[1],
                 gc.mem_free()))

    time.sleep_ms(5)          # always yield: never starve the task watchdog
