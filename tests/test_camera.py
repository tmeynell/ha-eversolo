"""Camera tests: the panel-view snapshot entity and its decode step (#38).

The decode test (`_FrameCollector`) runs against the real captured `capture.h264` fixture — no
live device, no fake bytes, proving the actual PyAV pipeline produces a real JPEG. The entity-level
tests fake `CastSession` itself (already covered on its own terms in `test_cast_session.py`), so
they can drive exactly the packet sequences each failure mode needs without a real socket. One
end-to-end test goes through the real HTTP seam instead, to prove the failure path holds all the
way from the handshake down.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import aiohttp
import av
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.camera import _FrameCollector
from custom_components.eversolo.cast_session import (
    CastPacket,
    CastSessionError,
    PacketFlag,
    PacketType,
)

from .helpers import (
    SETCASTMODE,
    entity_id_for,
    entity_object,
    prime_device,
    setup_integration,
)

CAPTURE_H264 = (Path(__file__).parent / "fixtures" / "capture.h264").read_bytes()


class _FakeCastSession:
    """Stand-in for `CastSession` — canned packets in, no real socket or handshake.

    `CastSession` itself is exercised on its own terms in `test_cast_session.py`; this only needs
    to look like one from the camera platform's point of view — an async context manager whose
    `read_packet()` hands back whatever a test wants next.
    """

    def __init__(
        self, packets: list[CastPacket] | None, aenter_error: Exception | None
    ) -> None:
        self._packets = list(packets or [])
        self._aenter_error = aenter_error

    async def __aenter__(self) -> _FakeCastSession:
        if self._aenter_error is not None:
            raise self._aenter_error
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    async def read_packet(self) -> CastPacket:
        if not self._packets:
            raise CastSessionError("fake cast session ran out of packets")
        return self._packets.pop(0)


def _patched_cast_session(
    packets: list[CastPacket] | None = None, aenter_error: Exception | None = None
):
    """Build a patch target for `camera.CastSession` that always returns the same canned fake."""
    return patch(
        "custom_components.eversolo.camera.CastSession",
        lambda client: _FakeCastSession(packets, aenter_error),
    )


# ------------------------------------------------------------------
# Decode — the real captured stream, no live device.
# ------------------------------------------------------------------


def test_frame_collector_decodes_the_captured_stream_to_a_real_jpeg() -> None:
    """Feeding the real `capture.h264` fixture produces a genuine JPEG, not a stub."""
    collector = _FrameCollector()

    jpeg = collector.feed(CAPTURE_H264)

    assert jpeg is not None
    assert jpeg.startswith(b"\xff\xd8")
    assert jpeg.endswith(b"\xff\xd9")


def test_frame_collector_decodes_at_the_streams_own_resolution() -> None:
    """The decoded frame is 960x360 — the stream's real size, not the 1600x600 panel.

    Nothing in the decode path consults `deviceWidth`/`deviceHeight` at all (#38's acceptance
    criterion); this proves the frame that comes out is genuinely stream-sized, by decoding the
    JPEG this module produced right back with PyAV and reading its own dimensions.
    """
    collector = _FrameCollector()
    jpeg = collector.feed(CAPTURE_H264)
    assert jpeg is not None

    with av.open(BytesIO(jpeg), format="mjpeg") as container:
        frames = list(container.decode(video=0))

    assert len(frames) == 1
    assert (frames[0].width, frames[0].height) == (960, 360)


def test_frame_collector_returns_none_until_a_frame_actually_decodes() -> None:
    """Bytes that never form a decodable frame yield ``None``, not an exception."""
    collector = _FrameCollector()

    assert collector.feed(b"\x00\x01\x02\x03not-h264-at-all") is None


# ------------------------------------------------------------------
# Entity — unconditional presence, translation, snapshot success/failure paths.
# ------------------------------------------------------------------


async def test_panel_camera_appears_unconditionally(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The entity exists from setup, named and typed as a still JPEG."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    entity_id = entity_id_for(hass, "_panel_camera")
    entity = entity_object(hass, entity_id)

    assert entity.content_type == "image/jpeg"
    state = hass.states.get(entity_id)
    assert state.attributes["friendly_name"].endswith("Panel view")


async def test_panel_camera_returns_the_decoded_snapshot(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``async_camera_image`` returns real JPEG bytes once a frame decodes."""
    prime_device(aioclient_mock)
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_panel_camera"))
    packets = [
        CastPacket(
            type=PacketType.VIDEO, flag=PacketFlag.KEY_FRAME, pts=1, data=CAPTURE_H264
        )
    ]

    with _patched_cast_session(packets=packets):
        image = await entity.async_camera_image()

    assert image is not None
    assert image.startswith(b"\xff\xd8")


async def test_panel_camera_stops_reading_once_the_device_sends_end(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An ``END`` packet with nothing decodable before it yields ``None``, not a hang."""
    prime_device(aioclient_mock)
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_panel_camera"))
    packets = [
        CastPacket(
            type=PacketType.VIDEO, flag=PacketFlag.FRAME, pts=1, data=b"garbage"
        ),
        CastPacket(type=PacketType.VIDEO, flag=PacketFlag.END, pts=2, data=b""),
    ]

    with _patched_cast_session(packets=packets):
        image = await entity.async_camera_image()

    assert image is None


async def test_panel_camera_ignores_audio_packets(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A leading audio packet is skipped, not fed to the video decoder."""
    prime_device(aioclient_mock)
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_panel_camera"))
    packets = [
        CastPacket(type=PacketType.AUDIO, flag=PacketFlag.FRAME, pts=0, data=b"pcm"),
        CastPacket(
            type=PacketType.VIDEO, flag=PacketFlag.KEY_FRAME, pts=1, data=CAPTURE_H264
        ),
    ]

    with _patched_cast_session(packets=packets):
        image = await entity.async_camera_image()

    assert image is not None
    assert image.startswith(b"\xff\xd8")


async def test_panel_camera_degrades_gracefully_when_the_session_wont_open(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A cast session that fails to open yields no picture, not an exception."""
    prime_device(aioclient_mock)
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_panel_camera"))

    with _patched_cast_session(aenter_error=CastSessionError("could not connect")):
        image = await entity.async_camera_image()

    assert image is None


async def test_panel_camera_degrades_gracefully_when_the_device_is_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """End to end through the real HTTP seam: a device that won't answer the handshake at all."""
    prime_device(
        aioclient_mock,
        {SETCASTMODE: {"exc": aiohttp.ClientError("device unreachable")}},
    )
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_panel_camera"))

    assert await entity.async_camera_image() is None


async def test_panel_camera_degrades_gracefully_when_firmware_lacks_the_endpoint(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Firmware without ``setcastmode`` answers 200 with a JSON error body, not a handshake.

    Same shape the old ``getScreenShot`` transport used for "this endpoint doesn't exist" — the
    handshake parse must turn that into ``None``, not an unhandled ``KeyError``.
    """
    prime_device(
        aioclient_mock,
        {SETCASTMODE: {"json": {"status": 804, "msg": "Url error"}}},
    )
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_panel_camera"))

    assert await entity.async_camera_image() is None
