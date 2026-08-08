# tab5_pulse.py — PulseSensor dashboard for the M5Stack Tab5 (ESP32-P4).
#
# A remote display for M5StickS3 sensors, styled after the PulseSensor CYD
# dashboard (pulsesensor.com/pages/cyd) and scaled up to 1280x720:
#
#   +--------------------------------------------------------------+
#   | PulseSensor.com        <3        QUALIFIED BEAT      1 LINKED |
#   | Tab5 remote display                                           |
#   +--------------------------------------------------------------+
#   |                                              THR 550          |
#   |        live waveform, dotted threshold line                   |
#   |                                              LED BEAT         |
#   +--------------------------------------------------------------+
#   |   BPM 121   |   IBI 550 ms   |   SIG  ||||||||||              |
#   +--------------------------------------------------------------+
#
# CYD semantics, kept faithfully:
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
PKT_LEN = 24                   # v3: 2 waveform samples + flags
STALE_MS = 2500

PKT_HZ_EXPECTED = 25           # link quality is measured against this
APP_NAME = "PulseSensor.com"
APP_SUB = "Tab5 remote display"

# --- CYD palette ---
BG        = 0x000000
GRID      = 0x1E4A32           # graph grid
FRAME     = 0x37D871           # panel borders, CYD green
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
FONT_STACK = (_F.DejaVu72, _F.DejaVu56, _F.DejaVu40, _F.DejaVu24,
              _F.DejaVu18, _F.DejaVu12, _F.DejaVu9)

def use_font(f):
    lcd.setFont(f)
    lcd.setTextSize(1)

def tw(s, f=None):
    if f is not None:
        use_font(f)
    return lcd.textWidth(s)

def th(f=None):
    if f is not None:
        use_font(f)
    return lcd.fontHeight()

def text_at(x, y, s, f, fg, bg):
    use_font(f)
    lcd.setTextColor(fg, bg)
    lcd.setCursor(int(x), int(y))
    lcd.print(s)

