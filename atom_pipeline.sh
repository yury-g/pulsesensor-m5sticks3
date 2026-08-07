#!/bin/zsh
# atom_pipeline.sh — wait for AtomS3R replug, then: flash UIFlow -> deploy pulse_cyd -> verify
set -u
SCRATCH=/private/tmp/claude-501/-Users-mininarwhal-MStackSTICK-S3/bae72f77-ea1a-4bed-8f39-3b9689059486/scratchpad
ESPTOOL=/Users/mininarwhal/Library/Arduino15/packages/m5stack/tools/esptool_py/5.0.dev1/esptool
DIR=/Users/mininarwhal/MStackSTICK-S3

for i in $(seq 1 200); do
  PORT=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)
  [ -n "$PORT" ] && break
  sleep 3
done
[ -z "${PORT:-}" ] && { echo "PIPELINE FAILED: no device in 10 min"; exit 1; }
echo "STEP1 port: $PORT"
sleep 2

echo "STEP2 flashing UIFlow AtomS3R..."
FLASHED=0
for i in 1 2 3 4 5; do
  caffeinate -dims "$ESPTOOL" --chip esp32s3 --port "$PORT" --baud 460800 \
    --connect-attempts 40 --after watchdog-reset write-flash 0x0 "$SCRATCH/uiflow_atoms3r.bin" 2>&1 \
    | tail -2 | grep -q "Hash of data verified" && { FLASHED=1; break; }
  echo "  flash attempt $i failed"; sleep 4
done
[ "$FLASHED" = 1 ] || { echo "PIPELINE FAILED: flash"; exit 1; }
echo "STEP2 flash OK (watchdog reset)"
sleep 6

echo "STEP3 setting boot_option + deploying pulse_cyd.py..."
python3 - "$PORT" <<'EOF'
import serial, sys, time
s = serial.Serial(sys.argv[1], 115200, timeout=1)
s.write(b'\x03\x03'); time.sleep(0.6); s.close()
EOF
python3 -m mpremote connect "$PORT" resume exec "import esp32; n=esp32.NVS('uiflow'); n.set_u8('boot_option',0); n.commit(); print('boot_option=0')" || { echo "PIPELINE FAILED: nvs"; exit 1; }
python3 -m mpremote connect "$PORT" resume fs cp "$DIR/pulse_cyd.py" :main.py || { echo "PIPELINE FAILED: cp"; exit 1; }
python3 -m mpremote connect "$PORT" resume exec "import machine; machine.reset()" 2>/dev/null

echo "STEP4 verifying boot..."
python3 - "$PORT" <<'EOF'
import serial, time, sys
time.sleep(2)
for a in range(10):
    try:
        s = serial.Serial(sys.argv[1], 115200, timeout=1); break
    except Exception:
        time.sleep(1)
else:
    print("PIPELINE WARN: port gone after reset"); raise SystemExit
t = time.time(); got = b''
while time.time() - t < 10:
    got += s.read(512)
out = got.decode(errors='replace')
if "pulse_cyd running" in out:
    print("PIPELINE OK: pulse_cyd boots on the AtomS3R")
elif "Traceback" in out:
    print("PIPELINE ERROR in app:"); print(out[-500:])
else:
    print("PIPELINE UNSURE, boot tail:", out[-200:])
EOF
