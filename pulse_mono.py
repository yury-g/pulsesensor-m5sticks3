# pulse_mono.py — ROLLBACK SNAPSHOT (saved 2026-07-16).
# The minimal monochrome build: white app name, white pulsing heart + "BEAT",
# cyan/white trace, no colour coding. Kept so we can go back to it.
#   ./stick.sh deploy pulse_mono.py     <- restore this look
#   ./stick.sh deploy pulse_cyd.py      <- the colour CYD build
#
# PulseSensor heart-rate monitor for M5StickS3 (UIFlow2 MicroPython).
# Sensor signal -> G2, VCC -> 3V3, GND -> GND.
#
# Screen (240x135): header with app name, live-beat heart, link + battery;
# scrolling PPG waveform; large BPM and IBI readouts.
#
# LAYOUT NOTE: the built-in font is PROPORTIONAL, not a 6x8 grid.
# Measured on hardware at size 1: "PULSESENSOR" = 92px (not 66), fontHeight = 15
# (not 8); digits are 8px wide. So size 4 digits are 60px TALL. Everything below
# is measured at runtime with lcd.textWidth()/fontHeight() and clamped to a 5px
# safe edge - never hardcode character cells.
import M5
import time
from machine import ADC, Pin

# ===== colors =====
BG        = 0x000000
PANEL     = 0x080808
PANEL_DK  = 0x001000
GRID      = 0x181C18
GRID_SOFT = 0x101410
TEXT      = 0xFFFFFF
MUTED     = 0x8C8E8C
CYAN      = 0x00FFFF
CYAN_DK   = 0x008A90
TEAL      = 0x00BE9A
RED       = 0xFF0000
RED_DK    = 0x600000
RED_MID   = 0xA80000
AMBER     = 0xF87C00

# ===== detector settings (CYD values, 10-bit signal scale) =====
PULSE_THRESHOLD = 550
NO_BEAT_TIMEOUT = 3000
MIN_BPM, MAX_BPM = 40, 180
MIN_IBI, MAX_IBI = 333, 1500
MIN_AMP = 20
Q_STEPS, LOCK_STEPS = 12, 10
REARM_RANGE, REARM_NO_BEAT, REARM_COOLDOWN = 120, 2200, 3500

M5.begin()
lcd = M5.Lcd
lcd.setRotation(1)
W, H = lcd.width(), lcd.height()          # 240 x 135
lcd.fillScreen(BG)

def mapv(v, a, b, c, d):
    if b == a: return c
    return c + (v - a) * (d - c) // (b - a)

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def tw(s, size):
    lcd.setTextSize(size)
    return lcd.textWidth(s)

def th(size):
    lcd.setTextSize(size)
    return lcd.fontHeight()

# ===== layout: safe edge on all four sides =====
SAFE = 5
L, R = SAFE, W - SAFE                      # 5 .. 235
TOP, BOT = SAFE, H - SAFE                  # 5 .. 130

HDR_Y = 4                                  # size-1 text -> 4..18
HDR_BOT = 20
BATT_W, BATT_H = 22, 11                    # + 3px nub = 25 total
BATT_X = R - (BATT_W + 3)                  # 210 -> 210..234
BATT_Y = 6
WIFI_W = 12
WIFI_X = BATT_X - 6 - WIFI_W               # 192 -> 192..203
WIFI_Y = 6
HEART_R = 7                                # heart occupies ~16px
HEART_Y = 10
HEART_REST = 5                             # size between beats, so it stays visible

GX, GY = 7, 24                             # graph 7..232, frame 5..234
GW, GH = W - 14, 64                        #        y 24..87, frame 22..89
GRID_STEP = 16                             # horizontal rules: 0/16/32/48/64

PY, PH = 93, 36                            # readouts y 93..129
PANEL_W = 112
BPM_X, IBI_X = L, 123                      # 5..117 and 123..235
# Only 36px of height now, so label and number sit SIDE BY SIDE
# (stacked would need 15 + 30 = 45px). Number is right-aligned.
NUM_SIZE = 2                               # 30px tall, digits 16px wide
LBL_SIZE = 1

# ===== state =====
sig = 512
bpm = ibi = 0
amp = 0
p = t = 512
thresh = PULSE_THRESHOLD
pulse = False
ibi_ms = 600
last_beat = last_qual = time.ticks_ms()
quality = 0
locked = False
rearms = 0
last_rearm = time.ticks_ms()
smin = smax = 512
led = 0
gx = 0
last_gy = GY + GH // 2
batt, charging, linked = 0, False, False

