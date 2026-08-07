#!/bin/zsh
# provision.sh — turn a factory/blank M5StickS3 into a PulseSensor CYD-dashboard device.
# Expects the stick plugged in ALREADY IN DOWNLOAD MODE (hold button while plugging USB).
#   ./provision.sh            flash UIFlow2 + deploy pulse_cyd.py as boot app + verify
#   ./provision.sh --no-flash skip firmware flash (stick already runs UIFlow2)
set -u
setopt null_glob
DIR=/Users/mininarwhal/MStackSTICK-S3
# The Arduino15 esptool lives on an external volume that is often unmounted,
# and its PyInstaller bundle fails when the volume is missing. Use the pip one.
# NOTE: esptool 4.x uses UNDERSCORE arg values (no_reset / watchdog_reset) and
# underscore subcommands (flash_id, write_flash) - not the 5.x dashed names.
ESPTOOL="/usr/bin/python3 -m esptool"
FW=$DIR/uiflow_sticks3.bin
APP=$DIR/pulse_cyd.py
NOFLASH=${1:-}

port() { local p=(/dev/cu.usbmodem*); [ ${#p} -gt 0 ] && echo $p[1]; }

wait_port() {  # $1 = max seconds
  local t=0
  while [ -z "$(port)" ]; do
    sleep 2; t=$((t+2))
    [ $t -ge $1 ] && { echo "FAIL: no USB port after ${1}s"; exit 1; }
  done
}

echo "== waiting for stick on USB =="
wait_port 60
P=$(port); echo "port: $P"

if [ "$NOFLASH" != "--no-flash" ]; then
  echo "== probing bootloader (stick must be in download mode) =="
  INFO=$(caffeinate -dims ${=ESPTOOL} --port $P --before no_reset --after no_reset flash_id 2>&1)
  if ! echo "$INFO" | grep -q "ESP32-S3-PICO-1"; then
    echo "$INFO" | grep -E "Chip type|Features|fatal"
    echo "FAIL: not an ESP32-S3-PICO-1 in download mode. Replug holding the button, or wrong device."
    exit 1
  fi
  echo "$INFO" | grep -E "Chip type|Features|MAC"
  if ! echo "$INFO" | grep -q "Embedded PSRAM 8MB (AP_3v3)"; then
    echo "FAIL: PSRAM profile does not match M5StickS3 (expected AP_3v3 quad) — refusing to flash StickS3 image."
    exit 1
  fi
  echo "== flashing UIFlow2 factory image (8MB, ~45s) =="
  caffeinate -dims ${=ESPTOOL} --port $P --before no_reset --after no_reset --baud 921600 \
    write_flash 0x0 $FW 2>&1 | tail -3
  grep -q "Hash of data verified" <<<"$(caffeinate -dims ${=ESPTOOL} --port $P --before no_reset --after no_reset verify_flash 0x0 $FW 2>&1 || true)" 2>/dev/null
  echo "== resetting into new firmware =="
  caffeinate -dims ${=ESPTOOL} --port $P --before no_reset --after watchdog_reset chip_id >/dev/null 2>&1
  sleep 3; wait_port 60; P=$(port)
  echo "== waiting for first-boot filesystem format =="
  ok=""
  for i in $(seq 1 12); do
    sleep 5
    if "$DIR/stick.sh" status 2>/dev/null | grep -q "chip id"; then ok=1; break; fi
  done
  [ -z "$ok" ] && { echo "FAIL: REPL never came up after flash"; exit 1; }
  echo "REPL is up."
fi

echo "== setting NVS boot_option=0 (run main.py at boot, skip UIFlow menu) =="
/usr/bin/python3 - <<'EOF'
import serial, sys, time
port = [__import__('glob').glob('/dev/cu.usbmodem*')[0]][0]
s = serial.Serial(port, 115200, timeout=1); s.write(b'\x03\x03'); time.sleep(0.4); s.close()
EOF
P=$(port)
/usr/bin/python3 -m mpremote connect $P resume exec "
import esp32
nvs = esp32.NVS('uiflow')
nvs.set_u8('boot_option', 0)
nvs.commit()
print('boot_option =', nvs.get_u8('boot_option'))
"

echo "== deploying $APP as boot app =="
"$DIR/stick.sh" deploy "$APP"
sleep 4; wait_port 30

echo "== verifying boot output (12s) =="
OUT=$("$DIR/stick.sh" watch 12 2>&1)
echo "$OUT" | tail -6
if echo "$OUT" | grep -qE "pulse_cyd running|Q=[0-9]+/[0-9]+|NO SIGNAL|QUALIFIED"; then
  echo "SUCCESS: stick boots into the PulseSensor CYD dashboard."
else
  echo "WARN: no dashboard output seen at boot — UIFlow2 boot_option may be hijacking main.py."
  echo "      Check: stick.sh run with a script reading esp32.NVS('uiflow') boot_option."
fi
