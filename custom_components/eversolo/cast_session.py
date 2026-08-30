"""Cast-mode session client for the front-panel screen mirror (#38).

Wraps the port-9529 ``setcastmode`` handshake and the raw TCP socket it hands back — the same
passive, read-only mechanism the vendor's phone/web apps use for live screen mirroring. Opening a
session and reading frames off it never wakes the panel or shows anything on it, unlike the
``getScreenShot`` transport this replaces (RESEARCH.md's 2026-08-30 entry).

This module is the shared client #39 (full-rate live view) and #40 (tap-to-control) both build on.
It stays strictly read-only — the same socket also accepts 16-byte touch packets and 4-byte key
packets, but nothing here ever writes to it. A write path is #40's job to add deliberately, not
something to grow here by accident.

Wire protocol, recovered from ``VideoPacket.fromArray`` / ``VideoPacket$Flag`` /
``MediaPacket$Type`` (``classes3.dex``, scope: all 10 DEX complete) and verified live against
``192.168.0.63`` on firmware ``v1.1.80``:

    [4-byte BE length N][N bytes payload]
      payload[0]     type   VIDEO=1, AUDIO=0
      payload[1]     flag   FRAME=0, KEY_FRAME=1, CONFIG=2, END=4
      payload[2:10]  presentationTimeStamp, 8-byte BE
      payload[10:]   H.264, Annex-B on the wire
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Self

from .api import EversoloApiClient, EversoloApiClientError
from .const import LOGGER

# Length prefix (4 bytes) + the fixed type/flag/pts header (1+1+8) that precedes the H.264 payload.
_LENGTH_PREFIX_BYTES = 4
_PAYLOAD_HEADER_BYTES = 10


class PacketType(IntEnum):
    """``MediaPacket$Type`` — which track a framed packet belongs to."""

    AUDIO = 0
    VIDEO = 1


class PacketFlag(IntEnum):
    """``VideoPacket$Flag`` — what kind of video packet this is.

    ``END`` is genuinely ``4``, not ``3`` — a gap in the numbering the device's own wire format
    carries, not a transcription slip here.
    """

    FRAME = 0
    KEY_FRAME = 1
    CONFIG = 2
    END = 4


@dataclass(frozen=True)
class CastPacket:
    """One framed packet read off an open cast session's socket."""

    type: PacketType
    flag: PacketFlag
    pts: int
    data: bytes


@dataclass(frozen=True)
class CastHandshake:
    """A cast session's own connection details, as the device just allocated them.

    ``video_width``/``video_height`` are the stream's real, downscaled resolution and are what
    decoding must use; ``device_width``/``device_height`` describe the physical panel (1600x600 on
    the DMP-A8) and are carried here only as metadata — never used to size a decode. ``is_rotated``
    is likewise unused by the decode path: every capture taken so far has reported ``False`` on the
    DMP-A8's fixed panel, so there is no real device state yet to justify rotating a frame — parsed
    and kept here so a caller (or a future capability check) can notice if that ever changes.
    """

    port: int
    ip: str
    video_width: int
    video_height: int
    device_width: int
    device_height: int
    is_rotated: bool
    is_show_menu: bool

    @classmethod
    def from_payload(cls, payload: dict, fallback_host: str) -> Self:
        """Parse a ``setcastmode?mode=1`` reply.

        ``ip`` falls back to the host the handshake itself was sent to — every capture so far has
        echoed the same host back, but nothing guarantees a firmware always will. Raises
        :class:`CastSessionError`, not ``KeyError``, if a required field is missing — firmware
        without this endpoint answers the same way the old ``getScreenShot`` transport did, HTTP
        200 with a JSON error body (``{"status":804,"msg":"Url error"}``) rather than a real
        handshake, and callers only catch the former.
        """
        try:
            return cls(
                port=payload["port"],
                ip=payload.get("ip") or fallback_host,
                video_width=payload["videoWidth"],
                video_height=payload["videoHeight"],
                device_width=payload["deviceWidth"],
                device_height=payload["deviceHeight"],
                is_rotated=bool(payload.get("isRotated", False)),
                is_show_menu=bool(payload.get("isShowMenu", False)),
            )
        except KeyError as exception:
            raise CastSessionError(
                f"cast handshake reply is missing {exception}: {payload}"
            ) from exception


class CastSessionError(EversoloApiClientError):
    """A cast session could not be opened, or broke while being read."""


async def read_packet(reader: asyncio.StreamReader) -> CastPacket:
    """Read one framed packet off an open cast socket.

    A pure function of the reader, independent of :class:`CastSession` — so a test can exercise
    the framing against any :class:`asyncio.StreamReader`, real socket or in-memory, without
    standing up a session at all.
    """
    try:
        (length,) = struct.unpack(">i", await reader.readexactly(_LENGTH_PREFIX_BYTES))
        # Equal to the header size is a valid, real shape — an ``END`` marker with no H.264
        # payload attached, say — only a length that can't even hold the fixed header is broken.
        if length < _PAYLOAD_HEADER_BYTES:
            raise CastSessionError(
                f"cast socket sent an impossible frame length {length}"
            )
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exception:
        raise CastSessionError("cast socket closed mid-frame") from exception

    return CastPacket(
        type=PacketType(payload[0]),
        flag=PacketFlag(payload[1]),
        pts=int.from_bytes(payload[2:10], "big", signed=True),
        data=payload[_PAYLOAD_HEADER_BYTES:],
    )


class CastSession:
    """One open cast-mode session: the handshake, plus the socket it allocated.

    An async context manager so the socket and the device-side session are always torn down
    together — on the happy path and on failure alike (#38's "does not leak sockets" acceptance
    criterion). Teardown is best-effort: a stop call that fails is logged and swallowed, not
    raised, since the device frees the port on socket close regardless (verified live,
    RESEARCH.md) and a caller already handling one failure should not be handed a second.
    """

    def __init__(self, client: EversoloApiClient) -> None:
        """Bind this session to the API client it will open the handshake through."""
        self._client = client
        self.handshake: CastHandshake | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def __aenter__(self) -> Self:
        """Open the handshake, then connect the socket it allocated."""
        payload = await self._client.async_start_cast_session()
        self.handshake = CastHandshake.from_payload(payload, self._client.host)
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self.handshake.ip, self.handshake.port
            )
        except OSError as exception:
            await self._async_stop_device_session()
            raise CastSessionError(
                f"could not connect to cast socket {self.handshake.ip}:{self.handshake.port}"
            ) from exception
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Close the socket, then tear down the device-side session."""
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(OSError):
                await self._writer.wait_closed()
        await self._async_stop_device_session()

    async def _async_stop_device_session(self) -> None:
        """Call the teardown endpoint, if the handshake ever landed."""
        if self.handshake is None:
            return
        try:
            await self._client.async_stop_cast_session(self.handshake.port)
        except EversoloApiClientError:
            LOGGER.debug(
                "cast session teardown for port %s did not confirm", self.handshake.port
            )

    async def read_packet(self) -> CastPacket:
        """Read the next framed packet from the open socket."""
        if self._reader is None:
            raise CastSessionError("read_packet called outside an open session")
        return await read_packet(self._reader)
