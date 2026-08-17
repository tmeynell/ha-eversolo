"""Binary sensor tests: whether the device is applying DSP / EQ right now.

Both flags come out of ``getState``, so every case here is driven from the real
captures — mutated where the development unit cannot produce the reading
itself (it has no EQ side, and had DSP engaged in all three captures).
"""

from __future__ import annotations

import aiohttp
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.const import SETTINGS_REFRESH_CYCLES

from .helpers import (
    GET_STATE,
    advance_cycles,
    entity_id_for,
    prime_device,
    setup_integration,
    state_with,
)

BINARY_SENSOR_DOMAIN = "binary_sensor"


def _entity_ids(hass: HomeAssistant) -> list[str]:
    """Every binary sensor the integration created."""
    return hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)


def _state_on_input(tag: str, *, dsp_active: bool) -> dict:
    """Return the capture as it reads with a different input selected.

    ``getState`` names the live input in ``volumeData.intputTag``, in the
    device's compound ``XMOS-XMOS`` form, and carries ``dspActive`` for that
    same input in the same payload.
    """
    state = state_with(dspActive=dsp_active)
    state["volumeData"]["intputTag"] = f"{tag}-{tag}"
    return state


async def test_dsp_active_reports_the_selected_input(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The capture has DSP engaged on the input it was taken on."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    state = hass.states.get(entity_id_for(hass, "_dsp_active"))
    assert state.state == STATE_ON


async def test_dsp_reads_off_when_the_selected_input_has_it_disabled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Not a global switch: the same unit reads off on an input DSP is off for."""
    prime_device(aioclient_mock, {GET_STATE: {"json": state_with(dspActive=False)}})
    await setup_integration(hass)

    assert hass.states.get(entity_id_for(hass, "_dsp_active")).state == STATE_OFF


async def test_the_reading_follows_the_selected_input(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Changing source can flip it with nothing else having changed.

    The sensor names the input its reading belongs to, and that name has to
    move in the same poll as the flag — naming the old input while already
    reporting the new one is the confusion the attribute exists to prevent.
    Both come off the one ``getState``, so they cannot disagree.
    """
    prime_device(aioclient_mock)
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_dsp_active")

    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
    assert state.attributes["input"] == "Internal player"

    # The user switches to TV, which is an input DSP is disabled for.
    aioclient_mock.clear_requests()
    prime_device(
        aioclient_mock,
        {GET_STATE: {"json": _state_on_input("EARC", dsp_active=False)}},
    )
    await advance_cycles(hass, freezer, 1)

    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF
    # The settings tier has not been re-read, so this is the live tag talking.
    assert state.attributes["input"] == "TV"


async def test_a_unit_without_the_dsp_side_never_gets_the_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``hasDspSetting`` is the gate, the same way a settings tag is elsewhere."""
    prime_device(aioclient_mock, {GET_STATE: {"json": state_with(hasDspSetting=False)}})
    await setup_integration(hass)

    assert not [
        entity_id for entity_id in _entity_ids(hass) if "dsp_active" in entity_id
    ]


async def test_the_a8_has_no_eq_side_so_gets_no_eq_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """DSP is the input side, EQ the output side; this unit only has the first."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    assert not [
        entity_id for entity_id in _entity_ids(hass) if "eq_active" in entity_id
    ]


async def test_a_unit_with_an_eq_side_gets_the_eq_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Proven against a mutated capture, since the A8 cannot report it."""
    prime_device(
        aioclient_mock,
        {GET_STATE: {"json": state_with(hasEQSetting=True, eqActive=True)}},
    )
    await setup_integration(hass)

    assert hass.states.get(entity_id_for(hass, "_eq_active")).state == STATE_ON


async def test_eq_reads_off_when_the_outputs_have_it_disabled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Which is how the A8's own outputs sit — all three on a stock, off profile."""
    prime_device(
        aioclient_mock,
        {GET_STATE: {"json": state_with(hasEQSetting=True, eqActive=False)}},
    )
    await setup_integration(hass)

    assert hass.states.get(entity_id_for(hass, "_eq_active")).state == STATE_OFF


async def test_they_go_unavailable_when_the_live_tier_fails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Both read getState, so they go down with the media player, not stale."""
    prime_device(aioclient_mock, {GET_STATE: {"json": state_with(hasEQSetting=True)}})
    entry = await setup_integration(hass)
    assert len(_entity_ids(hass)) == 2

    aioclient_mock.clear_requests()
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert [hass.states.get(entity_id).state for entity_id in _entity_ids(hass)] == [
        STATE_UNAVAILABLE,
        STATE_UNAVAILABLE,
    ]


async def test_they_are_diagnostic_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """They report on the device rather than being a primary control."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    entry = er.async_get(hass).async_get(entity_id_for(hass, "_dsp_active"))
    assert entry.entity_category is EntityCategory.DIAGNOSTIC


async def test_a_device_that_was_off_at_setup_still_gets_its_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The gate lives in getState, which an unreachable unit has not sent."""
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})
    await setup_integration(hass)

    assert not _entity_ids(hass)

    # The unit comes back and the sensor turns up on its own, without a reload.
    # Which exact poll carries the capabilities is not the point.
    aioclient_mock.clear_requests()
    prime_device(aioclient_mock)
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert hass.states.get(entity_id_for(hass, "_dsp_active")).state == STATE_ON
