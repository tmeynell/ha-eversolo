"""Camera tests: the panel-view snapshot entity and its decode step (#38).

The decode test (`_FrameCollector`) runs against the real captured `capture.h264` fixture — no
live device, no fake bytes, proving the actual PyAV pipeline produces a real JPEG. The entity-level
tests fake `CastSession` itself (already covered on its own terms in `test_cast_session.py`), so
they can drive exactly the packet sequences each failure mode needs without a real socket. One
end-to-end test goes through the real HTTP seam instead, to prove the failure path holds all the
way from the handshake down.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import av
import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.camera import _FrameCollector, _async_stream_live_view
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
    `read_packet()` hands back whatever a test wants next. `entered`/`exited` let a live-stream test
    assert a session was actually torn down, since that is `CastSession`'s own `__aexit__` job and
    this fake stands in for it.
    """

    def __init__(
        self, packets: list[CastPacket] | None, aenter_error: Exception | None
    ) -> None:
        self._packets = list(packets or [])
        self._aenter_error = aenter_error
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _FakeCastSession:
        if self._aenter_error is not None:
            raise self._aenter_error
        self.entered = True
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self.exited = True
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


def _patched_cast_session_sequence(sessions: list[_FakeCastSession]):
    """Patch `camera.CastSession` to hand back each fake in turn across successive opens.

    Models a live stream that reconnects: the first fake can simulate the session that drops, the
    next the one that replaces it — each with its own packets or `aenter_error`. The patch object
    is a `Mock` (not a bare lambda) so a test can assert on `call_count` — how many times the loop
    actually tried to open a session.
    """
    queue = list(sessions)
    return patch(
        "custom_components.eversolo.camera.CastSession",
        Mock(side_effect=lambda client: queue.pop(0)),
    )


class _FakeTransport:
    """Controls the disconnect signal `_async_stream_live_view` polls between reconnect attempts.

    `is_closing()` reports open for the first `closing_after` checks, then closed forever —
    letting a test bound exactly how many reconnect attempts the loop gets before it must stop.
    """

    def __init__(self, closing_after: int | None = None) -> None:
        self._checks = 0
        self._closing_after = closing_after

    def is_closing(self) -> bool:
        self._checks += 1
        if self._closing_after is None:
            return False
        return self._checks > self._closing_after


class _FakeStreamRequest:
    """Just enough of `aiohttp.web.Request` for the live-stream loop's disconnect check."""

    def __init__(self, transport: _FakeTransport | None) -> None:
        self.transport = transport


class _FakeMjpegResponse:
    """Stand-in for `aiohttp.web.StreamResponse` — records written frames, no real socket.

    `fail_after` raises `ConnectionResetError` on the write that would make `len(frames)` reach
    it, standing in for a viewer that abruptly disconnects mid-stream.
    """

    def __init__(self, fail_after: int | None = None) -> None:
        self.frames: list[bytes] = []
        self._fail_after = fail_after

    async def write(self, chunk: bytes) -> None:
        if self._fail_after is not None and len(self.frames) >= self._fail_after:
            raise ConnectionResetError("viewer gone")
        self.frames.append(chunk)


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


# ------------------------------------------------------------------
# Live stream — one held-open session, reconnect/backoff, disconnect handling (#39).
# ------------------------------------------------------------------


async def test_live_stream_writes_every_decoded_frame_from_one_session(
    hass: HomeAssistant,
) -> None:
    """A single session decoding several packets writes several frames, not just the first.

    Proves the full-rate shape: the base `Camera` class's default `handle_async_mjpeg_stream`
    would open a fresh session per frame; this reuses one `_FrameCollector` across the whole
    session, so two decodable packets yield two written frames without a reconnect in between.
    """
    packets = [
        CastPacket(
            type=PacketType.VIDEO, flag=PacketFlag.KEY_FRAME, pts=1, data=CAPTURE_H264
        ),
        CastPacket(
            type=PacketType.VIDEO, flag=PacketFlag.KEY_FRAME, pts=2, data=CAPTURE_H264
        ),
    ]
    request = _FakeStreamRequest(_FakeTransport(closing_after=1))
    response = _FakeMjpegResponse()

    with (
        _patched_cast_session(packets=packets),
        patch("custom_components.eversolo.camera.asyncio.sleep", AsyncMock()),
    ):
        await _async_stream_live_view(client=None, request=request, response=response)

    assert len(response.frames) == 2
    for frame in response.frames:
        assert b"Content-Type: image/jpeg" in frame
        assert frame.endswith(b"\xff\xd9\r\n")


