# probe_buttons.py — which button object is the front blue button?
import M5
import time

M5.begin()
lcd = M5.Lcd
lcd.setRotation(1)
lcd.fillScreen(0x000000)
lcd.setTextSize(2)
lcd.setTextColor(0x6EF58A, 0x000000)
lcd.setCursor(6, 40)
lcd.print("PRESS BUTTONS")

names = [n for n in ("BtnA", "BtnB", "BtnC", "BtnPWR", "BtnEXT") if hasattr(M5, n)]
print("button objects present:", names)
print("press each button; 20s...")

seen = {}
t_end = time.ticks_add(time.ticks_ms(), 20000)
while time.ticks_diff(t_end, time.ticks_ms()) > 0:
    M5.update()
    for n in names:
        b = getattr(M5, n)
        try:
            if b.wasPressed():
                seen[n] = seen.get(n, 0) + 1
                print("PRESSED:", n, "count", seen[n])
                lcd.fillScreen(0x000000)
                lcd.setCursor(6, 40)
                lcd.setTextColor(0xFFE34D, 0x000000)
                lcd.print(n)
        except Exception as e:
            print(n, "err", e)
    time.sleep_ms(20)

print("RESULT:", seen if seen else "NO BUTTON PRESSES DETECTED")
