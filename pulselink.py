# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Yury Gitman and World Famous Electronics
#
# pulselink.py — PulseLink for the M5Stack StickS3.
# UIFlow2 MicroPython.  Sensor: signal -> G2, VCC -> 3V3, GND -> GND.
# Beat-detection approach adapted from PulseSensorPlayground (MIT):
# https://github.com/WorldFamousElectronics/PulseSensorPlayground
# See THIRD_PARTY_NOTICES.md.
#
# ONE COLOUR LANGUAGE
#   The waveform, the heart, the coach and the tile borders always show the
#   SAME colour, so the whole screen reads at a glance:
#       blue   = collecting, nothing trustworthy yet
#       yellow = locking on
#       green  = full confidence
#   Yellow annotations over the graph (THR, beat ticks) are labels, not state.
#
# TWO THINGS TO KNOW BEFORE EDITING
#   1. The built-in font is PROPORTIONAL, not a 6x8 grid. At size 1 it is 15px
#      tall and "PulseSensor" is 92px wide; size-4 digits are 60px TALL.
#      Always measure with tw()/th() - never assume character cells.
#   2. lcd.print() paints an OPAQUE box behind the text, so anything drawn
#      beforehand in the same place is wiped out. Draw icons LAST.
#
# Everything tunable is in CONFIG. Layout derives from the screen size and is
# checked against a safe edge at boot - see the startup printout.

import M5
import time
from machine import ADC, Pin

# ============================== CONFIG ==============================

SENSOR_PIN = 2
SAMPLE_MS = 20                 # 50 Hz
SAFE = 5                       # safe edge on all four sides

# --- palette: yellow / light green / light blue only. No grey, no red. ---
BG        = 0x060A06           # near-black with a faint green cast
PANEL     = 0x0C140C           # tile fill
GRID      = 0x1C4A32           # grid lines + empty meter segments
GRID_SOFT = 0x143323
TEXT      = 0xFFFFFF
LABEL     = 0x5BE7FF           # tile labels
ANNOT     = 0xFFE34D           # yellow annotations over the graph

BLUE      = 0x5BE7FF           # collecting
YELLOW    = 0xFFE34D           # locking
GREEN     = 0x6EF58A           # confident

# --- detector: the PulseSensor beat-detection algorithm, on the 10-bit scale ---
PULSE_THRESHOLD = 550          # initial trigger level
NO_BEAT_TIMEOUT = 3000         # ms without a qualified beat -> drop the lock
MIN_BPM, MAX_BPM = 40, 180     # plausible rate window
MIN_IBI, MAX_IBI = 333, 1500   # plausible inter-beat interval, ms
MIN_AMP = 20                   # min peak-to-trough to call it a pulse
REFRACTORY = 250               # ms; also gated at 3/5 of the last IBI
Q_STEPS = 12                   # confidence ceiling
Q_LOCK = 10                    # >= this counts as locked
Q_UP, Q_DOWN = 3, 1            # confidence gained / lost per beat
RANGE_SNAP = 80                # min signal window before it snaps open
FLAT_RANGE, FLAT_AMP = 90, 12  # coach: "no signal" thresholds
REARM_RANGE = 120              # live signal but no beats -> re-arm
REARM_NO_BEAT, REARM_COOLDOWN = 2200, 3500
BPM_AVERAGE_N = 10             # classic PulseSensor rate[] smoothing.
                               # Set to 1 for instantaneous 60000/IBI.
BEAT_FLASH_MS = 200            # duration of the beat flash
STALE_MS = 1500                # no qualified beat this long -> stop claiming
                               # full confidence even if the counter is high
RESYNC_LABEL_MS = 900          # how long the RESYNC confirmation shows
RESYNC_FAST_MS = 6000          # fast-lock window after a resync
Q_UP_FAST = 6                  # confidence per beat during that window
                               # (locks in 2 clean beats instead of 4)

# ============================== SETUP ==============================

