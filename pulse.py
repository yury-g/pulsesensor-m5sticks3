# pulse.py — "Beat Happens" heart-rate monitor for M5StickS3 (UIFlow2 MicroPython)
# Sensor: pulsesensor.com PulseSensor — signal->G2 (GPIO2), VCC->3V3, GND->GND
# Screen: pulsing heart + "Beat Happens" flash, scrolling PPG waveform, big BPM.
import M5
import time
from machine import ADC, Pin

M5.begin()
lcd = M5.Lcd
lcd.setRotation(1)
W, H = lcd.width(), lcd.height()  # 240 x 135

# PulseSensor brand palette (dark mode)
BG = 0x1A1A1A
SALMON = 0xE63946   # waveform + beat flash (signature color)
GREEN = 0x4ADE80    # BPM number
HEART = 0xEF4444    # heart icon at rest
GRAY = 0x6B7280     # labels
GRID = 0x374151     # borders / dimmed text

HAS_TRI = hasattr(lcd, "fillTriangle")

# layout: top strip = heart + "Beat Happens" + BPM, bottom = waveform
WY0 = 58
WH = H - WY0
WY1 = H - 1
HX, HY = 30, 30
R_REST, R_BEAT = 10, 14

def draw_heart(cx, cy, r, col):
    lcd.fillCircle(cx - r, cy - r // 2, r, col)
    lcd.fillCircle(cx + r, cy - r // 2, r, col)
    if HAS_TRI:
        ty = cy - r // 2 + (2 * r) // 5
        lcd.fillTriangle(cx - 2 * r, ty, cx + 2 * r, ty, cx, cy + r + r // 2, col)
    else:
        lcd.fillCircle(cx, cy, r, col)

def heart_off():
    lcd.fillRect(0, 6, 62, 50, BG)
    draw_heart(HX, HY, R_REST, HEART)

def heart_on():
    lcd.fillRect(0, 6, 62, 50, BG)
    draw_heart(HX, HY, R_BEAT, SALMON)

def beat_text(on):
    lcd.setTextSize(2)
    lcd.setTextColor(SALMON if on else GRID, BG)
    lcd.setCursor(64, 8)
    lcd.print("Beat")
    lcd.setCursor(64, 30)
    lcd.print("Happens")

def show_bpm(bpm):
    lcd.setTextSize(4)
    lcd.setTextColor(GREEN, BG)
    lcd.setCursor(148, 8)
    lcd.print("%3s" % (str(bpm) if bpm else "--"))
    lcd.setTextSize(1)
    lcd.setTextColor(GRAY, BG)
    lcd.setCursor(198, 44)
    lcd.print("BPM")

lcd.fillScreen(BG)
lcd.drawLine(0, WY0 - 1, W - 1, WY0 - 1, GRID)
heart_off()
beat_text(False)
show_bpm(0)

adc = ADC(Pin(2))
adc.atten(ADC.ATTN_11DB)

# ---- classic PulseSensor beat detection state ----
SAMPLE_MS = 20            # 50 Hz
MIN_AMP = 150             # min peak-trough ADC counts to count as a pulse (noise gate)
ibi = 600                 # ms between beats
bpm = 0
pulse = False
first, second = True, False
thresh = 2048
P = 2048
T = 2048
last_amp = 0
rates = []
last_beat = time.ticks_ms()
beat_count = 0

# waveform autoscale + draw state
smin, smax = 4095, 0
x = 0
prev_y = None
flash_until = 0
last_stat = time.ticks_ms()

print("pulse.py running: PulseSensor on GPIO2, 50Hz, classic beat detection")

next_t = time.ticks_ms()
while True:
    M5.update()
    wait = time.ticks_diff(next_t, time.ticks_ms())
    if wait > 0:
        time.sleep_ms(wait)
    now = time.ticks_ms()
    next_t = time.ticks_add(next_t, SAMPLE_MS)
    if time.ticks_diff(now, next_t) > 100:  # fell behind badly, resync
        next_t = time.ticks_add(now, SAMPLE_MS)

    sig = adc.read()
    N = time.ticks_diff(now, last_beat)

    # track trough/peak (trough only after 3/5 of last IBI, like the original)
    if sig < thresh and N > (ibi * 3) // 5 and sig < T:
        T = sig
    if sig > thresh and sig > P:
        P = sig

    # rising crossing after refractory -> beat (only if last cycle had real amplitude)
    if N > 250 and sig > thresh and not pulse and N > (ibi * 3) // 5:
        pulse = True
      # fall through only counts as a beat when the previous cycle swung enough
    if pulse and last_amp >= MIN_AMP and N > 250 and time.ticks_diff(now, last_beat) == N:
        new_ibi = N
        last_beat = now
        if second:
            second = False
            ibi = new_ibi
            rates = [new_ibi] * 5
        if first:
            first = False
            second = True
        else:
            ok = 300 <= new_ibi <= 2000
            if ok and rates:
                avg = sum(rates) // len(rates)
                if new_ibi * 4 > avg * 7 or new_ibi * 7 < avg * 4:
                    ok = False  # >75% jump from running average: outlier
            if ok:
                ibi = new_ibi
                rates.append(new_ibi)
                if len(rates) > 5:
                    rates.pop(0)
                bpm = 60000 * len(rates) // sum(rates)
                beat_count += 1
                print("BEAT ibi=%d bpm=%d" % (new_ibi, bpm))
                heart_on()
                beat_text(True)
                show_bpm(bpm)
                flash_until = time.ticks_add(now, 180)

    # falling edge: re-arm and set threshold midway between peak and trough
    if sig < thresh and pulse:
        pulse = False
        amp = P - T
        thresh = T + amp // 2
        P = thresh
        T = thresh

    # 2.5s without a beat: reset detector
    if N > 2500:
        mid = (smin + smax) // 2 if smax > smin else 2048
        thresh = P = T = mid
        last_beat = now
        first, second = True, False
        pulse = False
        rates = []
        if bpm:
            bpm = 0
            show_bpm(0)

    # end the beat flash
    if flash_until and time.ticks_diff(now, flash_until) >= 0:
        flash_until = 0
        heart_off()
        beat_text(False)

    # ---- waveform: autoscale window that creeps shut, snaps open ----
    smin = sig if sig < smin else (smin + 2 if smin + 2 < sig else smin)
    smax = sig if sig > smax else (smax - 2 if smax - 2 > sig else smax)
    lo, hi = smin, smax
    if hi - lo < 120:
        c = (hi + lo) // 2
        lo, hi = c - 60, c + 60
    y = WY1 - ((sig - lo) * (WH - 2)) // (hi - lo)
    if y < WY0:
        y = WY0
    if y > WY1:
        y = WY1

    ew = 4 if x + 4 <= W else W - x
    lcd.fillRect(x, WY0, ew, WH, BG)  # erase this column + gap ahead
    if x > 0 and prev_y is not None:
        lcd.drawLine(x - 1, prev_y, x, y, SALMON)
    else:
        lcd.drawPixel(x, y, SALMON)
    prev_y = y
    x += 1
    if x >= W:
        x = 0
        prev_y = None

    if time.ticks_diff(now, last_stat) >= 5000:
        last_stat = now
        print("STAT raw=%d min=%d max=%d thresh=%d bpm=%d beats=%d"
              % (sig, smin, smax, thresh, bpm, beat_count))
        if smax - smin < 30 and (smin > 3990 or smax < 105):
            print("WIRING? signal flat at rail - check signal->G2, VCC->3V3, GND->GND")