adc = ADC(Pin(2))
adc.atten(ADC.ATTN_11DB)

def read_status():
    global batt, charging, linked
    try:
        batt = M5.Power.getBatteryLevel()
        charging = bool(M5.Power.isCharging())
    except Exception:
        batt, charging = -1, False
    try:
        import network
        w = network.WLAN(network.STA_IF)
        linked = bool(w.active() and w.isconnected())
    except Exception:
        linked = False

# ===== header =====
NAME = "PULSESENSOR"
LIVE = "BEAT"
NAME_W = tw(NAME, 1)
LIVE_W = tw(LIVE, 1)
# centre heart + BEAT in the gap between the name and the link icon,
# and fall back to a shorter label (or none) if that gap is ever too small
_gap_l = L + NAME_W + 6
_gap_r = WIFI_X - 6
_need = HEART_R * 2 + 4 + LIVE_W
if _need > _gap_r - _gap_l:
    LIVE, LIVE_W = "", 0
    _need = HEART_R * 2
_start = _gap_l + ((_gap_r - _gap_l) - _need) // 2
HEART_X = _start + HEART_R
LIVE_X = _start + HEART_R * 2 + 4

def pulse_gray():
    """One ramp drives both the heart and the BEAT label so they fade together.
    Never falls below 0x50 - the heart stays visible between beats."""
    v = clamp(mapv(led, 0, 255, 0x50, 0xFF), 0x50, 0xFF)
    return (v << 16) | (v << 8) | v

