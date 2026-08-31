"""browse_media tests (#47): Albums, Artists, Recently Played — against real captures."""

from __future__ import annotations

import pytest
from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.media_library import (
    LIBRARY_ALBUMS,
    LIBRARY_ARTISTS,
    LIBRARY_RECENTLY_PLAYED,
    async_search_library,
)

from .helpers import (
    GET_ALBUM_MUSICS,
    GET_ALBUMS,
    GET_ARTIST_ALBUMS,
    GET_ARTISTS,
    GET_FOLDERS,
    GET_RECENTLY_PLAYED,
    SEARCH_MUSIC,
    calls_to,
    entity_id_for,
    entity_object,
    fixture_json,
    prime_device,
    query_of,
    setup_integration,
)


async def _player(hass: HomeAssistant, aioclient_mock, overrides=None):
    """Set the integration up and return the live media_player entity.

    Returns the entity itself, not just its id: ``browse_media`` is reached
    over HA's websocket API rather than a service call, so tests call it
    directly.
    """
    prime_device(aioclient_mock, overrides)
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_media_player")
    return entity_object(hass, entity_id)


def _library_overrides(**by_path) -> dict:
    """Register the browse endpoints this test needs, each with its fixture."""
    return {path: {"json": fixture} for path, fixture in by_path.items()}


