"""Number tests: the display's two brightness sliders.

The device thinks in a 0..255 index and Home Assistant shows a percentage, so
the hazard here is the round trip. The percentage is deliberately the vendor's
own arithmetic — ``currentValue / maxValue`` — so that the reading agrees with
the one on the unit's own settings screen.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import aiohttp
from homeassistant.components.number import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
    NumberMode,
)
from homeassistant.const import ATTR_ENTITY_ID, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.const import (
    SETTINGS_REFRESH_CYCLES,
    SETTING_TAG_SCREEN_BRIGHTNESS,
)

from .helpers import (
    GET_KNOB_BRIGHTNESS,
    GET_SCREEN_BRIGHTNESS,
    GET_SYSTEM_SETTINGS,
    SET_KNOB_BRIGHTNESS,
    SET_SCREEN_BRIGHTNESS,
    advance_cycles,
    answers_with,
    entity_id_for,
    entity_object,
    fixture_json,
    prime_device,
    query_of,
    records_writes,
    settings_without,
    setup_integration,
)

NUMBER_DOMAIN = "number"

# A unit that does have a knob: only the A6 does, so no capture shows one.
KNOB_OPTION = {
    "status": 200,
    "items": [{"tag": "SettingsItemTagKnobLight", "title": "Knob brightness"}],
}
KNOB_BRIGHTNESS = {"status": 200, "currentValue": 128, "minValue": 0, "maxValue": 255}
GET_KNOB_OPTION = "/SystemSettings/displaySettings/getKnobSettingOption"


async def _numbers(hass: HomeAssistant, aioclient_mock, overrides=None) -> None:
    """Set the integration up against a device primed with the captures."""
    prime_device(aioclient_mock, overrides)
    await setup_integration(hass)


def _numbers_matching(hass: HomeAssistant, key: str) -> list[str]:
    """Every number entity whose id carries a key — empty when it was gated off."""
    return [
        entity_id
        for entity_id in hass.states.async_entity_ids(NUMBER_DOMAIN)
        if key in entity_id
    ]


async def _set(hass: HomeAssistant, entity_id: str, value: float) -> None:
    """Move the slider the way the frontend would."""
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value},
        blocking=True,
    )


def _brightness_at(value: int) -> dict[str, Any]:
    """Return the captured brightness payload with the slider somewhere else."""
    payload = fixture_json("getscreenbrightness.json")
    payload["currentValue"] = value
    return payload


async def test_screen_brightness_reads_as_a_percentage(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """30 of 255 is what the device itself renders as 11%."""
    await _numbers(hass, aioclient_mock)

    state = hass.states.get(entity_id_for(hass, "_screen_brightness"))

    assert float(state.state) == 12
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["min"] == 0
    assert state.attributes["max"] == 100
    assert state.attributes["mode"] == NumberMode.SLIDER


async def test_screen_brightness_writes_the_device_s_own_index(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A percentage on the slider is a 0..255 index on the wire."""
    await _numbers(hass, aioclient_mock)
    entity_id = entity_id_for(hass, "_screen_brightness")

    await _set(hass, entity_id, 100)
    assert query_of(aioclient_mock, SET_SCREEN_BRIGHTNESS) == {"index": "255"}

    await _set(hass, entity_id, 50)
    assert query_of(aioclient_mock, SET_SCREEN_BRIGHTNESS) == {"index": "128"}

    await _set(hass, entity_id, 0)
    assert query_of(aioclient_mock, SET_SCREEN_BRIGHTNESS) == {"index": "0"}


