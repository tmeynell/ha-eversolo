"""Cast-mode session tests: wire framing, handshake parsing, session lifecycle (#38).

None of this touches a live device. Framing is exercised against synthetic packets built by hand
to the documented wire format — the real captured ``capture.h264`` fixture has no framing left to
parse (see ``tests/fixtures/README.md``), so it drives the decoder tests in ``test_camera.py``
instead. The session lifecycle mocks the HTTP handshake through the same seam every other test in
this suite uses, and stubs ``asyncio.open_connection`` with an in-memory reader/writer pair.
"""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.api import EversoloApiClient
from custom_components.eversolo.cast_session import (
    CastHandshake,
    CastPacket,
    CastSession,
    CastSessionError,
    PacketFlag,
    PacketType,
    read_packet,
)

from .helpers import BASE_URL, HOST, PORT, SETCASTMODE

HANDSHAKE_PAYLOAD = {
    "status": 200,
    "port": 7007,
    "deviceWidth": 1600,
    "deviceHeight": 600,
    "videoWidth": 960,
    "videoHeight": 360,
    "isRotated": False,
    "ip": "192.168.0.63",
    "isShowMenu": True,
}


def _client(hass: HomeAssistant) -> EversoloApiClient:
    return EversoloApiClient(HOST, PORT, async_get_clientsession(hass))


def _framed_packet(ptype: PacketType, flag: PacketFlag, pts: int, data: bytes) -> bytes:
    """Build one wire-framed packet exactly as the device would send it."""
    payload = bytes([ptype, flag]) + pts.to_bytes(8, "big", signed=True) + data
    return struct.pack(">i", len(payload)) + payload


def _reader_with(*chunks: bytes) -> asyncio.StreamReader:
    """Build an in-memory StreamReader pre-loaded with bytes and already at EOF."""
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    return reader


class _FakeWriter:
    """Just enough of :class:`asyncio.StreamWriter` for `CastSession` teardown to use."""

    def __init__(self) -> None:
        """Start unclosed, as a freshly connected writer would be."""
        self.closed = False

    def close(self) -> None:
        """Record that the session closed this writer."""
        self.closed = True

    async def wait_closed(self) -> None:
        """No transport to actually wait on."""


# ------------------------------------------------------------------
# Framing (`read_packet`) — synthetic packets, no live device involved.
# ------------------------------------------------------------------


async def test_read_packet_parses_a_key_frame() -> None:
    """The documented header layout: type, flag, 8-byte BE pts, then payload."""
    reader = _reader_with(
        _framed_packet(PacketType.VIDEO, PacketFlag.KEY_FRAME, 123456, b"h264-bytes")
    )

    packet = await read_packet(reader)

    assert packet == CastPacket(
        type=PacketType.VIDEO, flag=PacketFlag.KEY_FRAME, pts=123456, data=b"h264-bytes"
    )


async def test_read_packet_parses_the_end_flag() -> None:
    """``END`` is 4, not 3 — a gap the wire format itself carries, not a typo here."""
    reader = _reader_with(_framed_packet(PacketType.VIDEO, PacketFlag.END, 0, b""))

    packet = await read_packet(reader)

    assert packet.flag == PacketFlag.END
    assert PacketFlag.END == 4


async def test_read_packet_reads_several_packets_in_sequence() -> None:
    """Two packets back to back on the same stream are read independently."""
    reader = _reader_with(
        _framed_packet(PacketType.VIDEO, PacketFlag.CONFIG, 1, b"sps-pps"),
        _framed_packet(PacketType.VIDEO, PacketFlag.FRAME, 2, b"frame-bytes"),
    )

    first = await read_packet(reader)
    second = await read_packet(reader)

    assert (first.flag, first.data) == (PacketFlag.CONFIG, b"sps-pps")
    assert (second.flag, second.data) == (PacketFlag.FRAME, b"frame-bytes")


async def test_read_packet_distinguishes_audio_from_video() -> None:
    """``type`` decodes to the right member, not just any nonzero byte."""
    reader = _reader_with(_framed_packet(PacketType.AUDIO, PacketFlag.FRAME, 0, b"pcm"))

    packet = await read_packet(reader)

    assert packet.type == PacketType.AUDIO


async def test_read_packet_raises_on_a_stream_that_closes_mid_header() -> None:
    """Fewer than 4 bytes before EOF is a broken frame, not a clean end of stream."""
    reader = _reader_with(b"\x00\x00")

    with pytest.raises(CastSessionError, match="mid-frame"):
        await read_packet(reader)


async def test_read_packet_raises_on_a_stream_that_closes_mid_payload() -> None:
    """The length prefix promised more bytes than the socket ever delivered."""
    reader = _reader_with(struct.pack(">i", 20) + b"too-short")

    with pytest.raises(CastSessionError, match="mid-frame"):
        await read_packet(reader)


async def test_read_packet_rejects_an_impossible_length() -> None:
    """A length at or below the fixed header size can't hold a valid payload."""
    reader = _reader_with(struct.pack(">i", 5) + b"\x00" * 5)

    with pytest.raises(CastSessionError, match="impossible frame length"):
        await read_packet(reader)


# ------------------------------------------------------------------
# CastHandshake.from_payload
# ------------------------------------------------------------------


