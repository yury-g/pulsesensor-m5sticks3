# calib_motion.py — measure the real motion-deviation distribution so
# MOTION_GATE is set from data instead of a guess.
# Run through three 10s phases: hold still, gentle movement, vigorous shake.
import M5
import time
from machine import ADC, Pin

M5.begin()
lcd = M5.Lcd
lcd.setRotation(1)
lcd.fillScreen(0x000000)
lcd.setTextSize(2)

adc = ADC(Pin(2))
adc.atten(ADC.ATTN_11DB)

MOTION_ALPHA = 8
mean = None
PHASES = (("HOLD STILL", 10000), ("MOVE GENTLY", 10000), ("SHAKE HARD", 10000))

print("=== motion calibration: 3 phases x 10s ===")
for label, dur in PHASES:
    lcd.fillScreen(0x000000)
    lcd.setTextColor(0x00FFFF, 0x000000)
    lcd.setCursor(6, 50)
    lcd.print(label)
    print("--- PHASE: %s ---" % label)
    samples = []
    sig_min, sig_max = 1023, 0
    t_end = time.ticks_add(time.ticks_ms(), dur)
    nxt = time.ticks_ms()
    while time.ticks_diff(t_end, time.ticks_ms()) > 0:
        w = time.ticks_diff(nxt, time.ticks_ms())
        if w > 0:
            time.sleep_ms(w)
        nxt = time.ticks_add(nxt, 20)
        a = M5.Imu.getAccel()
        mag = (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]) ** 0.5
        if mean is None:
            mean = mag
        mean += (mag - mean) / MOTION_ALPHA
        samples.append(abs(mag - mean))
        s = adc.read() >> 2
        if s < sig_min:
            sig_min = s
        if s > sig_max:
            sig_max = s
    samples.sort()
    n = len(samples)
    print("  n=%d  min=%.4f  p50=%.4f  p90=%.4f  p99=%.4f  max=%.4f g"
          % (n, samples[0], samples[n // 2], samples[(n * 9) // 10],
             samples[(n * 99) // 100], samples[-1]))
    print("  ppg range during phase: %d..%d (span %d)" % (sig_min, sig_max, sig_max - sig_min))

lcd.fillScreen(0x000000)
lcd.setTextColor(0x00BE9A, 0x000000)
lcd.setCursor(6, 50)
lcd.print("DONE")
print("=== calibration complete ===")
print("Set MOTION_GATE between the still-phase p99 and the gentle-phase p50.")
