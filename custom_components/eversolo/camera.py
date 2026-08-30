"""Camera platform for eversolo — front-panel snapshot (#38) and full-rate live view (#39).

Replaces the shipped ``panel_screenshot`` image entity (formerly ``image.py``), which polled
``getScreenShot`` and, on the physical unit, woke the panel's screen and popped up a brief
on-screen dialog on every poll (RESEARCH.md's 2026-08-30 entry). This entity instead opens a
cast-mode session (:mod:`.cast_session`) — the same passive mechanism the phone/web apps use for
live mirroring — reads until a frame decodes, and serves that as a JPEG. Read-only by construction:
nothing here ever writes to the cast socket, which also accepts touch and key packets (#40's job).

The live-view stream (``handle_async_mjpeg_stream``) builds on the same session and decode step,
holding one cast session open for the life of a single stream request rather than polling
``async_camera_image`` on an interval — the base :class:`~homeassistant.components.camera.Camera`
class's default ``handle_async_mjpeg_stream`` would otherwise tear a session down and reopen it on
every frame. One session per request, not one shared across viewers: concurrent cast sessions are
known to work independently (#39's own measurement notes), and per-request keeps "release on
disconnect" trivial — :class:`~.cast_session.CastSession` already guarantees that in its
``__aexit__`` regardless of how the request ends.
"""

from __future__ import annotations

import asyncio

import av
from aiohttp import web

from homeassistant.components.camera import Camera
from homeassistant.const import CONTENT_TYPE_MULTIPART

from .api import EversoloApiClient, EversoloApiClientError
from .cast_session import CastSession, CastSessionError, PacketFlag, PacketType
from .const import LOGGER
from .coordinator import EversoloConfigEntry, EversoloDataUpdateCoordinator
from .entity import EversoloEntity

# Bound on how long a snapshot fetch waits for a decodable frame. Cold-start latency measured live
# (RESEARCH.md's screen-mirror decode-pipeline entry) is ~1.2s handshake-to-JPEG; keyframes arrive
# unprompted at ~1.4/s, so an 8s budget (also the prototype spike's own read window) leaves
# comfortable margin for a session that connects but answers slowly.
_SNAPSHOT_TIMEOUT_SECONDS = 8.0
# Packets read hunting for a decodable frame before giving up, as defence in depth alongside the
# timeout above — in case a socket stays open and connected but never actually yields video.
_MAX_PACKETS = 64

# Reconnect policy for the live stream (#39) when a session drops mid-read — the device sleeping,
# or the socket resetting. Capped exponential: quick to recover from a momentary blip, but not a
# tight spin loop if the device stays unreachable for a while. Retried for as long as the viewer's
# own request is still open; a device that never comes back leaves the stream idle rather than
# wedging the entity, and the viewer navigating away ends the request (and this loop) on its own.
_RECONNECT_BACKOFF_SECONDS = (1, 2, 4, 8, 16, 30)

# Bound on one read from an open live-stream socket. Real packet spacing is tens of milliseconds
# (#39's own measurement: median 24.2ms, max 58.5ms) with keyframes at ~1.4/s, so this is nowhere
# near that normal cadence — it exists only to catch a device that stalls (asleep, wedged) without
# ever closing the socket, where `read_packet()` would otherwise block forever and no OSError would
# ever arrive to trigger the reconnect path above.
_READ_STALL_TIMEOUT_SECONDS = 15.0


async def async_setup_entry(
    hass, entry: EversoloConfigEntry, async_add_devices
) -> None:
    """Set up the Camera platform.

    Unconditional, like the ``panel_screenshot`` entity it replaces: every unit with a front panel
    answers the cast-mode handshake one way or another, and a unit that genuinely can't stream
    degrades gracefully (``async_camera_image`` returns ``None``) rather than needing a capability
    gate to hide behind.
    """
    coordinator = entry.runtime_data
    async_add_devices([EversoloPanelCamera(coordinator)])