def test_handshake_parses_every_field() -> None:
    """Every field the handshake carries, including the three the APK-derived docs missed."""
    handshake = CastHandshake.from_payload(HANDSHAKE_PAYLOAD, fallback_host="ignored")

    assert handshake.port == 7007
    assert handshake.ip == "192.168.0.63"
    assert handshake.video_width == 960
    assert handshake.video_height == 360
    assert handshake.device_width == 1600
    assert handshake.device_height == 600
    assert handshake.is_rotated is False
    assert handshake.is_show_menu is True


def test_handshake_falls_back_to_the_configured_host_when_ip_is_missing() -> None:
    """Every capture so far echoed ``ip`` back, but nothing guarantees every firmware will."""
    payload = {key: value for key, value in HANDSHAKE_PAYLOAD.items() if key != "ip"}

    handshake = CastHandshake.from_payload(payload, fallback_host="192.168.0.60")

    assert handshake.ip == "192.168.0.60"


def test_handshake_raises_cast_session_error_not_key_error_on_a_missing_field() -> None:
    """A firmware-lacks-this-endpoint error body must not surface as an unhandled KeyError.

    Firmware without ``setcastmode`` can plausibly answer the same way the old ``getScreenShot``
    transport did — HTTP 200 with a JSON error body rather than a real handshake — and every
    caller of this method only catches :class:`CastSessionError`, not a bare ``KeyError``.
    """
    error_body = {"status": 804, "msg": "Url error"}

    with pytest.raises(CastSessionError, match="port"):
        CastHandshake.from_payload(error_body, fallback_host="192.168.0.60")


# ------------------------------------------------------------------
# CastSession — handshake + socket lifecycle.
# ------------------------------------------------------------------


async def test_session_connects_to_the_handshakes_own_ip_and_port(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Entering the session opens the TCP socket the handshake just allocated."""
    aioclient_mock.get(f"{BASE_URL}{SETCASTMODE}", json=HANDSHAKE_PAYLOAD)
    fake_writer = _FakeWriter()

    with patch(
        "custom_components.eversolo.cast_session.asyncio.open_connection",
        AsyncMock(return_value=(_reader_with(b""), fake_writer)),
    ) as open_connection:
        async with CastSession(_client(hass)) as session:
            assert session.handshake is not None
            assert session.handshake.port == 7007

    open_connection.assert_awaited_once_with("192.168.0.63", 7007)


async def test_session_tears_down_the_device_side_session_on_the_normal_path(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Exiting the session closes the socket and calls ``mode=0`` with the allocated port."""
    aioclient_mock.get(f"{BASE_URL}{SETCASTMODE}", json=HANDSHAKE_PAYLOAD)
    fake_writer = _FakeWriter()

    with patch(
        "custom_components.eversolo.cast_session.asyncio.open_connection",
        AsyncMock(return_value=(_reader_with(b""), fake_writer)),
    ):
        async with CastSession(_client(hass)):
            pass

    assert fake_writer.closed
    teardown_calls = [
        dict(url.query)
        for _, url, *_ in aioclient_mock.mock_calls
        if url.path == SETCASTMODE and dict(url.query).get("mode") == "0"
    ]
    assert teardown_calls == [{"mode": "0", "port": "7007"}]


async def test_session_tears_down_even_when_the_body_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A caller's own exception inside ``async with`` still closes the socket and the session.

    This is #38's "does not leak sockets" acceptance criterion: a failure partway through reading
    packets must not leave either side of the session open.
    """
    aioclient_mock.get(f"{BASE_URL}{SETCASTMODE}", json=HANDSHAKE_PAYLOAD)
    fake_writer = _FakeWriter()

    with (
        patch(
            "custom_components.eversolo.cast_session.asyncio.open_connection",
            AsyncMock(return_value=(_reader_with(b""), fake_writer)),
        ),
        pytest.raises(ValueError, match="boom"),
    ):
        async with CastSession(_client(hass)):
            raise ValueError("boom")

    assert fake_writer.closed
    assert any(
        url.path == SETCASTMODE and dict(url.query).get("mode") == "0"
        for _, url, *_ in aioclient_mock.mock_calls
    )


async def test_session_raises_and_still_tears_down_when_the_socket_wont_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A handshake that answers but a socket that refuses is a session error, not a hang."""
    aioclient_mock.get(f"{BASE_URL}{SETCASTMODE}", json=HANDSHAKE_PAYLOAD)

    with (
        patch(
            "custom_components.eversolo.cast_session.asyncio.open_connection",
            AsyncMock(side_effect=OSError("connection refused")),
        ),
        pytest.raises(CastSessionError, match="could not connect"),
    ):
        async with CastSession(_client(hass)):
            pass

    assert any(
        url.path == SETCASTMODE and dict(url.query).get("mode") == "0"
        for _, url, *_ in aioclient_mock.mock_calls
    )


async def test_session_teardown_failure_is_swallowed_not_raised(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A device gone by the time teardown runs must not raise out of a clean ``async with`` exit."""
    aioclient_mock.get(f"{BASE_URL}{SETCASTMODE}", json=HANDSHAKE_PAYLOAD)
    fake_writer = _FakeWriter()

    with patch(
        "custom_components.eversolo.cast_session.asyncio.open_connection",
        AsyncMock(return_value=(_reader_with(b""), fake_writer)),
    ):
        async with CastSession(_client(hass)):
            aioclient_mock.clear_requests()
            aioclient_mock.get(
                f"{BASE_URL}{SETCASTMODE}", exc=aiohttp.ClientError("device gone")
            )
        # Reaching here without an exception propagating is the assertion.

    assert fake_writer.closed
