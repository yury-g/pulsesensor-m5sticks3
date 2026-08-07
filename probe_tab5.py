# probe_tab5.py — what can the Tab5 (ESP32-P4) actually do?
# The P4 has NO built-in radio; Wi-Fi comes from an ESP32-C6 companion over
# SDIO (ESP-Hosted). The whole remote-display design hinges on whether
# MicroPython exposes espnow (or at least a working WLAN) through that.
import sys

print("=" * 46)
print("platform:", sys.platform)
print("impl:", sys.implementation)

try:
    import M5
    M5.begin()
    lcd = M5.Lcd
    print("M5 board id:", M5.getBoard())
    print("screen:", lcd.width(), "x", lcd.height())
    for rot in (0, 1):
        lcd.setRotation(rot)
        print("  rotation %d -> %dx%d" % (rot, lcd.width(), lcd.height()))
    lcd.setRotation(1)
    lcd.setTextSize(1)
    print("font at size1: height", lcd.fontHeight(),
          " 'PulseSensor' width", lcd.textWidth("PulseSensor"))
    for s in (2, 3, 4, 6):
        lcd.setTextSize(s)
        print("  size %d: height %d, '888' width %d"
              % (s, lcd.fontHeight(), lcd.textWidth("888")))
    print("M5 attrs:", sorted(a for a in dir(M5) if not a.startswith("_")))
except Exception as e:
    print("M5 FAILED:", e)

print("--- wireless ---")
try:
    import espnow
    print("espnow module: YES", [a for a in dir(espnow) if not a.startswith("_")][:8])
except Exception as e:
    print("espnow module: NO (%s)" % e)

try:
    import network
    print("network module: YES")
    print("  attrs:", [a for a in dir(network) if not a.startswith("_")][:12])
    try:
        w = network.WLAN(network.STA_IF)
        w.active(True)
        print("  WLAN active ->", w.active())
        try:
            print("  mac:", w.config("mac").hex())
        except Exception as e2:
            print("  mac read failed:", e2)
        try:
            nets = w.scan()
            print("  scan found %d networks" % len(nets))
            for n in nets[:3]:
                print("   ", n[0], "rssi", n[3])
        except Exception as e2:
            print("  scan failed:", e2)
    except Exception as e2:
        print("  WLAN failed:", e2)
except Exception as e:
    print("network module: NO (%s)" % e)

try:
    import bluetooth
    print("bluetooth module: YES")
except Exception as e:
    print("bluetooth module: NO (%s)" % e)

print("PROBE COMPLETE")
print("=" * 46)
