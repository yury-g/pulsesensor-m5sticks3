#!/bin/zsh
# flash.sh <merged.bin> — robust flasher for the M5StickS3 (ESP32-S3-PICO-1)
# Handles all firmware states: Arduino apps, crash-loops, MicroPython/UIFlow.
set -u
BIN="$1"
PORT=${2:-/dev/cu.usbmodem31101}
ESPTOOL=/Users/mininarwhal/Library/Arduino15/packages/m5stack/tools/esptool_py/5.0.dev1/esptool

try_flash() {
  caffeinate -dims "$ESPTOOL" --chip esp32s3 --port "$PORT" --baud 921600 \
    --connect-attempts 40 write-flash 0x0 "$BIN" 2>&1 | tail -2 | grep -q "Hash of data verified"
}

echo "[flash.sh] attempt 1: standard esptool reset"
try_flash && { echo "[flash.sh] OK"; exit 0; }

echo "[flash.sh] attempt 2: MicroPython/UIFlow REPL bootloader entry"
python3 - "$PORT" <<'EOF'
import serial, sys, time
try:
    s = serial.Serial(sys.argv[1], 115200, timeout=1)
    s.write(b'\x03\x03\r\n'); time.sleep(0.4)
    s.write(b'import machine\r\nmachine.bootloader()\r\n'); time.sleep(1.5)
except Exception as e:
    print("repl entry failed:", e)
EOF
sleep 2
# chip may now be in USB-OTG download mode (PID 0x4001) — kick it with a USB reset
python3 - <<'EOF'
import time
try:
    import usb.core, usb.backend.libusb1
    be = usb.backend.libusb1.get_backend(find_library=lambda x: '/opt/homebrew/lib/libusb-1.0.dylib')
    d = usb.core.find(idVendor=0x303a, backend=be)
    if d is not None:
        print("usb device pid:", hex(d.idProduct))
        try: d.reset()
        except Exception as e: print("reset:", e)  # errno 2 after re-enum is fine
except Exception as e:
    print("pyusb unavailable:", e)
time.sleep(2)
EOF
try_flash && { echo "[flash.sh] OK"; exit 0; }

echo "[flash.sh] attempt 3: retry loop"
for i in 1 2 3 4; do
  sleep 3
  try_flash && { echo "[flash.sh] OK"; exit 0; }
  echo "[flash.sh] retry $i failed"
done
echo "[flash.sh] FAILED — may need physical replug"
exit 1