def fit(s, max_w, max_h, start_idx=0):
    """Biggest real font that fits the box. Never assume character cells."""
    for f in FONT_STACK[start_idx:]:
        if tw(s, f) <= max_w and th(f) <= max_h:
            return f
    return FONT_STACK[-1]

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
F_TITLE = fit(APP_NAME, W // 3, HDR_H // 2)
F_SUB = fit(APP_SUB, W // 3, HDR_H // 3, 3)
F_STATUS = fit("QUALIFIED BEAT", W // 3, HDR_H // 2, 2)
F_TAG = fit("WAITING FOR STICK", W // 3, HDR_H // 3, 4)
F_TILE_LBL = fit("BPM", 200, 60, 3)
F_ANNOT = fit("LED BEAT", 260, 50, 3)

HEART_R = max(18, HDR_H // 3)
HEART_X = W // 2
HEART_Y = HDR_H // 2

TILE_H = max(120, H // 5)                  # bottom readouts
TILE_Y = H - SAFE - TILE_H
TILE_GAP = SAFE
_avail = R - L - 2 * TILE_GAP
BPM_W = (_avail * 46) // 100               # BPM is the headline number
SIDE_W = (_avail - BPM_W) // 2
TILE_X = (L, L + BPM_W + TILE_GAP, L + BPM_W + TILE_GAP + SIDE_W + TILE_GAP)
TILE_WS = (BPM_W, SIDE_W, SIDE_W)

GX = L + 2                                  # waveform window
GY = HDR_H + SAFE
GW = (R - 2) - GX
GH = TILE_Y - SAFE - GY
GRID_X = max(40, GW // 12)
GRID_Y = max(30, GH // 5)

# The stick samples at 50Hz and sends every 2nd sample, so packets arrive at
# 25Hz. One pixel per packet would span ~50s of history across this wide
# screen; scale x so the window holds exactly WAVE_SECONDS.
WAVE_SECONDS = 5
PKT_HZ = 25
PTS_PER_PKT = 2                            # stick batches 2 samples per packet
WAVE_SAMPLES = WAVE_SECONDS * PKT_HZ * PTS_PER_PKT
X_STEP = max(1, GW // WAVE_SAMPLES)        # ~5px: smooth, not angular

# ============================== STATE ==============================

class Stick:
    __slots__ = ("dev", "bpm", "ibi", "quality", "state", "beat", "resync",
                 "sig", "samples", "smin", "smax", "thresh", "amp", "last")

    def __init__(self, dev):
        self.dev = dev
        self.bpm = self.ibi = self.quality = self.amp = 0
        self.state = 2
        self.beat = False
        self.resync = False
        self.sig = 512
        self.samples = (512, 512)
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
    """CYD: yellow while acquiring, green once the quality meter locks."""
    return GREEN if (s and s.state == LOCKED_STATE) else YELLOW

def draw_heart(cx, cy, r, col):
    lcd.fillCircle(cx - r // 2, cy - r // 3, r // 2 + 1, col)
    lcd.fillCircle(cx + r // 2, cy - r // 3, r // 2 + 1, col)
    lcd.fillTriangle(cx - r, cy - r // 4, cx + r, cy - r // 4, cx, cy + r, col)

def draw_heart_area(s):
    beat = bool(s and s.beat)
    r = HEART_R if beat else (HEART_R * 3) // 4
    lcd.fillRect(HEART_X - HEART_R - 3, 0, (HEART_R + 3) * 2, HDR_H - 3, BG)
    draw_heart(HEART_X, HEART_Y, r, RED if beat else RED_DIM)

BATT_W = max(34, W // 32)
BATT_H = max(16, HDR_H // 4)
BATT_X = R - BATT_W - 4
BATT_Y = 6
SIG_W = 4
SIG_GAP = 3
SIG_BARS = 4
SIG_X = BATT_X - 12 - (SIG_W + SIG_GAP) * SIG_BARS
SIG_Y = BATT_Y

def draw_signal(rate):
    """Link-quality bars from the ACTUAL packet rate.

    The AP interface exposes connected station MACs but no RSSI, so radio
    signal strength is not available. Packet rate against the expected 25/s
    is a real, honest measure of link health - not a guess at RSSI.
    """
    frac = 0.0 if rate <= 0 else min(1.0, rate / float(PKT_HZ_EXPECTED))
    lit = int(frac * SIG_BARS + 0.5)
    col = GREEN if frac >= 0.8 else (YELLOW if frac >= 0.4 else CYAN)
    lcd.fillRect(SIG_X, SIG_Y, (SIG_W + SIG_GAP) * SIG_BARS, BATT_H, BG)
    for i in range(SIG_BARS):
        h = 5 + i * ((BATT_H - 5) // (SIG_BARS - 1))
        lcd.fillRect(SIG_X + i * (SIG_W + SIG_GAP), SIG_Y + BATT_H - h,
                     SIG_W, h, col if i < lit else GRID)

def draw_battery(level, volts, charging):
    """Level bar when the gauge reports one; an EXT pill when it does not.

    Tab5 returned getBatteryLevel()=0 with 5482mV present, i.e. running from
    the USB rail. Drawing an empty battery there would be a lie, so external
    power gets its own explicit indicator.
    """
    lcd.fillRect(BATT_X - 2, BATT_Y - 2, BATT_W + 8, BATT_H + 4, BG)
    if level and level > 0:
        col = GREEN if level > 50 else (YELLOW if level > 20 else RED)
        lcd.drawRect(BATT_X, BATT_Y, BATT_W, BATT_H, LABEL)
        lcd.fillRect(BATT_X + BATT_W, BATT_Y + BATT_H // 3, 3, BATT_H // 3, LABEL)
        fill = (min(100, level) * (BATT_W - 4)) // 100
        if fill:
            lcd.fillRect(BATT_X + 2, BATT_Y + 2, fill, BATT_H - 4,
                         CYAN if charging else col)
    else:
        lcd.drawRect(BATT_X, BATT_Y, BATT_W, BATT_H, LABEL)
        lcd.fillRect(BATT_X + BATT_W, BATT_Y + BATT_H // 3, 3, BATT_H // 3, LABEL)
        f = fit("EXT", BATT_W - 6, BATT_H - 4, 4)
        text_at(BATT_X + (BATT_W - tw("EXT", f)) // 2,
                BATT_Y + (BATT_H - th(f)) // 2, "EXT", f, CYAN, BG)

def read_power():
    try:
        return (M5.Power.getBatteryLevel(), M5.Power.getBatteryVoltage(),
                bool(M5.Power.isCharging()))
    except Exception:
        return (0, 0, False)

def draw_header(s, nlinked):
    lcd.fillRect(0, 0, W, HDR_H, BG)
    lcd.fillRect(0, HDR_H - 2, W, 2, FRAME)
    text_at(L, HDR_H // 2 - th(F_TITLE) - 2, APP_NAME, F_TITLE, TEXT, BG)
    text_at(L, HDR_H // 2 + 4, APP_SUB, F_SUB, LABEL, BG)

    if s is None:
        name, col = "WAITING FOR STICK", CYAN
    else:
        name = STATE_NAMES[s.state] if s.state < len(STATE_NAMES) else "?"
        col = GREEN if s.state == LOCKED_STATE else YELLOW
    f = fit(name, W // 3, HDR_H // 2, 2)
    text_at(SIG_X - 12 - tw(name, f), HDR_H // 2 - th(f) - 2, name, f, col, BG)

    tag = "%d LINKED" % nlinked if nlinked else "NO LINK"
    text_at(SIG_X - 12 - tw(tag, F_TAG), HDR_H // 2 + 6, tag, F_TAG,
            GREEN if nlinked else LABEL, BG)
    draw_heart_area(s)

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

def thresh_y(s):
    lo, hi = (s.smin, s.smax) if s else (0, 1023)
    if hi <= lo:
        hi = lo + 1
    t = s.thresh if s else 550
    return clamp(mapv(t, lo, hi, GY + GH - 6, GY + 6), GY + 6, GY + GH - 6)

def draw_annotations(s):
    """THR readout + LED BEAT tag, CYD-style, inside the graph."""
    thr = "THR %d" % (s.thresh if s else 0)
    wpx = tw(thr, F_ANNOT)
    lcd.fillRect(GX + GW - wpx - 16, GY + 4, wpx + 14, th(F_ANNOT) + 4, BG)
    text_at(GX + GW - wpx - 8, GY + 6, thr, F_ANNOT, TEXT, BG)
    tag = "LED BEAT"
    wpx = tw(tag, F_ANNOT)
    ty = GY + GH - th(F_ANNOT) - 8
    lcd.fillRect(GX + GW - wpx - 16, ty - 2, wpx + 14, th(F_ANNOT) + 4, BG)
    text_at(GX + GW - wpx - 8, ty, tag, F_ANNOT, wave_color(s), BG)

def clear_column(cx, s):
    x = GX + cx
    lcd.fillRect(x, GY, 1, GH, GRID if (cx % GRID_X == 0) else BG)
    for y in range(GRID_Y, GH, GRID_Y):
        lcd.drawPixel(x, GY + y, GRID)
    if cx % 8 < 4:                          # dotted threshold line
        lcd.drawPixel(x, thresh_y(s), DOT)

def draw_wave(s):
    """Draw every sample in the batch, so the trace stays smooth."""
    global gx, last_gy
    lo, hi = s.smin, s.smax
    if hi <= lo:
        hi = lo + 1
    col = wave_color(s)
    for sample in s.samples:
        y = clamp(mapv(sample, lo, hi, GY + GH - 6, GY + 6),
                  GY + 6, GY + GH - 6)
        for k in range(X_STEP * 2):             # erase the span ahead
            clear_column((gx + k) % GW, s)
        if gx:
            x0, x1 = GX + gx - X_STEP, GX + gx
            lcd.drawLine(x0, last_gy, x1, y, col)
            lcd.drawLine(x0, last_gy + 1, x1, y + 1, col)
            lcd.drawLine(x0, last_gy - 1, x1, y - 1, col)
        if s.beat:
            for yy in range(GY + 4, GY + GH - 4, 6):
                lcd.drawPixel(GX + gx, yy, YELLOW)
        last_gy = y
        gx += X_STEP
        if gx >= GW:
            gx = 0
            draw_graph_frame()
            draw_annotations(s)

def draw_tile(i, label, value, unit, col, meter=None, flash=False):
    x, TILE_W = TILE_X[i], TILE_WS[i]
    bg = col if flash else BG                   # BPM tile blinks on each beat
    fg = BG if flash else col
    lcd.fillRect(x, TILE_Y, TILE_W, TILE_H, bg)
    rrect(x, TILE_Y, TILE_W, TILE_H, 12, col if flash else FRAME)
    col = fg
    text_at(x + 14, TILE_Y + 8, label, F_TILE_LBL, BG if flash else LABEL, bg)
    if meter is None:
        vf = fit(value, TILE_W - 40, (TILE_H * 3) // 5)
        vy = TILE_Y + TILE_H - th(vf) - 10
        text_at(x + 18, vy, value, vf, col, bg)
        if unit:
            uf = fit(unit, 120, 40, 4)
            text_at(x + 18 + tw(value, vf) + 10,
                    vy + th(vf) - th(uf) - 6, unit, uf,
                    BG if flash else LABEL, bg)
    else:
        segs = 16
        sw = (TILE_W - 28) // segs
        sh = TILE_H // 2
        sy = TILE_Y + TILE_H - sh - 14
        filled = clamp(meter * segs // 100, 0, segs)
        for k in range(segs):
            lcd.fillRect(x + 14 + k * sw, sy, sw - 3, sh,
                         col if k < filled else METER_OFF)

def draw_tiles(s, stale):
    if s is None or stale:
        draw_tile(0, "BPM", "--", "", LABEL)
        draw_tile(1, "IBI", "--", "ms", LABEL)
        draw_tile(2, "SIG", "", "", LABEL, meter=0)
        return
    col = wave_color(s)
    draw_tile(0, "BPM", str(s.bpm) if s.bpm else "--", "", col, flash=s.beat)
    draw_tile(1, "IBI", str(s.ibi) if s.ibi else "--", "ms", col)
    rng = clamp(s.smax - s.smin, 0, 600)
    draw_tile(2, "SIG", "", "", col, meter=rng * 100 // 600)

# ============================== LINK ==============================

_sock = None
_ap = None

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
    if len(d) < PKT_LEN or d[0:2] != LINK_MAGIC:
        return None
    def u16(i):
        return (d[i] << 8) | d[i + 1]
    return {"dev": bytes(d[3:6]), "bpm": d[6], "quality": d[7], "state": d[8],
            "beat": bool(d[9] & 1), "resync": bool(d[9] & 2),
            "samples": (u16(10), u16(12)),
            "smin": u16(14), "smax": u16(16), "thresh": u16(18),
            "amp": u16(20), "ibi": u16(22)}

def link_poll():
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
    return out

# ============================== BOOT ==============================

link_init()
batt_level, batt_v, batt_chg = read_power()
draw_header(None, 0)
draw_signal(0)
draw_battery(batt_level, batt_v, batt_chg)
draw_graph_frame()
draw_annotations(None)
draw_tiles(None, True)
print("tab5_pulse: %dx%d CYD-style dashboard, waiting for sticks" % (W, H))

prev = None
prev_beat = False
prev_resync = False
last_hdr = time.ticks_ms()
last_stat = time.ticks_ms()
rx_count = 0
rate_mark = time.ticks_ms()     # packet-rate window for the signal bars
rate_base = 0
pkt_rate = 0
last_icons = 0
batt_level = batt_v = 0
batt_chg = False

while True:
    now = time.ticks_ms()

    got = False
    for p in link_poll():
        rx_count += 1
        s = stick_for(p["dev"])
        s.bpm = p["bpm"]; s.ibi = p["ibi"]; s.quality = p["quality"]
        s.state = p["state"]; s.beat = p["beat"]
        s.samples = p["samples"]; s.sig = p["samples"][-1]
        s.resync = p["resync"]
        s.smin = p["smin"]; s.smax = p["smax"]
        s.thresh = p["thresh"]; s.amp = p["amp"]
        s.last = now
        if s is primary:
            got = True

    s = primary
    stale = (s is None) or time.ticks_diff(now, s.last) > STALE_MS
    nlinked = 0
    for x in sticks:
        if time.ticks_diff(now, x.last) <= STALE_MS:
            nlinked += 1

    if got and not stale:
        draw_wave(s)

    # change-driven: only repaint what actually changed (CYD behaviour)
    cur = None if stale else (s.bpm, s.ibi, s.state, s.smax - s.smin)
    if cur != prev:
        draw_tiles(s, stale)
        draw_annotations(None if stale else s)
        prev = cur
    if s is not None and s.beat != prev_beat:
        draw_heart_area(s)
        prev_beat = s.beat

    # RESYNC: the stick sets state 7 (and a flag) for ~900ms after BtnA
    rs = bool(s and not stale and (s.resync or s.state == 7))
    if rs != prev_resync:
        if rs:
            draw_resync_banner(True)
        else:
            draw_graph_frame()               # wipe the banner cleanly
            draw_annotations(s)
        prev_resync = rs
    if time.ticks_diff(now, last_hdr) > 500:
        draw_header(None if stale else s, nlinked)
        draw_signal(pkt_rate)
        draw_battery(batt_level, batt_v, batt_chg)
        last_hdr = now

    # measured packet rate over a 1s window -> signal bars
    if time.ticks_diff(now, rate_mark) >= 1000:
        pkt_rate = rx_count - rate_base
        rate_base = rx_count
        rate_mark = now
        draw_signal(pkt_rate)

    if time.ticks_diff(now, last_icons) >= 5000:
        last_icons = now
        batt_level, batt_v, batt_chg = read_power()
        draw_battery(batt_level, batt_v, batt_chg)

    if time.ticks_diff(now, last_stat) >= 5000:
        last_stat = now
        print("rx=%d rate=%d/s linked=%d bpm=%s state=%s batt=%s(%dmV)"
              % (rx_count, pkt_rate, nlinked,
                 s.bpm if s else "-", s.state if s else "-",
                 batt_level, batt_v))

    time.sleep_ms(5)          # always yield: never starve the task watchdog
