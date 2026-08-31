"""Browse the device's local music library as HA's ``BrowseMedia`` tree (#47).

Three top-level branches, two levels deep — Albums (-> tracks), Artists (->
albums -> tracks), Recently Played (-> tracks) — all live-verified against a
DMP-A8 Gen 2 on fw v1.1.80 (RESEARCH.md, "Ticket 15"). Folder browsing
(``getFolders``/``getFolderMusics``) is permanently out of scope: ``getFolders``
returns the SMB share password in cleartext in its ``url`` field, and a browse
node's ``media_content_id`` is rendered in the UI and written to traces
(RESEARCH.md, 2026-08-24). Favourites and Playlists are deferred — the
reference device has zero of each, so both branches would render permanently
empty — as are Genres/Years/Composers/Singles, redundant slices of the same
catalogue.

Content ids are standard HA types with the device's own integer as a bare id:
``media_content_type`` and ``media_content_id`` are separate fields, so the
type never needs encoding into the id (no invented ``eversolo://`` scheme).
Recently Played is the one non-standard node — a ``MediaClass.DIRECTORY``
under the custom :data:`LIBRARY_RECENTLY_PLAYED` type, since there is no
single device-side id it could be keyed on the way an album or artist is.

No pagination: ``BrowseMedia.children`` has no pagination primitive of its
own, so each branch is fetched whole. Measured, not assumed, live against the
reference device — 384 albums in 64 KB/28 ms, 523 artists in 60 KB/28 ms at
:data:`_FETCH_ALL` (RESEARCH.md, "Ticket 15") — cheap enough that this only
becomes wrong somewhere north of ~10x that library size.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaType,
)

from .api import EversoloApiClient

# The two collection nodes themselves ("all albums", "all artists") — not any
# one album/artist, so neither can borrow ``MediaType.ALBUM``/``ARTIST``.
LIBRARY_ALBUMS = "albums"
LIBRARY_ARTISTS = "artists"
# The one branch with no per-item id to browse deeper into (it is already the
# flat track list), so it is its own leaf-bearing directory type rather than
# a category that resolves to one.
LIBRARY_RECENTLY_PLAYED = "recently_played"
# The invisible root HA asks for first (``media_content_type is None``) and
# returns to when a client re-browses back to the top.
LIBRARY_ROOT = "library"

# One request per branch, no pagination — see the module docstring. Comfortably
# above any observed library size (523 artists was the largest branch measured).
_FETCH_ALL = 5000


def _thumbnail_for(client: EversoloApiClient, item_id: int, music_type: int) -> str:
    """Album (``music_type=2``) or track (``music_type=1``) cover URL."""
    return client.create_image_url_by_song_id(item_id, music_type)


def _album_node(client: EversoloApiClient, album: dict[str, Any]) -> BrowseMedia:
    """One album, browsable into its tracks or playable whole (#48).

    Thumbnail: ``getImage`` works for albums.
    """
    return BrowseMedia(
        media_class=MediaClass.ALBUM,
        media_content_id=str(album["id"]),
        media_content_type=MediaType.ALBUM,
        title=album.get("name") or "Unknown Album",
        can_play=True,
        can_expand=True,
        thumbnail=_thumbnail_for(client, album["id"], music_type=2),
    )


def _artist_node(artist: dict[str, Any]) -> BrowseMedia:
    """One artist, browsable into their albums.

    No thumbnail: ``getImage`` answers 806 ("resource does not exist") for
    every ``musicType`` tried against an artist id, and the online fallback
    ``getArtistImages`` comes back empty (RESEARCH.md, "Ticket 15") — leave
    the node bare and let HA render its own placeholder rather than
    substituting a generic icon. Playable whole as well as browsable, since
    #48 wired every artist's id up to ``play_media``.
    """
    return BrowseMedia(
        media_class=MediaClass.ARTIST,
        media_content_id=str(artist["id"]),
        media_content_type=MediaType.ARTIST,
        title=artist.get("name") or "Unknown Artist",
        can_play=True,
        can_expand=True,
    )


def _track_node(client: EversoloApiClient, track: dict[str, Any]) -> BrowseMedia:
    """One track leaf. Thumbnail: ``getImage`` with the track's own id, musicType=1."""
    return BrowseMedia(
        media_class=MediaClass.TRACK,
        media_content_id=str(track["id"]),
        media_content_type=MediaType.TRACK,
        title=track.get("title") or "Unknown Track",
        can_play=True,
        can_expand=False,
        thumbnail=_thumbnail_for(client, track["id"], music_type=1),
    )


def _directory(
    content_type: str, title: str, children: list[BrowseMedia]
) -> BrowseMedia:
    """Build a category node identified by one of the custom ``LIBRARY_*`` types."""
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=content_type,
        media_content_type=content_type,
        title=title,
        can_play=False,
        can_expand=True,
        children=children,
    )


