"""Optional MusicBrainz/Cover Art Archive lookup for Bluetooth now-playing art.

Independent of the device's own CD-lookup path (that one is keyed on a disc
table-of-contents checksum a Bluetooth stream has no equivalent of — see #06 of
the maintainer's private planning notes). Bluetooth already hands the
integration ``audioTitle``/``audioArtist``/``audioAlbum`` directly
(``EversoloPlayback.from_state``), so this looks the track up against
MusicBrainz on its own, entirely off-device (#18).

Two calls, in sequence:

1. ``GET musicbrainz.org/ws/2/release`` searching artist+title, plus album when
   known, for a candidate release MBID.
2. ``GET coverartarchive.org/release/<mbid>/front`` — a redirect straight to
   the image when the community picked a front cover for that release.

Every outcome is cached per ``(artist, title, album)``, including "no match",
so the same track is never looked up twice.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Final

import aiohttp

from .const import (
    COVER_ART_ARCHIVE_URL,
    LOGGER,
    MUSICBRAINZ_MIN_REQUEST_INTERVAL,
    MUSICBRAINZ_SEARCH_URL,
)

_REDIRECT_STATUSES: Final = (301, 302, 303, 307, 308)
_REQUEST_TIMEOUT: Final = 10

type CoverKey = tuple[str, str, str | None]


class _LookupUnavailable(Exception):
    """A step could not be completed — distinct from it completing with no cover.

    Raised internally, never a class a caller of :class:`EversoloMusicBrainzClient`
    sees. The distinction is what keeps a transient failure from being cached
    as a permanent "no cover" (#18): :meth:`EversoloMusicBrainzClient.async_lookup_cover`
    catches this and returns ``None`` without writing to the cache, so the
    next poll of the same track tries again instead of being stuck on one bad
    request forever.
    """


class EversoloMusicBrainzClient:
    """Looks up a release's front cover art by artist, title and album."""

    def __init__(self, session: aiohttp.ClientSession, user_agent: str) -> None:
        """Store the shared HTTP session and this integration's User-Agent."""
        self._session = session
        self._user_agent = user_agent
        self._cache: dict[CoverKey, str | None] = {}
        self._last_search_at: float | None = None
        # Serialises the throttle wait so concurrent lookups queue behind one
        # another rather than each timing itself off a stale ``_last_search_at``.
        self._search_lock = asyncio.Lock()

    async def async_lookup_cover(
        self, artist: str, title: str, album: str | None
    ) -> str | None:
        """Return a cover image URL for this track, or None if there is none.

        Cached per ``(artist, title, album)`` — a repeat lookup for the same
        key, including one that genuinely found nothing, never touches the
        network. A step that could not even be completed (comms error,
        malformed body) is a different outcome from "found nothing", and is
        deliberately **not** cached — see ``_LookupUnavailable`` — so the next
        poll of a still-playing track gets to try again.
        """
        key: CoverKey = (artist, title, album or None)
        if key in self._cache:
            return self._cache[key]

        try:
            cover = await self._async_lookup(artist, title, album)
        except _LookupUnavailable:
            return None
        self._cache[key] = cover
        return cover

    async def _async_lookup(
        self, artist: str, title: str, album: str | None
    ) -> str | None:
        """Search for a release, then resolve its front cover, if either exists."""
        mbid = await self._async_search_release(artist, title)
        if mbid is None:
            return None
        return await self._async_front_cover(mbid)

    async def _async_search_release(self, artist: str, title: str) -> str | None:
        """Return the best-matching release's MBID, or None on a genuine no-match.

        Raises :class:`_LookupUnavailable` if the step could not be completed
        at all — comms failure, or a body that isn't the JSON object the API
        promises — which is not the same thing and must not be cached as one.
        """
        # Album is deliberately never part of the query, even when known:
        # Bluetooth's ``audioAlbum`` (the only source this ever runs against)
        # is not reliably a real album name — a live capture returned junk
        # caption text (" O'Flynn - Video Available") for a track that has no
        # album at all, and folding that into a ``release:`` clause returned
        # a confidently wrong top-scored release rather than no match. It
        # still lives in the cache key (below), since it's still part of
        # what makes a track distinct, just not searched on.
        query = f"artist:{artist} AND recording:{title}"

        await self._async_throttle()
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT):
                response = await self._session.get(
                    MUSICBRAINZ_SEARCH_URL,
                    params={"query": query, "fmt": "json"},
                    headers={"User-Agent": self._user_agent},
                )
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as exception:
            LOGGER.debug(
                "MusicBrainz search failed for %r / %r: %s", artist, title, exception
            )
            raise _LookupUnavailable from exception

        if not isinstance(payload, dict):
            raise _LookupUnavailable(f"Unexpected search response shape: {payload!r}")

        releases = payload.get("releases") or []
        return releases[0].get("id") if releases else None

    async def _async_front_cover(self, mbid: str) -> str | None:
        """Follow Cover Art Archive's redirect to the front image, if any.

        ``None`` when the release has no community-picked front cover — a
        genuine, cacheable answer. Raises :class:`_LookupUnavailable` on a
        comms failure, same distinction as :meth:`_async_search_release`.

        Not throttled: MusicBrainz's rate-limiting policy gates the search
        step alone, and Cover Art Archive has no limit of its own.
        """
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT):
                response = await self._session.get(
                    f"{COVER_ART_ARCHIVE_URL}/{mbid}/front",
                    allow_redirects=False,
                )
        except (aiohttp.ClientError, TimeoutError) as exception:
            LOGGER.debug("Cover Art Archive lookup failed for %s: %s", mbid, exception)
            raise _LookupUnavailable from exception

        if response.status not in _REDIRECT_STATUSES:
            return None
        return response.headers.get("Location")

    async def _async_throttle(self) -> None:
        """Hold successive searches to MusicBrainz's own rate-limiting policy."""
        async with self._search_lock:
            if self._last_search_at is not None:
                wait = MUSICBRAINZ_MIN_REQUEST_INTERVAL - (
                    time.monotonic() - self._last_search_at
                )
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_search_at = time.monotonic()
