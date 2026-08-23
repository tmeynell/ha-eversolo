"""PROTOTYPE — coordinate input over the cast socket. Verified live 2026-08-23.

Run: .venv-spike/Scripts/python.exe prototypes/screen-mirror/tap.py X Y [host]

⚠️ THIS WRITES TO THE DEVICE. Every tap is a real touch event on the front panel and can change
playback, source or settings depending on what is under the coordinate. Unlike spike.py/measure.py
(read-only by construction), this script exists specifically to exercise the write path.

Coordinates are in the **stream's** space (960x360 as reported by the handshake's
videoWidth/videoHeight), NOT the panel's 1600x600 — the device upscales. Confirmed live: a tap at
(911, 24) hit the VU button that sits at panel (1519, 40); 911 == 1519 * 960/1600.

Wire format, from Scrcpy.touchEvent / Scrcpy.sendKeyEvent (classes3.dex, scope: all 10 DEX):
  touch: 16 bytes, four big-endian int32  [action, buttonState, x, y]
         action = Android MotionEvent constant: 0 DOWN, 1 UP, 2 MOVE
         buttonState = MotionEvent.getButtonState(), 0 for a finger
         x,y clamped to [0, width-1] / [0, height-1]
  key:   4 bytes, one big-endian int32 (the keycode alone)
A tap is DOWN then UP at the same coordinates.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import time
import urllib.request

X = int(sys.argv[1])
Y = int(sys.argv[2])
HOST = sys.argv[3] if len(sys.argv) > 3 else "192.168.0.63"
BASE = f"http://{HOST}:9529"

ACTION_DOWN, ACTION_UP = 0, 1


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=8) as r:
        return json.loads(r.read().decode())


def touch_packet(action: int, x: int, y: int, button: int = 0) -> bytes:
    return struct.pack(">iiii", action, button, x, y)


def key_packet(keycode: int) -> bytes:
    return struct.pack(">i", keycode)


sess = get("/ZidooControlCenter/setcastmode?mode=1&version=1")
port, w, h = sess["port"], sess["videoWidth"], sess["videoHeight"]
print(f"session port {port}  stream {w}x{h}  panel {sess['deviceWidth']}x{sess['deviceHeight']}")

if not (0 <= X < w and 0 <= Y < h):
    print(f"refusing: ({X},{Y}) is outside the {w}x{h} stream space")
    get(f"/ZidooControlCenter/setcastmode?mode=0&port={port}")
    raise SystemExit(1)

try:
    with socket.create_connection((HOST, port), timeout=8) as s:
        s.settimeout(8)
        s.recv(4)  # let the stream establish, as the app's reader thread does
        time.sleep(0.3)
        for action, name in ((ACTION_DOWN, "DOWN"), (ACTION_UP, "UP")):
            pkt = touch_packet(action, X, Y)
            print(f"-> {name:4} {pkt.hex(' ')}")
            s.sendall(pkt)
            time.sleep(0.06)
        time.sleep(0.4)
finally:
    print(f"stop -> {get(f'/ZidooControlCenter/setcastmode?mode=0&port={port}')}")