M5.begin()
lcd = M5.Lcd
lcd.setRotation(1)
W, H = lcd.width(), lcd.height()            # 240 x 135

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def mapv(v, a, b, c, d):
    if b == a: return c
    return c + (v - a) * (d - c) // (b - a)

def tw(s, size=1):
    lcd.setTextSize(size)
    return lcd.textWidth(s)

def th(size=1):
    lcd.setTextSize(size)
    return lcd.fontHeight()

def text_at(x, y, s, size, fg, bg):
    lcd.setTextSize(size)
    lcd.setTextColor(fg, bg)
    lcd.setCursor(x, y)
    lcd.print(s)

# ============================== LAYOUT ==============================
# All measured, all clamped to the safe edge, all verified at boot.

L, R = SAFE, W - SAFE                       # 5 .. 235
BOT = H - SAFE                              # 130

HDR_Y, HDR_BOT = 4, 20
NAME = "PulseSensor"
NAME_W = tw(NAME)

BATT_W, BATT_H = 22, 11                     # + 3px nub
BATT_X, BATT_Y = R - (BATT_W + 3), 6
# NOTE: keep these on separate lines. A tuple assignment that references
# WIFI_W on its own right-hand side evaluates the RHS first and dies with a
# NameError on a cold boot. Always verify the deployed main.py after a reset.
WIFI_W = 12
WIFI_X = BATT_X - 6 - WIFI_W
WIFI_Y = 6

HEART_R = 7                                 # heart is back in the top bar
HEART_X = L + NAME_W + 10 + HEART_R
HEART_Y = 10

GX, GY = 7, 24                              # waveform window
GW, GH = W - 14, 64
GRID_STEP, GRID_COL_STEP = 16, 34

PY, PH = 93, 36                             # the two tiles
TILE_W = 112
BPM_X, COACH_X = L, 123

LBL_SIZE, NUM_SIZE = 1, 2
LBL_H = th(LBL_SIZE)
LBL_Y = PY + (PH - LBL_H) // 2

THR_W = 62                                  # yellow annotation box
THR_X, THR_Y = GX + GW - THR_W - 2, GY + 2

CONF_SEGS, CONF_SEG_W = 10, 4

# ============================== STATE ==============================

sig = 512
smin = smax = 512
thresh = PULSE_THRESHOLD
peak = trough = 512
pulsing = False                 # inside a beat's rising phase
amp = 0
ibi_ms = 600                    # last interval, drives the 3/5 gate
bpm = 0
rates = []                      # last N qualified IBIs -> smoothed BPM
quality = 0
locked = False
first_beat, second_beat = True, False
last_beat = last_qual = last_rearm = time.ticks_ms()
rearms = 0
flash_until = 0
resync_label_until = 0          # showing the RESYNC confirmation
resync_fast_until = 0           # inside the fast-lock window
beat_mark = False               # draw a yellow tick at the next column
batt, charging, linked = 0, False, False
gx, last_gy = 0, GY + GH // 2

adc = ADC(Pin(SENSOR_PIN))
adc.atten(ADC.ATTN_11DB)

# ============================== DETECTOR ==============================
# PulseSensor beat-detection approach: adaptive threshold midway between
# the running peak and trough, rising-edge detection behind a refractory gate,
# plausibility qualification, and a confidence counter that must reach Q_LOCK
# before any number is shown.

def beating():
    return time.ticks_diff(flash_until, time.ticks_ms()) > 0

def track_range(s):
    """Adaptive signal window that creeps shut and snaps open."""
    global smin, smax
    smin = min(smin + 1, s)
    smax = max(smax - 1, s)
    if smax - smin < RANGE_SNAP:
        smin, smax = s - RANGE_SNAP // 2, s + RANGE_SNAP // 2

def qualify(new_ibi):
    """Physiologically plausible interval?"""
    rate = 60000 // new_ibi if new_ibi else 0
    return (MIN_BPM <= rate <= MAX_BPM
            and MIN_IBI <= new_ibi <= MAX_IBI
            and amp >= MIN_AMP)

