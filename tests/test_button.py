"""Button tests: the two power buttons the device says it accepts."""

from __future__ import annotations

import aiohttp
from homeassistant.components.button import ButtonDeviceClass, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.const import SETTINGS_REFRESH_CYCLES

from .helpers import (
    GET_MODEL,
    GET_STATE,
    SET_POWER_OPTION,
    advance_cycles,
    entity_id_for,
    fixture_json,
    prime_device,
    query_of,
    setup_integration,
)

BUTTON_DOMAIN = "button"


def _button_ids(hass: HomeAssistant, key: str) -> list[str]:
    """Every button entity whose id names a given key."""
    return [
        entity_id
        for entity_id in hass.states.async_entity_ids(BUTTON_DOMAIN)
        if key in entity_id
    ]


def _model_without(*flags: str) -> dict:
    """Return the A8's getModel with some ``ableRemote*`` flags cleared.

    The power buttons are gated on what the unit says it accepts, so clearing a
    flag is how a test stands in for a model that cannot do that at all.
    """
    model = fixture_json("getmodel.json")
    for flag in flags:
        model[flag] = False
    return model


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    """Press a button the way Home Assistant would."""
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def test_reboot_sends_the_reboot_command(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Reboot is the ``setPowerOption`` command tagged ``reboot``."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    await _press(hass, entity_id_for(hass, "_reboot"))

    assert query_of(aioclient_mock, SET_POWER_OPTION) == {"tag": "reboot"}


async def test_power_off_sends_the_poweroff_command(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Power Off is the same command tagged ``poweroff`` — a different tag."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    await _press(hass, entity_id_for(hass, "_power_off"))

    assert query_of(aioclient_mock, SET_POWER_OPTION) == {"tag": "poweroff"}


async def test_reboot_is_a_restart_button(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Its device class is what makes the frontend show it as a restart."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    entity_id = entity_id_for(hass, "_reboot")
    assert hass.states.get(entity_id).attributes["device_class"] == (
        ButtonDeviceClass.RESTART
    )


async def test_both_power_buttons_configure_the_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """They act on the device rather than reporting on it."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    registry = er.async_get(hass)
    for suffix in ("_reboot", "_power_off"):
        entry = registry.async_get(entity_id_for(hass, suffix))
        assert entry.entity_category is EntityCategory.CONFIG


async def test_a_unit_that_cannot_reboot_never_gets_the_button(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Each button is gated on its own flag, so the other one survives."""
    prime_device(
        aioclient_mock, {GET_MODEL: {"json": _model_without("ableRemoteReboot")}}
    )
    await setup_integration(hass)

    assert not _button_ids(hass, "reboot")
    assert _button_ids(hass, "power_off")


async def test_a_unit_that_cannot_shut_down_never_gets_the_button(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Likewise for shutdown: no flag, no Power Off."""
    prime_device(
        aioclient_mock, {GET_MODEL: {"json": _model_without("ableRemoteShutdown")}}
    )
    await setup_integration(hass)

    assert not _button_ids(hass, "power_off")
    assert _button_ids(hass, "reboot")


async def test_a_device_that_was_off_at_setup_still_gets_its_power_buttons(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The gate waits for the unit to say, rather than deciding "no" without it."""
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})
    await setup_integration(hass)

    assert not _button_ids(hass, "reboot")

    # The unit comes back. What matters is that the buttons turn up on their
    # own, without a reload — not which exact poll carries the capabilities.
    aioclient_mock.clear_requests()
    prime_device(aioclient_mock)
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert _button_ids(hass, "reboot")
    assert _button_ids(hass, "power_off")


async def test_nothing_offers_to_switch_the_device_on(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No entity of any platform offers power-on: the A8 cannot be woken.

    ``ableRemoteSleep`` is false on this unit and it is on WiFi, so
    Wake-on-LAN is a beyond-MVP experiment rather than something to ship a
    button for. (The player's own turn_on/off is pinned in test_media_player.)
    """
    prime_device(aioclient_mock)
    entry = await setup_integration(hass)

    # Named, so that re-adding a wake control under any name fails here rather
    # than only if it happens to be called "power_on".
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    buttons = {
        entity.unique_id.removeprefix(f"{entry.entry_id}_")
        for entity in entities
        if entity.domain == BUTTON_DOMAIN
    }
    assert buttons == {"reboot", "power_off"}


async def test_the_power_buttons_go_with_the_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A unit that is not answering cannot be told to reboot, so say so.

    They are CONFIG entities, but unlike a setting there is no last value to
    soft-keep: pressing one while the device is unreachable could only fail.
    """
    prime_device(aioclient_mock)
    entry = await setup_integration(hass)
    entity_id = entity_id_for(hass, "_reboot")
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
