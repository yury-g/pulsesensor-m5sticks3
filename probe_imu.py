# probe_imu.py — one-shot hardware probe: BMI270 IMU + speaker + ADC timing budget.
# Answers, on real hardware: how do we read the IMU from UIFlow2 MicroPython,
# does M5.Speaker actually work on the ES8311 codec, and does reading the IMU
# every cycle blow the 50 Hz sample cadence the detector depends on?
import M5
import time

M5.begin()
print("=" * 46)
print("M5 attrs:", sorted(a for a in dir(M5) if not a.startswith("_")))

# ---- 1. is there an Imu binding? ----
imu = getattr(M5, "Imu", None)
print("M5.Imu present:", imu is not None)
if imu is not None:
    print("M5.Imu attrs:", sorted(a for a in dir(imu) if not a.startswith("_")))
    for name in ("getAccel", "getAccelData", "getGyro", "isEnabled", "update"):
        fn = getattr(imu, name, None)
        if fn is None:
            continue
        try:
            print("  M5.Imu.%s() ->" % name, fn())
        except Exception as e:
            print("  M5.Imu.%s() FAILED:" % name, e)

# ---- 2. other possible module paths ----
for mod, attr in (("hardware", "IMU"), ("unit", "IMUUnit"), ("imu", "IMU")):
    try:
        m = __import__(mod)
        print("module %s has %s:" % (mod, attr), hasattr(m, attr))
    except Exception as e:
        print("module %s unavailable (%s)" % (mod, e))

# ---- 3. raw I2C: is the BMI270 really at 0x68? ----
try:
    from machine import I2C, Pin
    i2c = I2C(0, scl=Pin(48), sda=Pin(47), freq=100000)
    found = i2c.scan()
    print("I2C scan:", [hex(a) for a in found])
    print("  0x68 (BMI270 IMU):", 0x68 in found)
    print("  0x18 (ES8311 codec):", 0x18 in found)
    print("  0x6e (M5PM1 power):", 0x6E in found)
    if 0x68 in found:
        # BMI270 CHIP_ID register 0x00 should read 0x24
        cid = i2c.readfrom_mem(0x68, 0x00, 1)
        print("  BMI270 CHIP_ID: 0x%02x (expect 0x24)" % cid[0])
        # PWR_CTRL 0x7D: enable acc(bit2)+gyr(bit1); needs init for full use,
        # but chip id alone confirms the bus works.
except Exception as e:
    print("raw I2C probe FAILED:", e)

# ---- 4. speaker: does M5.Speaker exist and make sound? ----
spk = getattr(M5, "Speaker", None)
print("M5.Speaker present:", spk is not None)
if spk is not None:
    print("M5.Speaker attrs:", sorted(a for a in dir(spk) if not a.startswith("_")))
    try:
        spk.begin()
        spk.setVolume(128)
        print("playing 4-note chime NOW - listen")
        for f, d in ((262, 120), (392, 120), (523, 120), (659, 200)):
            spk.tone(f, d)
            time.sleep_ms(d + 30)
        print("chime done")
    except Exception as e:
        print("M5.Speaker FAILED:", e)

# ---- 5. timing budget: cost of an IMU read vs the 20ms sample period ----
if imu is not None:
    read = getattr(imu, "getAccel", None) or getattr(imu, "getAccelData", None)
    if read:
        try:
            t0 = time.ticks_us()
            for _ in range(50):
                read()
            dt = time.ticks_diff(time.ticks_us(), t0) / 50.0
            print("IMU read cost: %.0f us/call (budget is 20000 us/sample)" % dt)
        except Exception as e:
            print("IMU timing FAILED:", e)

from machine import ADC, Pin as P2
adc = ADC(P2(2))
adc.atten(ADC.ATTN_11DB)
t0 = time.ticks_us()
for _ in range(50):
    adc.read()
print("ADC read cost: %.0f us/call" % (time.ticks_diff(time.ticks_us(), t0) / 50.0))
print("PROBE COMPLETE")
print("=" * 46)