def smoothed_bpm(new_ibi):
    """Classic PulseSensor rate[] averaging over the last N intervals."""
    rates.append(new_ibi)
    if len(rates) > BPM_AVERAGE_N:
        rates.pop(0)
    return 60000 * len(rates) // sum(rates)

def rearm(reason):
    global thresh, peak, trough, pulsing, quality, bpm, locked
    global rearms, last_rearm, last_beat, rates
    print("re-arm:", reason)
    mid = (smin + smax) // 2 if smax > smin else PULSE_THRESHOLD
    thresh = peak = trough = mid
    pulsing = False
    quality = 0
    bpm = 0
    rates = []
    locked = False
    rearms += 1
    last_rearm = last_beat = time.ticks_ms()

def resync():
    """BtnA: "look at THIS waveform, now."

    Plain rearm() was not enough. It reset the threshold but left two pieces of
    stale state that actively block re-locking:
      * ibi_ms still held the last (possibly wrong) interval, and the detector
        gates beats at 3/5 of it - a stale 1400ms interval blocks every real
        beat for 840ms.
      * amp only updates on a falling edge, so a stale-low value made qualify()
        reject perfectly good beats forever.
    So a clean wave on screen could never lock, no matter how long you waited.

    This clears both, seeds amp from the live signal range so the very next beat
    can qualify, and opens a short fast-lock window so a good wave locks in two
    beats instead of four."""
    global thresh, peak, trough, pulsing, ibi_ms, amp, quality, bpm, rates
    global locked, first_beat, second_beat, last_beat, last_qual, last_rearm
    global rearms, resync_label_until, resync_fast_until
    now = time.ticks_ms()
    mid = (smin + smax) // 2 if smax > smin else PULSE_THRESHOLD
    thresh = peak = trough = mid
    pulsing = False
    ibi_ms = 600                     # drop the stale 3/5 gate
    amp = max(amp, smax - smin)      # trust what is on screen right now
    first_beat, second_beat = True, False
    quality = 0
    bpm = 0
    rates = []
    locked = False
    rearms += 1
    last_beat = last_rearm = last_qual = now
    resync_label_until = time.ticks_add(now, RESYNC_LABEL_MS)
    resync_fast_until = time.ticks_add(now, RESYNC_FAST_MS)
    print("RESYNC: thresh=%d amp=%d range=%d-%d (fast-lock %dms)"
          % (thresh, amp, smin, smax, RESYNC_FAST_MS))

def detect(now):
    global peak, trough, pulsing, amp, thresh, ibi_ms, bpm
    global quality, locked, first_beat, second_beat, last_beat, last_qual
    global flash_until, beat_mark

    n = time.ticks_diff(now, last_beat)
    gate = (ibi_ms * 3) // 5                 # 3/5-of-last-IBI refractory gate

    if sig < thresh and n > gate and sig < trough:
        trough = sig
    if sig > thresh and sig > peak:
        peak = sig

    if n > REFRACTORY and n > gate and sig > thresh and not pulsing:
        pulsing = True
        new_ibi = n
        last_beat = now
        if second_beat:                      # prime; don't trust it yet
            second_beat = False
            ibi_ms = new_ibi
        elif first_beat:
            first_beat, second_beat = False, True
        else:
            ibi_ms = new_ibi
            good = qualify(new_ibi)
            if good:
                bpm = smoothed_bpm(new_ibi)
                last_qual = now
                step = Q_UP_FAST if time.ticks_diff(resync_fast_until, now) > 0 else Q_UP
                quality = min(Q_STEPS, quality + step)
            else:
                quality = max(0, quality - Q_DOWN)
            locked = quality >= Q_LOCK
            if locked and good:
                flash_until = time.ticks_add(now, BEAT_FLASH_MS)
                beat_mark = True
                print("BEAT bpm=%d ibi=%d amp=%d Q=%d" % (bpm, new_ibi, amp, quality))

    if sig < thresh and pulsing:             # falling edge: retune threshold
        pulsing = False
        amp = peak - trough
        thresh = trough + amp // 2
        peak = trough = thresh

    if time.ticks_diff(now, last_qual) > NO_BEAT_TIMEOUT:
        locked = False
        quality = 0
        bpm = 0

    if (not locked
            and smax - smin >= REARM_RANGE
            and time.ticks_diff(now, last_beat) >= REARM_NO_BEAT
            and time.ticks_diff(now, last_rearm) >= REARM_COOLDOWN):
        rearm("live signal, no beats")