async def test_a_move_is_shown_at_once_and_then_confirmed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Brightness is slow-tier, so a drag must not wait 30 s to show."""
    device = {"index": 30}

    def _accept(query: dict[str, str]) -> None:
        device["index"] = int(query["index"])

    prime_device(
        aioclient_mock,
        {
            GET_SCREEN_BRIGHTNESS: answers_with(
                lambda: _brightness_at(device["index"])
            ),
            SET_SCREEN_BRIGHTNESS: records_writes([], _accept),
        },
    )
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_screen_brightness")

    coordinator = entity_object(hass, entity_id).coordinator
    confirming = coordinator.async_refresh_settings
    shown: list[str] = []

    async def _watch() -> None:
        shown.append(hass.states.get(entity_id).state)
        await confirming()

    with patch.object(coordinator, "async_refresh_settings", _watch):
        await _set(hass, entity_id, 40)

    # Shown while the confirming read was still in flight, and still shown
    # afterwards because the device really did move.
    assert [float(value) for value in shown] == [40]
    assert float(hass.states.get(entity_id).state) == 40


async def test_a_move_the_device_ignores_snaps_back(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The guess is a courtesy; the device's own report is the truth."""
    await _numbers(hass, aioclient_mock)
    entity_id = entity_id_for(hass, "_screen_brightness")

    # The seam keeps answering currentValue 30 whatever is written to it.
    await _set(hass, entity_id, 90)

    assert float(hass.states.get(entity_id).state) == 12


async def test_a_change_made_on_the_device_shows_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The slider is polled, so turning it down on the unit itself lands."""
    await _numbers(hass, aioclient_mock)
    entity_id = entity_id_for(hass, "_screen_brightness")

    aioclient_mock.clear_requests()
    prime_device(aioclient_mock, {GET_SCREEN_BRIGHTNESS: {"json": _brightness_at(255)}})
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert float(hass.states.get(entity_id).state) == 100


async def test_a_slider_the_device_stopped_answering_keeps_its_last_value(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A settings endpoint going quiet is a nuisance, not an outage."""
    await _numbers(hass, aioclient_mock)
    entity_id = entity_id_for(hass, "_screen_brightness")

    aioclient_mock.clear_requests()
    prime_device(
        aioclient_mock,
        {GET_SCREEN_BRIGHTNESS: {"exc": aiohttp.ClientError("no answer")}},
    )
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert float(hass.states.get(entity_id).state) == 12


async def test_it_is_a_config_entity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Brightness configures the device rather than reporting on it."""
    await _numbers(hass, aioclient_mock)

    entry = er.async_get(hass).async_get(entity_id_for(hass, "_screen_brightness"))
    assert entry.entity_category is EntityCategory.CONFIG


async def test_a_unit_without_the_brightness_setting_never_gets_the_slider(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The tree tag is what says this unit's screen can be dimmed."""
    await _numbers(
        hass,
        aioclient_mock,
        {
            GET_SYSTEM_SETTINGS: {
                "json": settings_without(SETTING_TAG_SCREEN_BRIGHTNESS)
            }
        },
    )

    assert not _numbers_matching(hass, "screen_brightness")


async def test_the_a8_has_no_knob_so_it_gets_no_knob_slider(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``getKnobSettingOption`` answers with an empty list on a knob-less unit."""
    await _numbers(hass, aioclient_mock)

    assert not _numbers_matching(hass, "knob_brightness")


async def test_a_unit_with_a_knob_gets_its_own_slider(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The knob write goes to a fixed setter, not one read off a response.

    No capture of a knob-bearing unit exists, so unlike the screen slider this
    one cannot read its setter out of the response.
    """
    await _numbers(
        hass,
        aioclient_mock,
        {
            GET_KNOB_OPTION: {"json": KNOB_OPTION},
            GET_KNOB_BRIGHTNESS: {"json": KNOB_BRIGHTNESS},
        },
    )
    entity_id = entity_id_for(hass, "_knob_brightness")
    assert float(hass.states.get(entity_id).state) == 50

    await _set(hass, entity_id, 100)

    assert query_of(aioclient_mock, SET_KNOB_BRIGHTNESS) == {"index": "255"}


async def test_a_knob_whose_range_the_device_omits_still_works(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No A6 has been captured, so failing closed here would be untestable.

    The screen slider can insist on a reported range because every capture has
    one; the knob falls back to an assumed range rather than reading
    ``unknown`` and refusing every write.
    """
    await _numbers(
        hass,
        aioclient_mock,
        {
            GET_KNOB_OPTION: {"json": KNOB_OPTION},
            GET_KNOB_BRIGHTNESS: {"json": {"status": 200, "currentValue": 64}},
        },
    )
    entity_id = entity_id_for(hass, "_knob_brightness")
    assert float(hass.states.get(entity_id).state) == 25

    await _set(hass, entity_id, 100)

    assert query_of(aioclient_mock, SET_KNOB_BRIGHTNESS) == {"index": "255"}