async def test_root_lists_the_three_branches(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Browsing with no content type/id returns the three top-level branches."""
    player = await _player(hass, aioclient_mock)

    root = await player.async_browse_media()

    titles = {child.title: child.media_content_type for child in root.children}
    assert titles == {
        "Albums": LIBRARY_ALBUMS,
        "Artists": LIBRARY_ARTISTS,
        "Recently Played": LIBRARY_RECENTLY_PLAYED,
    }
    assert all(child.can_expand for child in root.children)
    assert all(not child.can_play for child in root.children)


async def test_albums_branch_lists_albums_with_thumbnails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Each album carries its own id, name and a ``musicType=2`` cover URL."""
    player = await _player(
        hass,
        aioclient_mock,
        _library_overrides(**{GET_ALBUMS: fixture_json("getalbums.json")}),
    )

    node = await player.async_browse_media(LIBRARY_ALBUMS)

    assert [child.title for child in node.children] == [
        "A Foot in the Door The Best of Pink Floyd",
        "A Moon Shaped Pool",
        "always centered at night",
    ]
    moon = node.children[1]
    assert moon.media_content_type == MediaType.ALBUM
    assert moon.media_content_id == "469"
    assert moon.media_class == MediaClass.ALBUM
    assert moon.can_expand is True
    assert moon.can_play is True
    assert "id=469" in moon.thumbnail
    assert "musicType=2" in moon.thumbnail


async def test_artists_branch_lists_artists_without_thumbnails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Artists carry no thumbnail — the device has none to offer (#47)."""
    player = await _player(
        hass,
        aioclient_mock,
        _library_overrides(**{GET_ARTISTS: fixture_json("getartists.json")}),
    )

    node = await player.async_browse_media(LIBRARY_ARTISTS)

    tribe = node.children[0]
    assert tribe.title == "A Tribe Called Quest"
    assert tribe.media_content_type == MediaType.ARTIST
    assert tribe.media_content_id == "10000820"
    assert tribe.can_play is True
    assert tribe.media_class == MediaClass.ARTIST
    assert tribe.can_expand is True
    assert tribe.thumbnail is None


async def test_browsing_into_an_album_lists_its_tracks(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An album's children are its tracks, with a ``musicType=1`` cover each."""
    player = await _player(
        hass,
        aioclient_mock,
        _library_overrides(**{GET_ALBUM_MUSICS: fixture_json("getalbummusics.json")}),
    )

    node = await player.async_browse_media(MediaType.ALBUM, "469")

    assert query_of(aioclient_mock, GET_ALBUM_MUSICS)["id"] == "469"
    assert node.title == "A Moon Shaped Pool"
    assert node.media_class == MediaClass.ALBUM
    assert node.can_play is True
    assert len(node.children) == 3
    track = node.children[0]
    assert track.title == "Burn the Witch"
    assert track.media_content_type == MediaType.TRACK
    assert track.media_content_id == "6111"
    assert track.media_class == MediaClass.TRACK
    assert track.can_play is True
    assert track.can_expand is False
    assert "id=6111" in track.thumbnail
    assert "musicType=1" in track.thumbnail


async def test_browsing_into_an_artist_lists_their_albums(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An artist's children are their albums, drillable into tracks again."""
    player = await _player(
        hass,
        aioclient_mock,
        _library_overrides(**{GET_ARTIST_ALBUMS: fixture_json("getartistalbums.json")}),
    )

    node = await player.async_browse_media(MediaType.ARTIST, "10000820")

    assert query_of(aioclient_mock, GET_ARTIST_ALBUMS)["id"] == "10000820"
    assert node.title == "A Tribe Called Quest"
    assert node.media_class == MediaClass.ARTIST
    assert node.can_play is True
    assert len(node.children) == 1
    album = node.children[0]
    assert album.title == "The Anthology"
    assert album.media_content_type == MediaType.ALBUM
    assert album.media_content_id == "465"
    assert album.can_expand is True


async def test_recently_played_lists_tracks_directly(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Recently Played has no per-item id of its own — it is already the track list."""
    player = await _player(
        hass,
        aioclient_mock,
        _library_overrides(
            **{GET_RECENTLY_PLAYED: fixture_json("getrecentlyplayedmusiclist.json")}
        ),
    )

    node = await player.async_browse_media(LIBRARY_RECENTLY_PLAYED)

    assert node.media_class == MediaClass.DIRECTORY
    assert len(node.children) == 3
    assert node.children[0].media_content_type == MediaType.TRACK
    assert node.children[0].can_play is True


@pytest.mark.parametrize(
    ("content_type", "path", "empty_fixture"),
    [
        (LIBRARY_ALBUMS, GET_ALBUMS, "getalbums_empty.json"),
        (LIBRARY_ARTISTS, GET_ARTISTS, "getartists_empty.json"),
        (
            LIBRARY_RECENTLY_PLAYED,
            GET_RECENTLY_PLAYED,
            "getrecentlyplayedmusiclist_empty.json",
        ),
    ],
)
async def test_empty_library_branches_have_no_children(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    content_type: str,
    path: str,
    empty_fixture: str,
) -> None:
    """A library with nothing in a branch renders that branch with no children."""
    player = await _player(
        hass, aioclient_mock, _library_overrides(**{path: fixture_json(empty_fixture)})
    )

    node = await player.async_browse_media(content_type)

    assert node.children == []


async def test_an_empty_album_has_no_tracks(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A nonexistent/empty album answers ``total:0, array:[]``, not an error."""
    player = await _player(
        hass,
        aioclient_mock,
        _library_overrides(
            **{GET_ALBUM_MUSICS: fixture_json("getalbummusics_empty.json")}
        ),
    )

    node = await player.async_browse_media(MediaType.ALBUM, "999999999")

    assert node.children == []
    assert node.title == "Unknown Album"


async def test_an_unknown_media_type_is_a_browse_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Anything outside the three branches (or their album/artist leaves) is refused."""
    player = await _player(hass, aioclient_mock)

    with pytest.raises(BrowseError):
        await player.async_browse_media("nonsense", "1")


@pytest.mark.parametrize("media_content_type", [MediaType.ALBUM, MediaType.ARTIST])
async def test_a_non_numeric_id_is_a_browse_error_not_a_crash(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, media_content_type: str
) -> None:
    """A malformed id (stale cache, hand-crafted call) is refused cleanly."""
    player = await _player(hass, aioclient_mock)

    with pytest.raises(BrowseError):
        await player.async_browse_media(media_content_type, "not-a-number")


async def test_search_library_unwraps_each_hit_into_a_track_node(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``searchMusicV2``'s ``{"keyName", "result"}`` wrapper is stripped to the track (#49)."""
    player = await _player(
        hass,
        aioclient_mock,
        _library_overrides(**{SEARCH_MUSIC: fixture_json("searchmusicv2.json")}),
    )

    results = await async_search_library(player.coordinator.client, "radiohead")

    assert query_of(aioclient_mock, SEARCH_MUSIC)["key"] == "radiohead"
    assert [track.title for track in results] == [
        "203-radiohead-lift",
        "Burn the Witch",
    ]
    burn = results[1]
    assert burn.media_content_type == MediaType.TRACK
    assert burn.media_content_id == "6111"
    assert burn.can_play is True
    assert "id=6111" in burn.thumbnail


async def test_search_library_with_no_hits_returns_no_nodes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A zero-results query answers an empty list, not an error (#49)."""
    player = await _player(
        hass,
        aioclient_mock,
        _library_overrides(**{SEARCH_MUSIC: fixture_json("searchmusicv2_empty.json")}),
    )

    results = await async_search_library(player.coordinator.client, "nosuchtrack")

    assert results == []


async def test_getfolders_is_never_called(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Folder browsing is permanently out of scope — it leaks the SMB password (#47)."""
    player = await _player(
        hass,
        aioclient_mock,
        _library_overrides(
            **{
                GET_ALBUMS: fixture_json("getalbums.json"),
                GET_ARTISTS: fixture_json("getartists.json"),
                GET_ALBUM_MUSICS: fixture_json("getalbummusics.json"),
                GET_ARTIST_ALBUMS: fixture_json("getartistalbums.json"),
                GET_RECENTLY_PLAYED: fixture_json("getrecentlyplayedmusiclist.json"),
            }
        ),
    )

    await player.async_browse_media()
    await player.async_browse_media(LIBRARY_ALBUMS)
    await player.async_browse_media(MediaType.ALBUM, "469")
    await player.async_browse_media(LIBRARY_ARTISTS)
    await player.async_browse_media(MediaType.ARTIST, "10000820")
    await player.async_browse_media(LIBRARY_RECENTLY_PLAYED)

    assert calls_to(aioclient_mock, GET_FOLDERS) == 0
