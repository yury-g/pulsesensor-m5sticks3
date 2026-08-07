#!/bin/zsh
# stick.sh — one tool for everything M5StickS3.
#   stick.sh status              what's connected, what's it running
#   stick.sh run <file.py>       run script now (RAM, ~1s, not persistent)
#   stick.sh deploy <file.py>    make script the boot program (persists)
#   stick.sh watch [secs]        stream serial output (default 8s)
#   stick.sh flash <image.bin>   full firmware flash (rarely needed)
set -u
CMD=${1:-status}
PORT=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)
DIR=/Users/mininarwhal/MStackSTICK-S3
ESPTOOL=/Users/mininarwhal/Library/Arduino15/packages/m5stack/tools/esptool_py/5.0.dev1/esptool

[ -z "$PORT" ] && { echo "NO DEVICE on USB — replug the stick or press its power button"; exit 1; }

interrupt() {
  /usr/bin/python3 - "$PORT" <<'EOF'
import serial, sys, time
s = serial.Serial(sys.argv[1], 115200, timeout=1)
s.write(b'\x03\x03'); time.sleep(0.4); s.close()
EOF
}

case "$CMD" in
  status)
    interrupt
    /usr/bin/python3 -m mpremote connect "$PORT" resume exec "
import machine, os
print('port: $PORT')
print('chip id:', machine.unique_id().hex())
print('files:', os.listdir('/flash') if 'flash' in os.listdir('/') else os.listdir())
" 2>/dev/null || echo "$PORT present but no MicroPython REPL (C firmware or hung) — try: stick.sh flash"
    ;;
  run)
    interrupt
    /usr/bin/python3 -m mpremote connect "$PORT" resume run --no-follow "$2" && echo "RUNNING: $2 (until reset)"
    ;;
  deploy)
    interrupt
    /usr/bin/python3 -m mpremote connect "$PORT" resume fs cp "$2" :main.py && echo "DEPLOYED: $2 -> boots at every power-on"
    /usr/bin/python3 -m mpremote connect "$PORT" resume exec "import machine; machine.reset()" 2>/dev/null || true
    ;;
  watch)
    /usr/bin/python3 - "$PORT" "${2:-8}" <<'EOF'
import serial, sys, time
s = serial.Serial(sys.argv[1], 115200, timeout=1)
t = time.time()
while time.time() - t < float(sys.argv[2]):
    d = s.read(512)
    if d: print(d.decode(errors='replace'), end='')
print("\n[watch done]")
EOF
    ;;
  flash)
    "$DIR/flash.sh" "$2" "$PORT"
    ;;
  *)
    echo "usage: stick.sh {status|run|deploy|watch|flash}"; exit 1;;
esac