class _FrameCollector:
    """Feeds Annex-B H.264 payloads to PyAV and hands back the first frame it can decode.

    Payloads are fed one packet at a time, in wire order, not pre-joined: PyAV's own parser
    reassembles NAL units across packet boundaries, and a ``CONFIG`` packet is not required to
    decode — the ``KEY_FRAME`` that follows one repeats its SPS/PPS inline (RESEARCH.md,
    "Screen-mirror decode pipeline"). Kept as an object, not a function, so the codec's internal
    state persists across calls instead of re-parsing everything fed so far on every packet.

    The JPEG encoder is likewise created once and reused for the collector's whole life, not
    per frame — a snapshot (#38) only ever feeds one collector once, but the live stream (#39)
    feeds the same collector continuously at ~40 fps, where recreating PyAV's encoder setup on
    every frame would be pure waste.
    """

    def __init__(self) -> None:
        """Create a fresh H.264 decoder; the JPEG encoder is created lazily on the first frame."""
        self._codec = av.CodecContext.create("h264", "r")
        self._encoder: av.CodecContext | None = None

    def feed(self, payload: bytes) -> bytes | None:
        """Feed one packet's H.264 bytes; return a JPEG the moment a frame decodes."""
        for packet in self._codec.parse(payload):
            for frame in self._codec.decode(packet):
                return self._encode_jpeg(frame)
        return None

    def _encode_jpeg(self, frame: av.VideoFrame) -> bytes:
        """Encode one decoded video frame as a JPEG, using this collector's one persistent encoder.

        Uses PyAV's own ``mjpeg`` encoder rather than ``VideoFrame.to_image()``, which would pull
        in Pillow as a second dependency purely to re-save a JPEG PyAV can already write directly.
        Never flushes with ``encode(None)`` between frames — verified against the real capture
        fixture that ``encode(frame)`` alone already returns each frame's complete JPEG packet
        synchronously, since mjpeg is intra-frame with nothing to buffer; an explicit flush call
        instead marks the encoder finished, and every ``encode()`` after it raises ``EOFError``.
        """
        if self._encoder is None:
            self._encoder = av.CodecContext.create("mjpeg", "w")
            self._encoder.width = frame.width
            self._encoder.height = frame.height
            self._encoder.pix_fmt = "yuvj420p"
        frame.pts = None
        return b"".join(bytes(packet) for packet in self._encoder.encode(frame))


async def _async_capture_snapshot(client: EversoloApiClient) -> bytes | None:
    """Open a cast session, read until a frame decodes, and return it as JPEG bytes.

    Returns ``None`` rather than raising for every failure short of a genuine bug: the device is
    off or unreachable, the handshake fails, the socket closes early, or nothing decodable arrives
    inside the time/packet budget. The session is always closed on the way out regardless of which
    of those happened — :class:`~.cast_session.CastSession` tears itself down in ``__aexit__``,
    covering #38's "does not leak sockets" acceptance criterion even when this raises through it.
    """
    collector = _FrameCollector()
    try:
        async with (
            asyncio.timeout(_SNAPSHOT_TIMEOUT_SECONDS),
            CastSession(client) as session,
        ):
            for _ in range(_MAX_PACKETS):
                packet = await session.read_packet()
                if packet.flag == PacketFlag.END:
                    break
                if packet.type != PacketType.VIDEO:
                    continue
                if jpeg := collector.feed(packet.data):
                    return jpeg
    except (
        CastSessionError,
        EversoloApiClientError,
        TimeoutError,
        OSError,
    ) as exception:
        LOGGER.debug("panel camera snapshot failed: %s", exception)
        return None
    return None


async def _async_write_mjpeg_frame(response: web.StreamResponse, jpeg: bytes) -> None:
    """Write one JPEG as a multipart frame to an already-``prepare``d MJPEG response."""
    await response.write(
        b"--frameboundary\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
    )


