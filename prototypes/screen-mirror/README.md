# PROTOTYPE — screen-mirror decode spike (`ha-eversolo#15`)

Throwaway. Lives on the `prototype/screen-mirror-spike` branch only, never merged to `main`.

**Question it answers:** can the integration pull the DMP-A8's front-panel display over the
port-9529 cast-mode mechanism and turn it into a JPEG an HA `camera` entity could serve?

**Verdict: yes — the whole pipeline works, first run, against `192.168.0.63` on `v1.1.80`
(2026-08-23).** See `out/frame.jpg` — the unit's screensaver clock, decoded from the socket.

## Run it

```bash
python -m venv .venv-spike
./.venv-spike/Scripts/python.exe -m pip install av
./.venv-spike/Scripts/python.exe prototypes/screen-mirror/spike.py [host]
```

## What the run proved

| Question | Answer |
|---|---|
| Handshake shape | `GET :9529/ZidooControlCenter/setcastmode?mode=1&version=1` → `{"status":200,"port":7007,"deviceWidth":1600,"deviceHeight":600,"videoWidth":960,"videoHeight":360,"isRotated":false,"ip":"192.168.0.63","isShowMenu":true}`. `deviceWidth`/`deviceHeight`/`isRotated` were **not** in the APK-derived field list — three new fields. |
| Framing | Exactly as `VideoPacket.fromArray` says: `[4-byte BE len][type][flag][8-byte BE pts][H.264]`. Parsed 45/45 packets with zero resync. |
| Stream format | Annex-B (`00 00 00 01`) straight off the wire. `CONFIG` (31 B) carries SPS/PPS, then a `KEY_FRAME` that **repeats** the SPS/PPS inline. |
| Keyframe cadence | 2 keyframes in 8 s, unprompted. **No `request_keyframe` equivalent is needed on this transport** — unlike port 9599's WS path. |
| Frame rate / bandwidth | **~40 fps, 319 KB/s ≈ 2.6 Mbit/s** on an active display (median pts delta 24 ms, keyframes 1.4/s). The ~5 fps / 4 KB/s first measured here was the **static screensaver** — a floor, not a typical case. See `measure.py`. |
| On-demand snapshot latency | handshake 230–250 ms + connect ~20 ms + first decoded JPEG 861–1053 ms = **~1.2 s total**, ~31 KB JPEG (3 trials). |
| Decode | PyAV 18.1.0 `CodecContext.create("h264","r")` decodes to 960x360 `yuv420p`; re-encoded to JPEG with PyAV's own `mjpeg` encoder — **no Pillow needed**. |
| Dependency | **Use `av==16.0.1` — HA core 2026.4.0's own pin** (`stream` component). Verified: 16.0.1 decodes the saved capture identically to 18.1.0. Already installed by core, so it costs no download. |
| Teardown | `setcastmode?mode=0&port=<port>` → `{"status":200}`; port is reallocated per session (7007 both runs). |

## Safety

The socket that carries video also accepts touch (16-byte) and key (4-byte) packets. The spike
**never writes to it** — read-only by construction, as a camera entity would be. Do not add a
write path here.

## What is still unproven

- Long-lived session behaviour: reconnect/backoff, and what happens when the device sleeps.
  (Concurrent sessions are answered — two clients stream independently on separate ports.)
- Whether the device can be asked for a **lower capture rate**. At 2.6 Mbit/s a persistent
  session is expensive; a rate control would make it cheap. Unprobed.
- `isShowMenu: true` — its effect on what gets captured was not investigated.

(`mode=0`-teardown-required and concurrent-session questions are answered — see `probes.py`.)
