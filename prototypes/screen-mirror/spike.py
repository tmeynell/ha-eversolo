"""PROTOTYPE — throwaway spike for ha-eversolo#15. Not production code, not imported by the
integration, no tests. Answers one question: can we pull the DMP-A8's front-panel screen over the
port-9529 cast-mode mechanism and turn it into a JPEG that HA could serve from a `camera` entity?

Run:  .venv-spike/Scripts/python.exe prototypes/screen-mirror/spike.py [host]

READ-ONLY BY CONSTRUCTION. The same socket that carries video also accepts 16-byte touch packets
and 4-byte key packets (Scrcpy.touchEvent/sendKeyEvent, classes3.dex). This script never writes a
single byte to that socket — it only ever reads. Do not add a write path here.

Protocol, from the control app v1.15.65 (all classes in classes3.dex, scope: all 10 DEX complete):

  1. GET http://<host>:9529/ZidooControlCenter/setcastmode?mode=1&version=1   (ScrcpyApi.getStartUrl)
     -> {"port": int, "ip": str, "isShowMenu": bool, "videoWidth": int, "videoHeight": int}
     The port is allocated per session, not fixed.
  2. Raw TCP to (host, port). The device pushes immediately; the client sends nothing to start.
     Framing (Scrcpy.startInputConnection + VideoPacket.fromArray):
        [4-byte BE length N][N bytes of payload]
        payload[0]     = MediaPacket.Type   VIDEO=1, AUDIO=0
        payload[1]     = VideoPacket.Flag   FRAME=0, KEY_FRAME=1, CONFIG=2, END=4
        payload[2:10]  = presentationTimeStamp, 8-byte BE (ByteUtils.bytesToLong -> BigInteger, BE)
        payload[10:]   = H.264 bytes (CONFIG carries SPS/PPS in Annex-B form)
  3. GET http://<host>:9529/ZidooControlCenter/setcastmode?mode=0&port=<port>  (getStopUrl)
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import time
import urllib.request
from pathlib import Path

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.63"
OUT = Path(__file__).parent / "out"
READ_SECONDS = 8.0
TYPE_VIDEO = 1
FLAG_NAMES = {0: "FRAME", 1: "KEY_FRAME", 2: "CONFIG", 4: "END"}


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def start_session(host: str) -> dict:
    url = f"http://{host}:9529/ZidooControlCenter/setcastmode?mode=1&version=1"
    print(f"[handshake] GET {url}")
    body = _get(url)
    print(f"[handshake] <- {body}")
    return body


def stop_session(host: str, port: int) -> None:
    url = f"http://{host}:9529/ZidooControlCenter/setcastmode?mode=0&port={port}"
    try:
        print(f"[stop] GET {url}")
        print(f"[stop] <- {_get(url)}")
    except Exception as err:  # noqa: BLE001 - spike
        print(f"[stop] FAILED: {err!r} (port {port} may stay allocated)")


def read_exactly(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"socket closed with {len(buf)}/{n} bytes read")
        buf += chunk
    return bytes(buf)


def collect_packets(host: str, port: int, seconds: float) -> tuple[list[tuple[int, int, bytes]], bytes]:
    """Read framed packets for `seconds`. Returns (packets, annexb_bytes)."""
    packets: list[tuple[int, int, bytes]] = []
    stream = bytearray()
    print(f"[socket] connecting to {host}:{port}")
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        sock.settimeout(5)
        print("[socket] connected; reading (never writing)")
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            length = struct.unpack(">i", read_exactly(sock, 4))[0]
            if length <= 0:
                print(f"[socket] non-positive length {length}, skipping")
                continue
            payload = read_exactly(sock, length)
            ptype = payload[0]
            flag = payload[1]
            pts = int.from_bytes(payload[2:10], "big", signed=True)
            data = payload[10:]
            packets.append((ptype, flag, data))
            if len(packets) <= 6 or flag in (2, 4):
                print(
                    f"[pkt {len(packets):>3}] type={ptype} flag={flag}"
                    f" ({FLAG_NAMES.get(flag, '?')}) pts={pts} bytes={len(data)}"
                    f" head={data[:8].hex(' ')}"
                )
            if ptype == TYPE_VIDEO:
                if flag == 4:
                    print("[socket] END flag received")
                    break
                stream += data
    return packets, bytes(stream)


def decode_first_frame(annexb: bytes) -> Path | None:
    try:
        import av
    except ImportError:
        print("[decode] SKIPPED — PyAV not importable in this interpreter")
        return None

    codec = av.CodecContext.create("h264", "r")
    for packet in codec.parse(annexb):
        for frame in codec.decode(packet):
            OUT.mkdir(exist_ok=True)
            path = OUT / "frame.jpg"
            # Encode with PyAV's own mjpeg encoder rather than frame.to_image(), which would
            # pull in Pillow as a second dependency.
            enc = av.CodecContext.create("mjpeg", "w")
            enc.width, enc.height = frame.width, frame.height
            enc.pix_fmt = "yuvj420p"
            frame.pts = None
            jpeg = b"".join(bytes(p) for p in enc.encode(frame)) + b"".join(
                bytes(p) for p in enc.encode(None)
            )
            path.write_bytes(jpeg)
            print(
                f"[decode] {frame.width}x{frame.height} {frame.format.name}"
                f" -> {path} ({len(jpeg)} bytes)"
            )
            return path
    print("[decode] no frame decoded from the captured stream")
    return None


def main() -> int:
    session = start_session(HOST)
    port = session.get("port")
    if not port:
        print(f"[fatal] no port in handshake response: {session}")
        return 1
    ip = session.get("ip") or HOST
    try:
        packets, annexb = collect_packets(ip, port, READ_SECONDS)
    finally:
        stop_session(HOST, port)

    flags: dict[str, int] = {}
    for _ptype, flag, _data in packets:
        key = FLAG_NAMES.get(flag, str(flag))
        flags[key] = flags.get(key, 0) + 1
    print(f"\n[state] packets={len(packets)} by-flag={flags} h264-bytes={len(annexb)}")
    print(f"[state] handshake said videoWidth={session.get('videoWidth')}"
          f" videoHeight={session.get('videoHeight')} isShowMenu={session.get('isShowMenu')}")

    if not annexb:
        print("[verdict] NO VIDEO BYTES — the socket connected but the device pushed nothing")
        return 1

    OUT.mkdir(exist_ok=True)
    raw = OUT / "capture.h264"
    raw.write_bytes(annexb)
    print(f"[state] raw stream -> {raw}")

    jpg = decode_first_frame(annexb)
    print(f"\n[verdict] {'PIPELINE WORKS' if jpg else 'capture OK, decode did not produce a frame'}")
    return 0 if jpg else 1


if __name__ == "__main__":
    raise SystemExit(main())
