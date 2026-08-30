"""Camera platform for eversolo — a still snapshot of the front panel (#38).

Replaces the shipped ``panel_screenshot`` image entity (formerly ``image.py``), which polled
``getScreenShot`` and, on the physical unit, woke the panel's screen and popped up a brief
on-screen dialog on every poll (RESEARCH.md's 2026-08-30 entry). This entity instead opens a
cast-mode session (:mod:`.cast_session`) — the same passive mechanism the phone/web apps use for
live mirroring — reads until a frame decodes, and serves that as a JPEG. Read-only by construction:
nothing here ever writes to the cast socket, which also accepts touch and key packets (#40's job).

This is also the foundation the full-rate live view (#39) builds on, reusing
:class:`.cast_session.CastSession` and the packet-decode step below.
"""

from __future__ import annotations

import asyncio

import av

from homeassistant.components.camera import Camera

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
    """

    def __init__(self) -> None:
        """Create a fresh H.264 decoder with no state yet fed to it."""
        self._codec = av.CodecContext.create("h264", "r")

    def feed(self, payload: bytes) -> bytes | None:
        """Feed one packet's H.264 bytes; return a JPEG the moment a frame decodes."""
        for packet in self._codec.parse(payload):
            for frame in self._codec.decode(packet):
                return _encode_jpeg(frame)
        return None


def _encode_jpeg(frame: av.VideoFrame) -> bytes:
    """Encode one decoded video frame as a JPEG.

    Uses PyAV's own ``mjpeg`` encoder rather than ``VideoFrame.to_image()``, which would pull in
    Pillow as a second dependency purely to re-save a JPEG PyAV can already write directly.
    """
    encoder = av.CodecContext.create("mjpeg", "w")
    encoder.width = frame.width
    encoder.height = frame.height
    encoder.pix_fmt = "yuvj420p"
    frame.pts = None
    return b"".join(bytes(packet) for packet in encoder.encode(frame)) + b"".join(
        bytes(packet) for packet in encoder.encode(None)
    )


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


class EversoloPanelCamera(EversoloEntity, Camera):
    """Snapshot of the DMP-A8's front panel, over the cast-mode socket (#38).

    A session is opened and torn down per snapshot; nothing here holds a socket open between
    fetches — that always-connected-while-viewed behaviour is #39's job for the live-view stream,
    not this entity's.
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