async def async_search_library(
    client: EversoloApiClient, search_query: str
) -> list[BrowseMedia]:
    """Search the local library for tracks matching ``search_query`` (#49).

    ``searchMusicV2`` is filename-driven, not metadata-quality — matches can
    be scene-release strings or raw filenames on an untidy library. That is a
    stated expectation (RESEARCH.md, "Ticket 15"), not something to
    re-rank away here.
    """
    payload = await client.async_search_music(search_query, start=0, count=_FETCH_ALL)
    hits = payload.get("array") or []
    return [_track_node(client, hit["result"]) for hit in hits]


async def async_browse_library(
    client: EversoloApiClient,
    media_content_type: str | None,
    media_content_id: str | None,
) -> BrowseMedia:
    """Resolve one ``browse_media`` step against the device's local library."""
    if media_content_type in (None, LIBRARY_ROOT):
        return _directory(
            LIBRARY_ROOT,
            "Local Library",
            [
                _directory(LIBRARY_ALBUMS, "Albums", []),
                _directory(LIBRARY_ARTISTS, "Artists", []),
                _directory(LIBRARY_RECENTLY_PLAYED, "Recently Played", []),
            ],
        )

    if media_content_type == LIBRARY_ALBUMS:
        payload = await client.async_get_albums(start=0, count=_FETCH_ALL)
        albums = payload.get("array") or []
        return _directory(
            LIBRARY_ALBUMS, "Albums", [_album_node(client, album) for album in albums]
        )

    if media_content_type == LIBRARY_ARTISTS:
        payload = await client.async_get_artists(start=0, count=_FETCH_ALL)
        artists = payload.get("array") or []
        return _directory(
            LIBRARY_ARTISTS, "Artists", [_artist_node(artist) for artist in artists]
        )

    if media_content_type == LIBRARY_RECENTLY_PLAYED:
        payload = await client.async_get_recently_played_music_list(
            start=0, count=_FETCH_ALL
        )
        tracks = payload.get("array") or []
        return _directory(
            LIBRARY_RECENTLY_PLAYED,
            "Recently Played",
            [_track_node(client, track) for track in tracks],
        )

    if media_content_type == MediaType.ALBUM and media_content_id is not None:
        try:
            album_id = int(media_content_id)
        except ValueError:
            raise BrowseError(
                f"Media not found: {media_content_type} / {media_content_id}"
            ) from None
        payload = await client.async_get_album_musics(
            album_id, start=0, count=_FETCH_ALL
        )
        tracks = payload.get("array") or []
        title = tracks[0]["album"] if tracks else "Unknown Album"
        return BrowseMedia(
            media_class=MediaClass.ALBUM,
            media_content_id=media_content_id,
            media_content_type=MediaType.ALBUM,
            title=title,
            can_play=True,
            can_expand=True,
            children=[_track_node(client, track) for track in tracks],
        )

    if media_content_type == MediaType.ARTIST and media_content_id is not None:
        try:
            artist_id = int(media_content_id)
        except ValueError:
            raise BrowseError(
                f"Media not found: {media_content_type} / {media_content_id}"
            ) from None
        payload = await client.async_get_artist_albums(
            artist_id, start=0, count=_FETCH_ALL
        )
        albums = payload.get("array") or []
        title = albums[0]["artist"] if albums else "Unknown Artist"
        return BrowseMedia(
            media_class=MediaClass.ARTIST,
            media_content_id=media_content_id,
            media_content_type=MediaType.ARTIST,
            title=title,
            can_play=True,
            can_expand=True,
            children=[_album_node(client, album) for album in albums],
        )

    raise BrowseError(f"Media not found: {media_content_type} / {media_content_id}")