# ============================== COACH ==============================

def coach():
    """(label, colour) - the single source of truth for the screen's colour.

    ORDER MATTERS. Real beat activity is checked BEFORE the flatness heuristic:
    a small signal range was previously reported as "NO SIGNAL" even while the
    detector was happily qualifying beats at 8/12, which is exactly the case
    where the user is looking at a clean wave and the coach refuses to agree.
    If beats are arriving and fresh, that is the truth - say so.
      1. resync confirmation (button feedback)
      2. fresh qualified beats  -> QUALIFIED / LOCKING
      3. had a lock, beats died -> SIGNAL LOST
      4. nothing there          -> NO SIGNAL
      5. touching but too weak  -> HOLD STEADY
      6. waveform, no beats yet -> GOOD WAVE / SEARCHING
    """
    rng = smax - smin
    now_ms = time.ticks_ms()
    if time.ticks_diff(resync_label_until, now_ms) > 0:
        return "RESYNC", YELLOW          # confirm the button press on screen

    fresh = time.ticks_diff(now_ms, last_qual) <= STALE_MS
    if fresh and locked and quality >= Q_STEPS:
        return "QUALIFIED", GREEN
    if fresh and (locked or quality > 0):
        return "LOCKING", YELLOW
    if locked:                           # counter high but beats dried up
        return "SIGNAL LOST", YELLOW
    if rng < FLAT_RANGE or amp < FLAT_AMP:
        return "NO SIGNAL", BLUE
    if amp < MIN_AMP:
        return "HOLD STEADY", BLUE
    if rng >= REARM_RANGE:
        return "GOOD WAVE", BLUE
    return "SEARCHING", BLUE

def state_color():
    return coach()[1]

# ============================== DRAWING ==============================