async def test_live_stream_reconnects_after_a_mid_stream_drop(
    hass: HomeAssistant,
) -> None:
    """A session that runs dry mid-stream is replaced by a fresh one, not left dead.

    The dropped session's packets run out (`_FakeCastSession` raises `CastSessionError`, standing
    in for the socket resetting or the device sleeping) and the loop opens a second session that
    picks up streaming — #39's "reconnect/backoff... without wedging the entity" criterion.
    """
    dropped = _FakeCastSession(packets=[], aenter_error=None)
    replacement = _FakeCastSession(
        packets=[
            CastPacket(
                type=PacketType.VIDEO,
                flag=PacketFlag.KEY_FRAME,
                pts=1,
                data=CAPTURE_H264,
            )
        ],
        aenter_error=None,
    )
    request = _FakeStreamRequest(_FakeTransport(closing_after=2))
    response = _FakeMjpegResponse()

    with (
        _patched_cast_session_sequence([dropped, replacement]),
        patch(
            "custom_components.eversolo.camera.asyncio.sleep", AsyncMock()
        ) as sleep_mock,
    ):
        await _async_stream_live_view(client=None, request=request, response=response)

    assert dropped.entered and dropped.exited
    assert replacement.entered and replacement.exited
    assert len(response.frames) == 1
    sleep_mock.assert_awaited()


async def test_live_stream_reconnects_after_a_frame_that_fails_to_decode(
    hass: HomeAssistant,
) -> None:
    """A packet PyAV can't decode reconnects, rather than killing the stream outright.

    `_FrameCollector.feed` is patched to raise `av.FFmpegError` on its first call only — the
    shape a corrupted mid-stream payload or a post-reconnect codec-state mismatch would take —
    proving that failure is treated the same as a socket-level drop, not left to propagate past
    the reconnect loop entirely.
    """
    broken = _FakeCastSession(
        packets=[
            CastPacket(
                type=PacketType.VIDEO, flag=PacketFlag.KEY_FRAME, pts=1, data=b"garbage"
            )
        ],
        aenter_error=None,
    )
    replacement = _FakeCastSession(
        packets=[
            CastPacket(
                type=PacketType.VIDEO,
                flag=PacketFlag.KEY_FRAME,
                pts=2,
                data=CAPTURE_H264,
            )
        ],
        aenter_error=None,
    )
    request = _FakeStreamRequest(_FakeTransport(closing_after=2))
    response = _FakeMjpegResponse()

    with (
        _patched_cast_session_sequence([broken, replacement]),
        patch(
            "custom_components.eversolo.camera._FrameCollector.feed",
            Mock(
                side_effect=[
                    av.error.InvalidDataError(-22, "corrupt frame"),
                    b"\xff\xd8fake-jpeg\xff\xd9",
                ]
            ),
        ),
        patch("custom_components.eversolo.camera.asyncio.sleep", AsyncMock()),
    ):
        await _async_stream_live_view(client=None, request=request, response=response)

    assert broken.entered and broken.exited
    assert replacement.entered and replacement.exited
    assert len(response.frames) == 1