def draw_heart():
    s = clamp(mapv(led, 0, 255, HEART_REST, HEART_R), HEART_REST, HEART_R)
    y0 = max(0, HEART_Y - HEART_R - 1)
    y1 = min(HDR_BOT - 1, HEART_Y + HEART_R + 2)   # never erase the divider
    lcd.fillRect(HEART_X - HEART_R - 1, y0, (HEART_R + 1) * 2, y1 - y0, BG)
    c = pulse_gray()
    lcd.fillCircle(HEART_X - s // 2, HEART_Y - s // 3, s // 2 + 1, c)
    lcd.fillCircle(HEART_X + s // 2, HEART_Y - s // 3, s // 2 + 1, c)
    lcd.fillTriangle(HEART_X - s, HEART_Y - s // 4,
                     HEART_X + s, HEART_Y - s // 4,
                     HEART_X, HEART_Y + s, c)

def draw_link():
    c = TEAL if linked else GRID
    lcd.fillRect(WIFI_X, WIFI_Y, WIFI_W, 12, BG)
    for i in range(3):
        h = 4 + i * 3
        lcd.fillRect(WIFI_X + i * 4, WIFI_Y + 11 - h, 3, h, c)

def batt_color():
    if batt < 0: return MUTED
    if charging: return CYAN
    if batt <= 20: return RED
    if batt <= 50: return AMBER
    return TEAL

def draw_battery():
    """Level is shown by fill width + colour; there is no room for a % label."""
    col = batt_color()
    lvl = clamp(batt, 0, 100) if batt >= 0 else 0
    lcd.drawRect(BATT_X, BATT_Y, BATT_W, BATT_H, MUTED)
    lcd.fillRect(BATT_X + BATT_W, BATT_Y + 3, 3, 5, MUTED)
    lcd.fillRect(BATT_X + 2, BATT_Y + 2, BATT_W - 4, BATT_H - 4, BG)
    fill = (lvl * (BATT_W - 4)) // 100
    if fill > 0:
        lcd.fillRect(BATT_X + 2, BATT_Y + 2, fill, BATT_H - 4, col)

def draw_beat_label():
    if not LIVE:
        return
    lcd.setTextSize(1)
    lcd.setTextColor(pulse_gray(), BG)
    lcd.setCursor(LIVE_X, HDR_Y)
    lcd.print(LIVE)

def draw_header():
    lcd.fillRect(0, 0, W, HDR_BOT, BG)
    lcd.fillRect(0, HDR_BOT - 1, W, 1, GRID)
    lcd.setTextSize(1)
    lcd.setTextColor(TEXT, BG)
    lcd.setCursor(L, HDR_Y)
    lcd.print(NAME)
    draw_beat_label()
    draw_link()
    draw_battery()
    draw_heart()

# ===== waveform =====
def trace_color():
    return TEXT if locked else CYAN

def thresh_dot(lx):
    if lx % 6: return
    y = clamp(mapv(PULSE_THRESHOLD, smin, smax, GY + GH - 4, GY + 4), GY + 4, GY + GH - 4)
    lcd.drawPixel(GX + lx, y, AMBER if pulse else CYAN_DK)

def graph_col_bg(lx):
    x = GX + lx
    lcd.fillRect(x, GY, 1, GH, GRID_SOFT if lx % 34 == 0 else BG)
    for y in range(0, GH + 1, GRID_STEP):
        lcd.drawPixel(x, GY + y, GRID_SOFT)
    thresh_dot(lx)

def draw_graph_frame():
    lcd.fillRect(GX - 2, GY - 2, GW + 4, GH + 4, PANEL_DK)
    lcd.drawRect(GX - 2, GY - 2, GW + 4, GH + 4, GRID)
    lcd.fillRect(GX, GY, GW, GH, BG)
    for x in range(0, GW, 34):
        lcd.fillRect(GX + x, GY, 1, GH, GRID_SOFT)
    for y in range(0, GH + 1, GRID_STEP):
        lcd.fillRect(GX, GY + y, GW, 1, GRID_SOFT)
    for x in range(0, GW, 6):
        thresh_dot(x)

# ===== readouts =====
LBL_H = th(LBL_SIZE)
LBL_Y = PY + (PH - LBL_H) // 2             # label vertically centred in the row
NUM_PAD = 3

def readout(x, label, value, valid):
    """Label left, number right-aligned on the SAME row - at 36px tall there is
    no room to stack them (15 + 30 = 45). The number steps down a size if it
    would ever exceed the space left beside the label, so it cannot clip."""
    lcd.fillRect(x, PY, PANEL_W, PH, PANEL)
    lcd.drawRect(x, PY, PANEL_W, PH, TEAL if valid else GRID)
    lbl_w = tw(label, LBL_SIZE)
    lcd.setTextSize(LBL_SIZE)
    lcd.setTextColor(MUTED, PANEL)
    lcd.setCursor(x + 6, LBL_Y)
    lcd.print(label)
    s = str(value) if valid else "--"
    size = NUM_SIZE
    avail_w = PANEL_W - 12 - lbl_w - 4     # 6px pad each side, 4px gap after label
    avail_h = PH - NUM_PAD * 2
    while size > 1 and (tw(s, size) > avail_w or th(size) > avail_h):
        size -= 1
    num_w, num_h = tw(s, size), th(size)
    lcd.setTextSize(size)
    lcd.setTextColor(TEXT if valid else MUTED, PANEL)
    lcd.setCursor(x + PANEL_W - 6 - num_w, PY + (PH - num_h) // 2)
    lcd.print(s)

def draw_readouts():
    readout(BPM_X, "BPM", bpm, locked)
    readout(IBI_X, "IBI", ibi, locked)

def rearm(reason):
    global thresh, p, t, pulse, quality, bpm, ibi, locked, rearms, last_rearm, last_beat
    print("Re-arming detector:", reason)
    mid = (smin + smax) // 2
    thresh = p = t = mid if smax > smin else PULSE_THRESHOLD
    pulse = False
    quality = 0
    bpm = ibi = 0
    locked = False
    rearms += 1
    last_rearm = last_beat = time.ticks_ms()

# ===== boot: report the computed geometry so overflow is provable, not assumed =====
read_status()
draw_header()
draw_graph_frame()
draw_readouts()
print("screen %dx%d safe=%d" % (W, H, SAFE))
print("  header: name %d..%d | heart %d..%d | live '%s' %d..%d | wifi %d..%d | batt %d..%d"
      % (L, L + NAME_W, HEART_X - HEART_R, HEART_X + HEART_R, LIVE,
         LIVE_X, LIVE_X + LIVE_W, WIFI_X, WIFI_X + WIFI_W, BATT_X, BATT_X + BATT_W + 3))
print("  graph frame x %d..%d  y %d..%d" % (GX - 2, GX + GW + 2, GY - 2, GY + GH + 2))
print("  panels x %d..%d and %d..%d  y %d..%d" %
      (BPM_X, BPM_X + PANEL_W, IBI_X, IBI_X + PANEL_W, PY, PY + PH))
_edges = (L + NAME_W, LIVE_X + LIVE_W, WIFI_X + WIFI_W, BATT_X + BATT_W + 3,
          GX + GW + 2, BPM_X + PANEL_W, IBI_X + PANEL_W)
print("  max right edge %d (limit %d) -> %s"
      % (max(_edges), R, "OK" if max(_edges) <= R else "OVERFLOW"))
print("  max bottom %d (limit %d) -> %s"
      % (PY + PH, BOT, "OK" if PY + PH <= BOT else "OVERFLOW"))
def _chosen(s, label):
    _av = PANEL_W - 12 - tw(label, LBL_SIZE) - 4
    _sz = NUM_SIZE
    while _sz > 1 and (tw(s, _sz) > _av or th(_sz) > PH - NUM_PAD * 2):
        _sz -= 1
    return _sz, tw(s, _sz), th(_sz), _av
for _s, _l in (("888", "BPM"), ("8888", "IBI"), ("--", "IBI")):
    _sz, _w, _h, _av = _chosen(_s, _l)
    print("  %-6s %-5r -> size %d, %dx%d px, fits in %dx%d"
          % (_l, _s, _sz, _w, _h, _av, PH - NUM_PAD * 2))
print("  graph is now %dpx tall, readouts %dpx" % (GH, PH))
print("pulse_cyd running: sensor G2. BtnA = re-arm.")

prev = None
last_stat = last_poll = time.ticks_ms()
next_t = time.ticks_ms()
first, second = True, False

while True:
    M5.update()
    now = time.ticks_ms()

    if M5.BtnA.wasPressed():
        rearm("manual (BtnA)")

    wait = time.ticks_diff(next_t, now)
    if wait > 0:
        time.sleep_ms(min(wait, 5))
        continue
    next_t = time.ticks_add(next_t, 20)      # 50 Hz
    if time.ticks_diff(now, next_t) > 100:
        next_t = time.ticks_add(now, 20)

    sig = adc.read() >> 2                    # 12-bit -> 10-bit like the CYD build

    smin = min(smin + 1, sig)
    smax = max(smax - 1, sig)
    if smax - smin < 80:
        smin, smax = sig - 40, sig + 40

    # ===== beat detection =====
    N = time.ticks_diff(now, last_beat)
    if sig < thresh and N > (ibi_ms * 3) // 5 and sig < t:
        t = sig
    if sig > thresh and sig > p:
        p = sig

    if N > 250 and sig > thresh and not pulse and N > (ibi_ms * 3) // 5:
        pulse = True
        new_ibi = N
        last_beat = now
        if second:
            second = False
            ibi_ms = new_ibi
        if first:
            first = False
            second = True
        else:
            ibi_ms = new_ibi
            nbpm = 60000 // new_ibi if new_ibi else 0
            qualified = (MIN_BPM <= nbpm <= MAX_BPM and MIN_IBI <= new_ibi <= MAX_IBI
                         and amp >= MIN_AMP)
            if qualified:
                bpm, ibi = nbpm, new_ibi
                last_qual = now
                quality = min(Q_STEPS, quality + 3)
            else:
                quality = max(0, quality - 1)
            locked = quality >= LOCK_STEPS
            if locked and qualified:
                led = 255
                print("BEAT bpm=%d ibi=%d amp=%d Q=%d" % (bpm, ibi, amp, quality))

    if sig < thresh and pulse:
        pulse = False
        amp = p - t
        thresh = t + amp // 2
        p = t = thresh

    if time.ticks_diff(now, last_qual) > NO_BEAT_TIMEOUT:
        locked = False
        quality = 0
        bpm = ibi = 0

    if (not locked and smax - smin >= REARM_RANGE
            and time.ticks_diff(now, last_beat) >= REARM_NO_BEAT
            and time.ticks_diff(now, last_rearm) >= REARM_COOLDOWN):
        rearm("alive signal without beat event")

    if led > 0:
        led = max(0, led - 12)
        draw_heart()
        draw_beat_label()

    # ===== waveform =====
    y = clamp(mapv(sig, smin, smax, GY + GH - 4, GY + 4), GY + 4, GY + GH - 4)
    graph_col_bg(gx)
    graph_col_bg((gx + 1) % GW)
    graph_col_bg((gx + 2) % GW)
    if gx > 0:
        lcd.drawLine(GX + gx - 1, last_gy, GX + gx, y, trace_color())
    if led > 180:
        lcd.fillCircle(GX + gx, y, 2, RED)
    last_gy = y
    gx += 1
    if gx >= GW:
        gx = 0
        draw_graph_frame()

    cur = (bpm, ibi, locked)
    if cur != prev:
        if prev is None or locked != prev[2]:
            draw_header()
        draw_readouts()
        prev = cur

    if time.ticks_diff(now, last_poll) >= 2000:
        last_poll = now
        read_status()
        draw_link()
        draw_battery()

    if time.ticks_diff(now, last_stat) >= 5000:
        last_stat = now
        print("signal=%d amp=%d bpm=%d ibi=%d locked=%d Q=%d batt=%d%% link=%d"
              % (sig, amp, bpm, ibi, 1 if locked else 0, quality, batt, 1 if linked else 0))
