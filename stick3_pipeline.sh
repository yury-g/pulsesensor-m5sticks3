#!/bin/zsh
# stick3_pipeline.sh — wait for a StickS3 on USB, then flash UIFlow + deploy pulse_cyd + verify
setopt null_glob
SCRATCH=/private/tmp/claude-501/-Users-mininarwhal-MStackSTICK-S3/bae72f77-ea1a-4bed-8f39-3b9689059486/scratchpad
ESPTOOL=/Users/mininarwhal/Library/Arduino15/packages/m5stack/tools/esptool_py/5.0.dev1/esptool
DIR=/Users/mininarwhal/MStackSTICK-S3

echo "$(date +%T) waiting for device (15 min window)..."
END=$((SECONDS + 900)); OK=0
while [ $SECONDS -lt $END ]; do
  PORTS=(/dev/cu.usbmodem*)
  if [ ${#PORTS} -gt 0 ]; then
    PORT=${PORTS[1]}
    echo "$(date +%T) device at $PORT — flashing StickS3 UIFlow"
    caffeinate -dims "$ESPTOOL" --chip esp32s3 --port "$PORT" --baud 460800 \
      --connect-attempts 10 --after watchdog-reset write-flash 0x0 "$SCRATCH/uiflow_sticks3.bin" 2>&1 | tail -1
    [ ${pipestatus[1]:-1} -eq 0 ] && { OK=1; break; }
    sleep 1
  fi
  sleep 0.5
done
[ $OK = 1 ] || { echo "FAILED: no flashable device in window"; exit 1; }
echo "$(date +%T) flash OK, waiting for UIFlow boot..."
sleep 6
PORTS=(/dev/cu.usbmodem*); PORT=${PORTS[1]:-}
[ -z "$PORT" ] && { echo "FAILED: port gone after flash"; exit 1; }

python3 - "$PORT" <<'EOF'
import serial, sys, time
s = serial.Serial(sys.argv[1], 115200, timeout=1)
s.write(b'\x03\x03'); time.sleep(0.6); s.close()
EOF
python3 -m mpremote connect "$PORT" resume exec "import esp32; n=esp32.NVS('uiflow'); n.set_u8('boot_option',0); n.commit(); print('boot_option=0 OK')" || { echo "FAILED: nvs"; exit 1; }
python3 -m mpremote connect "$PORT" resume fs cp "$DIR/pulse_cyd.py" :main.py || { echo "FAILED: deploy"; exit 1; }
echo "deployed pulse_cyd.py -> main.py"
python3 -m mpremote connect "$PORT" resume exec "import machine; machine.reset()" 2>/dev/null

python3 - "$PORT" <<'EOF'
import serial, sys, time
time.sleep(2)
for a in range(10):
    try:
        s = serial.Serial(sys.argv[1], 115200, timeout=1); break
    except Exception: time.sleep(1)
else:
    print("VERIFY: port did not return"); raise SystemExit
t=time.time(); got=b''
while time.time()-t < 10: got += s.read(512)
out = got.decode(errors='replace')
if "pulse_cyd running" in out: print("VERIFIED: PulseSensor dashboard boots on stick #3")
elif "Traceback" in out: print("APP ERROR:", out[-300:])
else: print("UNSURE, boot tail:", out[-200:])
EOF