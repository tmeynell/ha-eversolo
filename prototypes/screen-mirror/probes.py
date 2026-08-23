"""PROTOTYPE — read-only probes for the open questions on ha-eversolo#15.

Run: .venv-spike/Scripts/python.exe prototypes/screen-mirror/probes.py [host]

Never writes to a cast socket. The only writes are `setcastmode` mode=1/mode=0, which are
session lifecycle, not device configuration.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import time
import urllib.error
import urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.63"
BASE = f"http://{HOST}:9529"


def get(path: str) -> dict | str:
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=8) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return f"ERR {e!r}"
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body[:300]


def start(version: int = 1) -> dict | str:
    return get(f"/ZidooControlCenter/setcastmode?mode=1&version={version}")


def stop(port: int) -> dict | str:
    return get(f"/ZidooControlCenter/setcastmode?mode=0&port={port}")


def first_frames(host: str, port: int, want: int = 3, timeout: float = 6.0):
    """Read up to `want` packets. Returns list of (flag, nbytes) or an error string."""
    out = []
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            deadline = time.monotonic() + timeout
            while len(out) < want and time.monotonic() < deadline:
                head = s.recv(4)
                if len(head) < 4:
                    break
                n = struct.unpack(">i", head)[0]
                if n <= 0:
                    continue
                buf = bytearray()
                while len(buf) < n:
                    c = s.recv(n - len(buf))
                    if not c:
                        break
                    buf += c
                out.append((buf[1], len(buf) - 10))
    except Exception as e:  # noqa: BLE001
        return f"ERR {e!r}"
    return out


print("=" * 72)
print("PROBE 1 — getDeviceSupportedFeatures (capability-gating source)")
print("=" * 72)
for path in (
    "/ZidooControlCenter/getDeviceSupportedFeatures",
    "/ZidooControlCenter/getSupportedFeatures",
):
    print(f"{path}\n  -> {str(get(path))[:600]}\n")

print("=" * 72)
print("PROBE 2 — two concurrent cast sessions")
print("=" * 72)
a = start()
print(f"session A -> {a}")
b = start()
print(f"session B -> {b}")
pa = a.get("port") if isinstance(a, dict) else None
pb = b.get("port") if isinstance(b, dict) else None
if pa and pb:
    print(f"same port? {pa == pb}   (A={pa} B={pb})")
    print(f"A frames: {first_frames(HOST, pa)}")
    print(f"B frames: {first_frames(HOST, pb)}")
for p in {pa, pb} - {None}:
    print(f"stop({p}) -> {stop(p)}")

print()
print("=" * 72)
print("PROBE 3 — is mode=0 teardown required to reclaim a port?")
print("=" * 72)
s1 = start()
p1 = s1.get("port") if isinstance(s1, dict) else None
print(f"open   -> port {p1}")
if p1:
    sock = socket.create_connection((HOST, p1), timeout=6)
    sock.recv(4)
    sock.close()
    print("dropped the socket WITHOUT calling mode=0")
    time.sleep(1.5)
    s2 = start()
    p2 = s2.get("port") if isinstance(s2, dict) else None
    print(f"reopen -> port {p2}   (reused same port? {p1 == p2})")
    print(f"frames on reopened session: {first_frames(HOST, p2) if p2 else 'n/a'}")
    for p in {p1, p2} - {None}:
        print(f"stop({p}) -> {stop(p)}")

print()
print("=" * 72)
print("PROBE 4 — does version=2 change the handshake / isShowMenu?")
print("=" * 72)
v2 = start(version=2)
print(f"version=2 -> {v2}")
pv = v2.get("port") if isinstance(v2, dict) else None
if pv:
    print(f"frames: {first_frames(HOST, pv)}")
    print(f"stop({pv}) -> {stop(pv)}")