async def _async_stream_live_view(
    client: EversoloApiClient, request: web.Request, response: web.StreamResponse
) -> None:
    """Feed cast-session video to ``response`` as MJPEG for as long as the request stays open.

    Holds one cast session open across many packets — the point of #39, versus #38's snapshot
    which opens and tears one down per fetch — and reconnects with backoff
    (:data:`_RECONNECT_BACKOFF_SECONDS`) on a mid-stream drop: the device sleeping (caught by
    :data:`_READ_STALL_TIMEOUT_SECONDS` even if the socket itself never closes), the socket
    resetting, a corrupt frame the decoder can't parse, or the device itself ending the session
    (``END``). That is #39's "reconnect/backoff handles the device sleeping or dropping the socket
    mid-stream without wedging the entity" criterion. Between attempts it checks whether the viewer
    is still there, so a device that never comes back does not loop forever after the viewer has
    already left.

    A viewer disconnecting abruptly is a different failure and must not trigger a reconnect against
    the device: it surfaces as ``response.write()`` raising ``ConnectionResetError`` — an
    ``OSError`` subclass, same as a device-socket drop, so it is deliberately *not* included in the
    reconnect-worthy exceptions below (the ``OSError`` catch inside the loop wraps only the read
    from the device socket, never the write to the viewer). It is left to propagate out of this
    function entirely — the ``CastSession`` this runs inside still tears down on the way out, and
    the HTTP layer above (core's ``async_get_mjpeg_stream``) already catches and logs that
    exception.
    """
    attempt = 0
    while request.transport is not None and not request.transport.is_closing():
        try:
            async with CastSession(client) as session:
                attempt = 0
                collector = _FrameCollector()
                while True:
                    try:
                        async with asyncio.timeout(_READ_STALL_TIMEOUT_SECONDS):
                            packet = await session.read_packet()
                    except (OSError, TimeoutError) as exception:
                        raise CastSessionError(
                            "cast socket stalled or errored while streaming"
                        ) from exception
                    if packet.flag == PacketFlag.END:
                        raise CastSessionError("cast session ended (END packet)")
                    if packet.type != PacketType.VIDEO:
                        continue
                    try:
                        jpeg = collector.feed(packet.data)
                    except av.FFmpegError as exception:
                        raise CastSessionError(
                            "cast stream decode failed"
                        ) from exception
                    if jpeg is None:
                        continue
                    await _async_write_mjpeg_frame(response, jpeg)
        except (CastSessionError, EversoloApiClientError) as exception:
            LOGGER.debug(
                "panel camera live stream dropped, reconnecting: %s", exception
            )
            delay = _RECONNECT_BACKOFF_SECONDS[
                min(attempt, len(_RECONNECT_BACKOFF_SECONDS) - 1)
            ]
            attempt += 1
            await asyncio.sleep(delay)


class EversoloPanelCamera(EversoloEntity, Camera):
    """The DMP-A8's front panel, over the cast-mode socket: still snapshot (#38) and live view (#39).

    A snapshot fetch opens and tears down its own session (#38's shape); the live-view stream
    holds one session open for the request's life instead (#39's shape) — the two never share a
    session, matching the device's own concurrent-session support.
    """

    _attr_translation_key = "panel_camera"
    _attr_content_type = "image/jpeg"

    def __init__(self, coordinator: EversoloDataUpdateCoordinator) -> None:
        """Initialize the panel camera."""
        EversoloEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_panel_camera"

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Fetch a fresh snapshot, or ``None`` if the device didn't hand one back."""
        return await _async_capture_snapshot(self.coordinator.client)

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        """Stream the front panel live, at the device's own capture rate (#39).

        Overrides the base :class:`~homeassistant.components.camera.Camera` implementation, which
        would otherwise poll ``async_camera_image`` on ``frame_interval`` — opening and tearing
        down a fresh cast session on every single frame, nowhere close to the device's real ~40
        fps. This holds one session open for as long as the dashboard card keeps the request open,
        which is exactly the "on demand" lifetime HA's camera card already gives a stream request
        (#39's design note) — no viewer-tracking of our own needed.
        """
        response = web.StreamResponse()
        response.content_type = CONTENT_TYPE_MULTIPART.format("--frameboundary")
        await response.prepare(request)
        await _async_stream_live_view(self.coordinator.client, request, response)
        return response