def draw_heart(cx, cy, r, col):
    lcd.fillCircle(cx - r // 2, cy - r // 3, r // 2 + 1, col)
    lcd.fillCircle(cx + r // 2, cy - r // 3, r // 2 + 1, col)
    lcd.fillTriangle(cx - r, cy - r // 4, cx + r, cy - r // 4, cx, cy + r, col)

def draw_header_heart():
    """Same colour as the waveform; swells on each beat."""
    r = HEART_R if beating() else HEART_R - 2
    lcd.fillRect(HEART_X - HEART_R - 1, 0, (HEART_R + 1) * 2, HDR_BOT - 1, BG)
    draw_heart(HEART_X, HEART_Y, r, state_color())

def draw_link():
    col = state_color() if linked else GRID
    lcd.fillRect(WIFI_X, WIFI_Y, WIFI_W, 12, BG)
    for i in range(3):
        h = 4 + i * 3
        lcd.fillRect(WIFI_X + i * 4, WIFI_Y + 11 - h, 3, h, col)

def draw_battery():
    if batt < 0:
        col, lvl = LABEL, 0
    else:
        lvl = clamp(batt, 0, 100)
        col = BLUE if charging else (YELLOW if lvl <= 25 else GREEN)
    lcd.drawRect(BATT_X, BATT_Y, BATT_W, BATT_H, LABEL)
    lcd.fillRect(BATT_X + BATT_W, BATT_Y + 3, 3, 5, LABEL)
    lcd.fillRect(BATT_X + 2, BATT_Y + 2, BATT_W - 4, BATT_H - 4, BG)
    fill = (lvl * (BATT_W - 4)) // 100
    if fill:
        lcd.fillRect(BATT_X + 2, BATT_Y + 2, fill, BATT_H - 4, col)

def draw_header():
    lcd.fillRect(0, 0, W, HDR_BOT, BG)
    lcd.fillRect(0, HDR_BOT - 1, W, 1, GRID)
    text_at(L, HDR_Y, NAME, 1, TEXT, BG)
    draw_link()
    draw_battery()
    draw_header_heart()                      # icons last: text paints opaque

def draw_thr():
    """Yellow annotation. Cleared + fixed width, or old digits garble it."""
    lcd.fillRect(THR_X - 2, THR_Y - 1, THR_W + 2, 16, BG)
    text_at(THR_X, THR_Y, "THR %4d" % int(thresh), 1, ANNOT, BG)

def thresh_dot(col_x):
    if col_x % 6:
        return
    y = clamp(mapv(PULSE_THRESHOLD, smin, smax, GY + GH - 4, GY + 4),
              GY + 4, GY + GH - 4)
    lcd.drawPixel(GX + col_x, y, ANNOT)

def clear_column(col_x):
    x = GX + col_x
    lcd.fillRect(x, GY, 1, GH, GRID_SOFT if col_x % GRID_COL_STEP == 0 else BG)
    for y in range(0, GH + 1, GRID_STEP):
        lcd.drawPixel(x, GY + y, GRID_SOFT)
    thresh_dot(col_x)

def draw_graph_frame():
    lcd.drawRect(GX - 2, GY - 2, GW + 4, GH + 4, GRID)
    lcd.fillRect(GX, GY, GW, GH, BG)
    for x in range(0, GW, GRID_COL_STEP):
        lcd.fillRect(GX + x, GY, 1, GH, GRID_SOFT)
    for y in range(0, GH + 1, GRID_STEP):
        lcd.fillRect(GX, GY + y, GW, 1, GRID_SOFT)
    for x in range(0, GW, 6):
        thresh_dot(x)
    draw_thr()

def draw_wave():
    """One column per sample: erase ahead, draw the new segment."""
    global gx, last_gy, beat_mark
    y = clamp(mapv(sig, smin, smax, GY + GH - 4, GY + 4), GY + 4, GY + GH - 4)
    for k in range(3):
        clear_column((gx + k) % GW)
    if beat_mark:                            # yellow tick BEHIND the trace
        beat_mark = False
        for yy in range(GY, GY + GH, 3):
            lcd.drawPixel(GX + gx, yy, ANNOT)
    if gx:
        col = state_color()
        lcd.drawLine(GX + gx - 1, last_gy, GX + gx, y, col)
        lcd.drawLine(GX + gx - 1, last_gy + 1, GX + gx, y + 1, col)   # 2px
    # Only repaint the annotation while the sweep is actually inside its box,
    # and at most every 6th column - a text render per sample was heavy enough
    # to push the frame past SAMPLE_MS.
    if GX + gx >= THR_X - 2 and gx % 6 == 0:
        draw_thr()
    last_gy = y
    gx += 1
    if gx >= GW:
        gx = 0
        draw_graph_frame()

def draw_bpm_tile():
    """Inverts on every beat. No icon here - the heart is in the top bar."""
    col = state_color()
    invert = beating()
    bg = col if invert else PANEL
    lcd.fillRect(BPM_X, PY, TILE_W, PH, bg)
    lcd.drawRect(BPM_X, PY, TILE_W, PH, col if locked else GRID)
    text_at(BPM_X + 6, LBL_Y, "BPM", LBL_SIZE, BG if invert else LABEL, bg)
    s = str(bpm) if locked else "--"
    size = NUM_SIZE
    avail = TILE_W - 12 - tw("BPM", LBL_SIZE) - 4
    while size > 1 and (tw(s, size) > avail or th(size) > PH - 6):
        size -= 1
    fg = BG if invert else (col if locked else LABEL)
    text_at(BPM_X + TILE_W - 6 - tw(s, size), PY + (PH - th(size)) // 2,
            s, size, fg, bg)

def draw_coach_tile():
    label, col = coach()
    conf = quality * 100 // Q_STEPS
    lcd.fillRect(COACH_X, PY, TILE_W, PH, PANEL)
    lcd.drawRect(COACH_X, PY, TILE_W, PH, col)
    text_at(COACH_X + 6, PY + 3, label, 1, col, PANEL)
    filled = conf * CONF_SEGS // 100
    by = PY + 21
    for i in range(CONF_SEGS):
        lcd.fillRect(COACH_X + 6 + i * CONF_SEG_W, by, CONF_SEG_W - 1, 8,
                     col if i < filled else GRID)
    pct = "%d%%" % conf
    text_at(COACH_X + TILE_W - 6 - tw(pct), by - 3, pct, 1, col, PANEL)

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

# ============================== BOOT ==============================

lcd.fillScreen(BG)
read_status()
draw_header()
draw_graph_frame()
draw_bpm_tile()
draw_coach_tile()

_edges = (L + NAME_W, HEART_X + HEART_R, WIFI_X + WIFI_W, BATT_X + BATT_W + 3,
          GX + GW + 2, BPM_X + TILE_W, COACH_X + TILE_W)
print("screen %dx%d  safe edge %d" % (W, H, SAFE))
print("  header: name %d..%d | heart %d..%d | wifi %d..%d | batt %d..%d"
      % (L, L + NAME_W, HEART_X - HEART_R, HEART_X + HEART_R,
         WIFI_X, WIFI_X + WIFI_W, BATT_X, BATT_X + BATT_W + 3))
print("  right edge %d/%d %s   bottom %d/%d %s"
      % (max(_edges), R, "OK" if max(_edges) <= R else "OVERFLOW",
         PY + PH, BOT, "OK" if PY + PH <= BOT else "OVERFLOW"))
for _c in ("QUALIFIED", "HOLD STEADY", "SIGNAL LOST", "NO SIGNAL",
           "SEARCHING", "GOOD WAVE", "LOCKING"):
    _w = tw(_c)
    print("  coach %-13r %3dpx %s"
          % (_c, _w, "OK" if _w <= TILE_W - 12 else "TOO WIDE"))
print("PulseLink running: G2 @ %dHz, BPM averaged over up to %d beats. BtnA = RESYNC, BtnB = reset."
      % (1000 // SAMPLE_MS, BPM_AVERAGE_N))

# ============================== MAIN LOOP ==============================

prev = None
next_t = time.ticks_ms()
last_poll = last_stat = time.ticks_ms()

while True:
    M5.update()
    now = time.ticks_ms()

    if M5.BtnA.wasPressed():             # front blue button
        resync()
    if M5.BtnB.wasPressed():             # side button: full cold reset
        rearm("BtnB full reset")

    wait = time.ticks_diff(next_t, now)
    if wait > 0:
        time.sleep_ms(min(wait, 5))
        continue
    # ALWAYS yield, even when we are behind schedule. Without this the loop
    # spins with no sleep the moment a frame costs >= SAMPLE_MS, which starves
    # the task watchdog and the board reboots (rst:0x8 TG1WDT_SYS_RST).
    time.sleep_ms(1)
    next_t = time.ticks_add(next_t, SAMPLE_MS)
    if time.ticks_diff(now, next_t) > 100:       # fell behind: resync
        next_t = time.ticks_add(now, SAMPLE_MS)

    sig = adc.read() >> 2                        # 12-bit -> 10-bit for detector compatibility
    track_range(sig)
    detect(now)
    draw_wave()

    # redraw only what changed: value, coach state, or the beat flash
    label, col = coach()
    cur = (bpm, label, quality, locked, beating())
    if cur != prev:
        if prev is None or label != prev[1] or cur[4] != prev[4]:
            draw_header_heart()
        draw_bpm_tile()
        draw_coach_tile()
        prev = cur

    if time.ticks_diff(now, last_poll) >= 2000:
        last_poll = now
        read_status()
        draw_link()
        draw_battery()

    if time.ticks_diff(now, last_stat) >= 5000:
        last_stat = now
        print("sig=%d amp=%d bpm=%d Q=%d/%d %s batt=%d%% link=%d rearms=%d"
              % (sig, amp, bpm, quality, Q_STEPS, label, batt,
                 1 if linked else 0, rearms))
