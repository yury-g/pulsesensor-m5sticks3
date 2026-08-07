# imu_check.py — is M5.Imu.getAccel() live, or cached until M5.update()?
# Button-paced so the capture lines up with the user actually moving the stick.
import M5
import time

M5.begin()
lcd = M5.Lcd
lcd.setRotation(1)


def banner(msg, col=0x00FFFF):
    lcd.fillScreen(0x000000)
    lcd.setTextColor(col, 0x000000)
    lcd.setTextSize(2)
    lcd.setCursor(6, 40)
    lcd.print(msg)


def capture(label, call_update, secs=8):
    banner(label + "\n MOVE IT!", 0xF87C00)
    print("--- %s (M5.update %s) ---" % (label, "ON" if call_update else "OFF"))
    mags = []
    raws = set()
    t_end = time.ticks_add(time.ticks_ms(), secs * 1000)
    while time.ticks_diff(t_end, time.ticks_ms()) > 0:
        if call_update:
            M5.update()
        a = M5.Imu.getAccel()
        raws.add((a[0], a[1], a[2]))
        mags.append((a[0] * a[0] + a[1] * a[1] + a[2] * a[2]) ** 0.5)
        time.sleep_ms(20)
    lo, hi = min(mags), max(mags)
    print("  samples=%d  distinct readings=%d" % (len(mags), len(raws)))
    print("  |accel| min=%.4f max=%.4f SPAN=%.4f g" % (lo, hi, hi - lo))
    if len(raws) < 5:
        print("  >>> IMU IS FROZEN - getAccel() is not refreshing")
    elif hi - lo < 0.05:
        print("  >>> readings change but magnitude barely moves")
    else:
        print("  >>> IMU IS LIVE and sees the motion")
    return hi - lo


banner("Press BtnA\n to start", 0x00BE9A)
print("waiting for BtnA...")
while True:
    M5.update()
    if M5.BtnA.wasPressed():
        break
    time.sleep_ms(30)

a = capture("TEST 1/2", False)
banner("PAUSE", 0x8C8E8C)
time.sleep(2)
b = capture("TEST 2/2", True)

banner("DONE", 0x00BE9A)
print("=== RESULT ===")
print("without M5.update(): span %.4f g" % a)
print("with    M5.update(): span %.4f g" % b)
if b > a * 3 and b > 0.05:
    print("VERDICT: M5.update() IS REQUIRED to refresh the IMU")
elif a > 0.05:
    print("VERDICT: IMU is live either way")
else:
    print("VERDICT: no motion seen in either test - was the stick actually moved?")
