#!/bin/zsh
# run.sh <script.py> — instantly run a MicroPython script on the M5StickS3.
# No flashing: code is pushed over USB and executes from RAM (~2 seconds).
set -eu
SCRIPT="$1"
PORT=${2:-/dev/cu.usbmodem31101}
# interrupt whatever is running (UIFlow app, previous script)
python3 - "$PORT" <<'EOF'
import serial, sys, time
s = serial.Serial(sys.argv[1], 115200, timeout=1)
s.write(b'\x03\x03'); time.sleep(0.4); s.close()
EOF
exec python3 -m mpremote connect "$PORT" resume run "$SCRIPT"