async def test_live_stream_reconnects_when_the_device_stalls_without_closing_the_socket(
    hass: HomeAssistant,
) -> None:
    """A read that never returns (device asleep, socket never closed) times out and reconnects.

    Without a read timeout, `session.read_packet()` blocking forever would starve the whole loop
    — the transport-closing check and backoff are only reached once a read call returns. The
    hanging read waits on an `asyncio.Event` that is never set, not `asyncio.sleep`, so patching
    `camera.asyncio.sleep` for the backoff delay below can't accidentally short-circuit it too.
    """

    class _HangingSession(_FakeCastSession):
        async def read_packet(self) -> CastPacket:
            await asyncio.Event().wait()
            raise AssertionError("should have timed out before this ever returns")

    hanging = _HangingSession(packets=None, aenter_error=None)
    replacement = _FakeCastSession(
        packets=[
            CastPacket(
                type=PacketType.VIDEO,
                flag=PacketFlag.KEY_FRAME,
                pts=1,
                data=CAPTURE_H264,
            )
        ],
        aenter_error=None,
    )
    request = _FakeStreamRequest(_FakeTransport(closing_after=2))
    response = _FakeMjpegResponse()

    with (
        _patched_cast_session_sequence([hanging, replacement]),
        patch("custom_components.eversolo.camera._READ_STALL_TIMEOUT_SECONDS", 0.01),
        patch("custom_components.eversolo.camera.asyncio.sleep", AsyncMock()),
    ):
        await _async_stream_live_view(client=None, request=request, response=response)

    assert hanging.entered and hanging.exited
    assert replacement.entered and replacement.exited
    assert len(response.frames) == 1


async def test_live_stream_backs_off_with_a_growing_delay_on_repeated_connect_failures(
    hass: HomeAssistant,
) -> None:
    """Consecutive failures to even open a session grow the retry delay, not spin tightly."""
    sessions = [
        _FakeCastSession(packets=None, aenter_error=CastSessionError("no route"))
        for _ in range(3)
    ]
    request = _FakeStreamRequest(_FakeTransport(closing_after=3))
    response = _FakeMjpegResponse()

    with (
        _patched_cast_session_sequence(sessions),
        patch(
            "custom_components.eversolo.camera.asyncio.sleep", AsyncMock()
        ) as sleep_mock,
    ):
        await _async_stream_live_view(client=None, request=request, response=response)

    delays = [call.args[0] for call in sleep_mock.await_args_list]
    assert delays == [1, 2, 4]


async def test_live_stream_stops_reconnecting_once_the_viewer_is_gone(
    hass: HomeAssistant,
) -> None:
    """No second attempt is made once the request's transport reports closing."""
    sessions = [
        _FakeCastSession(packets=None, aenter_error=CastSessionError("no route"))
    ]
    request = _FakeStreamRequest(_FakeTransport(closing_after=1))
    response = _FakeMjpegResponse()

    with (
        _patched_cast_session_sequence(sessions) as cast_session,
        patch("custom_components.eversolo.camera.asyncio.sleep", AsyncMock()),
    ):
        await _async_stream_live_view(client=None, request=request, response=response)

        assert cast_session.call_count == 1


async def test_live_stream_releases_the_session_on_an_abrupt_viewer_disconnect(
    hass: HomeAssistant,
) -> None:
    """A write that raises `ConnectionResetError` (viewer gone) still tears the session down.

    The exception is left to propagate rather than caught inside the stream loop — the HTTP layer
    above (core's `async_get_mjpeg_stream`) already catches and logs it — but the session must not
    leak on the way out.
    """
    session = _FakeCastSession(
        packets=[
            CastPacket(
                type=PacketType.VIDEO,
                flag=PacketFlag.KEY_FRAME,
                pts=1,
                data=CAPTURE_H264,
            )
        ],
        aenter_error=None,
    )
    request = _FakeStreamRequest(_FakeTransport())
    response = _FakeMjpegResponse(fail_after=0)

    with (
        patch("custom_components.eversolo.camera.CastSession", lambda client: session),
        pytest.raises(ConnectionResetError),
    ):
        await _async_stream_live_view(client=None, request=request, response=response)

    assert session.entered
    assert session.exited
