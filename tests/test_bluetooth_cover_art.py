"""#18: the optional MusicBrainz cover-art lookup for Bluetooth playback.

Exercises the coordinator/media_player wiring end to end — the client's own
search/cache/throttle behaviour is covered in ``test_musicbrainz.py``.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.const import (
    CONF_ENABLE_MUSICBRAINZ_LOOKUP,
    COVER_ART_ARCHIVE_URL,
    LIVE_UPDATE_INTERVAL,
    MUSICBRAINZ_SEARCH_URL,
)

from .helpers import (
    GET_STATE,
    answers_with,
    calls_to,
    entity_id_for,
    entity_object,
    fixture_json,
    prime_device,
    setup_integration,
)

COVER_URL = "https://coverartarchive.org/release/mbid-1/front-500.jpg"


def _bluetooth_state() -> dict:
    return fixture_json("getstate_bluetooth.json")


def _mock_found_cover(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json={"releases": [{"id": "mbid-1"}]})
    aioclient_mock.get(
        f"{COVER_ART_ARCHIVE_URL}/mbid-1/front",
        status=307,
        headers={"Location": COVER_URL},
    )


def _mock_no_match(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(MUSICBRAINZ_SEARCH_URL, json={"releases": []})


async def _bt_player(hass: HomeAssistant, aioclient_mock, *, enabled: bool) -> str:
    prime_device(aioclient_mock, {GET_STATE: {"json": _bluetooth_state()}})
    await setup_integration(hass, options={CONF_ENABLE_MUSICBRAINZ_LOOKUP: enabled})
    await hass.async_block_till_done(wait_background_tasks=True)
    return entity_id_for(hass, "_media_player")


async def test_off_by_default_never_calls_musicbrainz(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No options at all — the same as the toggle being off (#18)."""
    prime_device(aioclient_mock, {GET_STATE: {"json": _bluetooth_state()}})

    await setup_integration(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    entity_id = entity_id_for(hass, "_media_player")

    assert calls_to(aioclient_mock, "/ws/2/release") == 0
    assert entity_object(hass, entity_id).media_image_url is None


async def test_explicitly_disabled_never_calls_musicbrainz(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The toggle set to False behaves the same as it never being set."""
    entity_id = await _bt_player(hass, aioclient_mock, enabled=False)

    assert calls_to(aioclient_mock, "/ws/2/release") == 0
    assert entity_object(hass, entity_id).media_image_url is None


async def test_enabling_lookup_resolves_the_bluetooth_cover(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A found cover becomes the entity's image, and reports as remote."""
    _mock_found_cover(aioclient_mock)

    entity_id = await _bt_player(hass, aioclient_mock, enabled=True)
    entity = entity_object(hass, entity_id)

    assert entity.media_image_url == COVER_URL
    assert entity.media_image_remotely_accessible is True


async def test_a_search_query_carries_the_bluetooth_track(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The lookup is keyed on the device's own BT title/artist/album."""
    _mock_found_cover(aioclient_mock)

    await _bt_player(hass, aioclient_mock, enabled=True)

    query = [
        dict(url.query)["query"]
        for _, url, *_ in aioclient_mock.mock_calls
        if url.path == "/ws/2/release"
    ][0]
    assert "artist:Andy Compton" in query
    assert "recording:Nifanyeje" in query


async def test_no_match_leaves_the_image_unset_and_not_remotely_accessible(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A wrong or broken image must never replace no art (#18)."""
    _mock_no_match(aioclient_mock)

    entity_id = await _bt_player(hass, aioclient_mock, enabled=True)
    entity = entity_object(hass, entity_id)

    assert entity.media_image_url is None
    assert entity.media_image_remotely_accessible is False


async def test_repeat_polls_of_the_same_track_do_not_relookup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The same (artist, title, album) tuple across cycles fires once."""
    _mock_found_cover(aioclient_mock)

    await _bt_player(hass, aioclient_mock, enabled=True)

    for _ in range(3):
        freezer.tick(timedelta(seconds=LIVE_UPDATE_INTERVAL, milliseconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert calls_to(aioclient_mock, "/ws/2/release") == 1


async def test_track_change_fires_a_new_lookup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A new (artist, title, album) tuple on the next poll re-fires (#18)."""
    _mock_found_cover(aioclient_mock)
    current = {"state": _bluetooth_state()}
    prime_device(aioclient_mock, {GET_STATE: answers_with(lambda: current["state"])})

    await setup_integration(hass, options={CONF_ENABLE_MUSICBRAINZ_LOOKUP: True})
    await hass.async_block_till_done(wait_background_tasks=True)

    next_state = _bluetooth_state()
    next_state["everSoloPlayInfo"]["everSoloBtInInfo"]["audioTitle"] = "Another Song"
    current["state"] = next_state

    freezer.tick(timedelta(seconds=LIVE_UPDATE_INTERVAL, milliseconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert calls_to(aioclient_mock, "/ws/2/release") == 2
