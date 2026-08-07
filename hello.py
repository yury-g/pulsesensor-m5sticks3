import M5

M5.begin()
lcd = M5.Lcd
lcd.setRotation(1)          # landscape, USB port on the left
lcd.fillScreen(0x000000)

lcd.setTextColor(0x00FF00, 0x000000)
lcd.setTextSize(3)
lcd.setCursor(20, 40)
lcd.print("Hello")
lcd.setCursor(20, 80)
lcd.print("World!")

print("hello.py ran — screen updated")
