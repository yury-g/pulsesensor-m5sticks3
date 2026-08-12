#!/bin/zsh
# play.sh — serve StickJump and open it in Chrome.
#
# Web Bluetooth only works in a secure context, so the page has to come from
# http://localhost (a file:// page silently has no navigator.bluetooth).
set -u
PORT=${PORT:-8123}
DIR=${0:a:h}

if ! curl -s -o /dev/null "http://localhost:$PORT"; then
  /usr/bin/python3 -m http.server "$PORT" --directory "$DIR" >/dev/null 2>&1 &
  for i in {1..40}; do
    curl -s -o /dev/null "http://localhost:$PORT" && break
    sleep 0.1
  done
fi

echo "StickJump: http://localhost:$PORT"
open -a "Google Chrome" "http://localhost:$PORT" 2>/dev/null || open "http://localhost:$PORT"
