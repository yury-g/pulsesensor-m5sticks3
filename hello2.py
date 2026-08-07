import M5

M5.begin()
lcd = M5.Lcd
lcd.setRotation(1)
lcd.fillScreen(0x001030)     # dark blue background

lcd.setTextColor(0xFFFFFF, 0x001030)
lcd.setTextSize(2)
lcd.setCursor(12, 20)
lcd.print("Iteration #2")

lcd.setTextColor(0xFF8040, 0x001030)  # salmon
lcd.setTextSize(3)
lcd.setCursor(12, 60)
lcd.print("No choking!")

w = lcd.width()
h = lcd.height()
lcd.drawRect(4, 4, w - 8, h - 8, 0x00FFFF)
print("hello2 ran, display is", w, "x", h)
