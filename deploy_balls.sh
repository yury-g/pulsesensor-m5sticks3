#!/bin/zsh
# deploy_balls.sh — make balls.py the boot program on the connected stick.
set -eu
PORT=${1:-/dev/cu.usbmodem31101}
cd /Users/mininarwhal/MStackSTICK-S3
python3 - "$PORT" <<'EOF'
import serial, sys, time
s = serial.Serial(sys.argv[1], 115200, timeout=1)
s.write(b'\x03\x03'); time.sleep(0.5); s.close()
EOF
python3 -m mpremote connect "$PORT" resume fs cp balls.py :main.py
python3 -m mpremote connect "$PORT" resume exec "import machine; machine.reset()" 2>/dev/null || true
echo "DEPLOYED — balls.py now runs at every boot"
