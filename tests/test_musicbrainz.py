"""MusicBrainz/Cover Art Archive client tests through the mocked HTTP seam."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.const import (
    COVER_ART_ARCHIVE_URL,
    MUSICBRAINZ_SEARCH_URL,
)
from custom_components.eversolo.musicbrainz import EversoloMusicBrainzClient

USER_AGENT = "ha-eversolo/9.9.9 ( https://github.com/tmeynell/ha-eversolo )"


def _client(hass: HomeAssistant) -> EversoloMusicBrainzClient:
    return EversoloMusicBrainzClient(async_get_clientsession(hass), USER_AGENT)


def _search_ok(mbid: str) -> dict:
    return {"releases": [{"id": mbid}]}


async def test_lookup_resolves_the_cover_art_archive_redirect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A matched release whose cover art archive front redirects is the cover."""
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json=_search_ok("mbid-1"))
    aioclient_mock.get(
        f"{COVER_ART_ARCHIVE_URL}/mbid-1/front",
        status=307,
        headers={"Location": "https://coverartarchive.org/release/mbid-1/123.jpg"},
    )

    cover = await _client(hass).async_lookup_cover("Andy Compton", "Nifanyeje", None)

    assert cover == "https://coverartarchive.org/release/mbid-1/123.jpg"


async def test_search_query_never_includes_the_release_clause(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Album is deliberately never searched on, even when known.

    Bluetooth's ``audioAlbum`` is the only source this ever runs against, and
    it is not reliably a real album name — a live capture returned junk
    caption text for a track with no album at all, which corrupted the
    search into a confidently wrong top-scored result.
    """
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json=_search_ok("mbid-1"))
    aioclient_mock.get(f"{COVER_ART_ARCHIVE_URL}/mbid-1/front", status=404)

    await _client(hass).async_lookup_cover("Andy Compton", "Nifanyeje", "The Rurals")

    query = aioclient_mock.mock_calls[0][1].query["query"]
    assert "artist:Andy Compton" in query
    assert "release:" not in query
    assert "recording:Nifanyeje" in query


async def test_search_query_drops_the_release_clause_without_an_album(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Same query shape whether or not ``audioAlbum`` is empty."""
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json=_search_ok("mbid-1"))
    aioclient_mock.get(f"{COVER_ART_ARCHIVE_URL}/mbid-1/front", status=404)

    await _client(hass).async_lookup_cover("Andy Compton", "Nifanyeje", None)

    query = aioclient_mock.mock_calls[0][1].query["query"]
    assert "release:" not in query


async def test_search_sends_the_required_user_agent(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """MusicBrainz's policy requires a proper contact-carrying User-Agent."""
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json=_search_ok("mbid-1"))
    aioclient_mock.get(f"{COVER_ART_ARCHIVE_URL}/mbid-1/front", status=404)

    await _client(hass).async_lookup_cover("Andy Compton", "Nifanyeje", None)

    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["User-Agent"] == USER_AGENT


async def test_no_release_match_returns_none(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An empty result set is "no cover", not an error."""
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json={"releases": []})

    cover = await _client(hass).async_lookup_cover("Nobody", "Nothing", None)

    assert cover is None


async def test_no_front_cover_picked_returns_none(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A matched release with no community front cover is "no cover"."""
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json=_search_ok("mbid-1"))
    aioclient_mock.get(f"{COVER_ART_ARCHIVE_URL}/mbid-1/front", status=404)

    cover = await _client(hass).async_lookup_cover("Andy Compton", "Nifanyeje", None)

    assert cover is None


async def test_a_search_error_returns_none_rather_than_raising(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A wrong or broken image must never replace no art — errors degrade quietly."""
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, exc=TimeoutError())

    cover = await _client(hass).async_lookup_cover("Andy Compton", "Nifanyeje", None)

    assert cover is None


async def test_a_cover_art_archive_error_returns_none_rather_than_raising(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Same degrade-quietly guarantee on the second call of the pair."""
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json=_search_ok("mbid-1"))
    aioclient_mock.get(f"{COVER_ART_ARCHIVE_URL}/mbid-1/front", exc=TimeoutError())

    cover = await _client(hass).async_lookup_cover("Andy Compton", "Nifanyeje", None)

    assert cover is None


async def test_a_transient_search_error_is_not_cached(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A comms failure must not permanently stick a track at "no cover".

    Distinct from ``test_results_including_no_match_are_cached_per_track``:
    that one is a *genuine* answer (MusicBrainz found nothing) and stays
    cached; this one is MusicBrainz simply not having answered at all, and
    the next poll of the same still-playing track has to be free to try again.
    """
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, exc=TimeoutError())
    client = _client(hass)

    first = await client.async_lookup_cover("Andy Compton", "Nifanyeje", None)
    second = await client.async_lookup_cover("Andy Compton", "Nifanyeje", None)

    assert first is None
    assert second is None
    # Two lookups, two network calls: a failed search never got cached, so
    # the second call retried instead of being served the first one's None.
    assert len(aioclient_mock.mock_calls) == 2


async def test_results_including_no_match_are_cached_per_track(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A second lookup for the same key never touches the network again."""
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json={"releases": []})
    client = _client(hass)

    first = await client.async_lookup_cover("Andy Compton", "Nifanyeje", None)
    second = await client.async_lookup_cover("Andy Compton", "Nifanyeje", None)

    assert first is None
    assert second is None
    assert len(aioclient_mock.mock_calls) == 1


async def test_a_different_track_is_not_served_from_another_tracks_cache(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The cache key is the full (artist, title, album) tuple."""
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json=_search_ok("mbid-1"))
    aioclient_mock.get(f"{COVER_ART_ARCHIVE_URL}/mbid-1/front", status=404)
    client = _client(hass)

    await client.async_lookup_cover("Andy Compton", "Nifanyeje", None)
    await client.async_lookup_cover("Andy Compton", "A Different Title", None)

    assert len(aioclient_mock.mock_calls) == 4


async def test_search_requests_are_throttled_to_the_stated_policy(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Back-to-back searches wait out MusicBrainz's ~1 req/sec average policy."""
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json={"releases": []})
    client = _client(hass)

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    await client.async_lookup_cover("Artist One", "Title One", None)
    await client.async_lookup_cover("Artist Two", "Title Two", None)

    assert sleeps, "the second back-to-back search never throttled"
    assert sleeps[0] > 0
