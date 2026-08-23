"""PROTOTYPE — measure real frame rate / bitrate of the 9529 cast stream.

Run: .venv-spike/Scripts/python.exe prototypes/screen-mirror/measure.py [host] [seconds]

The earlier ~5 fps figure was taken against a near-static screensaver. This reports the pts-delta
distribution so a change-driven stream is visible as such. Read-only; never writes to the socket.
"""

from __future__ import annotations

import json
import socket
import statistics
import struct
import sys
import time
import urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.63"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
BASE = f"http://{HOST}:9529"
FLAGS = {0: "FRAME", 1: "KEY_FRAME", 2: "CONFIG", 4: "END"}


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=8) as r:
        return json.loads(r.read().decode())


def recv_exact(s: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        c = s.recv(n - len(buf))
        if not c:
            raise ConnectionError("closed")
        buf += c
    return bytes(buf)


sess = get("/ZidooControlCenter/setcastmode?mode=1&version=1")
port = sess["port"]
print(f"session -> port {port}, stream {sess['videoWidth']}x{sess['videoHeight']}")

pts_list: list[int] = []
sizes: list[int] = []
flags: dict[str, int] = {}
t0 = time.monotonic()
try:
    with socket.create_connection((HOST, port), timeout=10) as s:
        s.settimeout(5)
        deadline = t0 + SECONDS
        while time.monotonic() < deadline:
            n = struct.unpack(">i", recv_exact(s, 4))[0]
            if n <= 0:
                continue
            p = recv_exact(s, n)
            flag = p[1]
            flags[FLAGS.get(flag, str(flag))] = flags.get(FLAGS.get(flag, str(flag)), 0) + 1
            pts_list.append(int.from_bytes(p[2:10], "big", signed=True))
            sizes.append(n - 10)
finally:
    elapsed = time.monotonic() - t0
    print(f"stop -> {get(f'/ZidooControlCenter/setcastmode?mode=0&port={port}')}")

total = sum(sizes)
print(f"\nwall time      {elapsed:.2f} s")
print(f"packets        {len(sizes)}  by-flag {flags}")
print(f"observed rate  {len(sizes) / elapsed:.1f} packets/s")
print(f"bytes          {total} ({total / elapsed / 1024:.1f} KB/s, {total * 8 / elapsed / 1000:.0f} kbit/s)")
print(f"payload size   min={min(sizes)} median={int(statistics.median(sizes))} max={max(sizes)}")

deltas = [b - a for a, b in zip(pts_list, pts_list[1:]) if b > a]
if deltas:
    ms = sorted(d / 1000 for d in deltas)
    print(f"\npts deltas (ms, n={len(ms)})")
    print(f"  min={ms[0]:.1f}  p25={ms[len(ms)//4]:.1f}  median={ms[len(ms)//2]:.1f}"
          f"  p75={ms[3*len(ms)//4]:.1f}  max={ms[-1]:.1f}")
    fast = sum(1 for d in ms if d < 60)
    print(f"  deltas under 60 ms (i.e. >16 fps instantaneous): {fast}/{len(ms)}"
          f" ({100 * fast / len(ms):.0f}%)")
    print(f"  implied fps at median delta: {1000 / ms[len(ms)//2]:.1f}")
