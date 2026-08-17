"""Switch tests: the device's ?switch= toggles, starting with CD Auto Play."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import mock_restore_cache
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.const import (
    SETTINGS_REFRESH_CYCLES,
    SETTING_TAG_CD_AUTO_PLAY,
    SETTING_TAG_GAPLESS,
    SETTING_TAG_SUBWOOFER,
)

from .helpers import (
    GET_POWER_OPTION,
    GET_STATE,
    GET_SUB_OUTPUT,
    GET_SYSTEM_SETTINGS,
    SET_CD_AUTO_PLAY,
    SET_EOS_ENGINE,
    SET_GAPLESS,
    SET_POWER_OPTION,
    SET_SUBWOOFER,
    advance_cycles,
    answers_with,
    calls_to,
    entity_id_for,
    entity_object,
    fixture_json,
    prime_device,
    query_of,
    records_writes,
    settings_without,
    setup_integration,
)

SWITCH_DOMAIN = "switch"


def _tree_with_cd_auto_play(enabled: bool) -> dict:
    """Build the settings tree as the device reports it with the toggle set."""
    tree = fixture_json("getsystemsettings.json")
    for group in tree["settings"]:
        for item in group.get("items", []):
            if item.get("tag") == SETTING_TAG_CD_AUTO_PLAY:
                item["switchStatus"] = enabled
    return tree


async def _switch(hass: HomeAssistant, aioclient_mock, overrides=None) -> str:
    """Set the integration up and return the CD Auto Play entity_id."""
    prime_device(aioclient_mock, overrides)
    await setup_integration(hass)
    return entity_id_for(hass, "_cd_auto_play")


def _fake_device(aioclient_mock, *, cd_auto_play: bool) -> dict:
    """Prime a device whose toggle actually changes when it is written to."""
    device = {"cd_auto_play": cd_auto_play, "writes": []}

    def _accept(query: dict[str, str]) -> None:
        device["cd_auto_play"] = query["switch"] == "1"

    prime_device(
        aioclient_mock,
        {
            GET_SYSTEM_SETTINGS: answers_with(
                lambda: _tree_with_cd_auto_play(device["cd_auto_play"])
            ),
            SET_CD_AUTO_PLAY: records_writes(device["writes"], _accept),
        },
    )
    return device


async def _turn(hass: HomeAssistant, entity_id: str, service: str) -> str:
    """Call the switch, returning the state it showed before the confirm read."""
    coordinator = entity_object(hass, entity_id).coordinator
    confirming = coordinator.async_refresh_settings
    shown: list[str] = []

    async def _watch() -> None:
        shown.append(hass.states.get(entity_id).state)
        await confirming()

    with patch.object(coordinator, "async_refresh_settings", _watch):
        await hass.services.async_call(
            SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
        )

    assert shown, "the write never reached the confirming read"
    return shown[0]


async def test_cd_auto_play_reads_its_state_from_the_settings_tree(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """There is no getter for a toggle; the tree is where its state lives."""
    entity_id = await _switch(hass, aioclient_mock)

    # The capture has it off, and the tree is the only thing that says so.
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_a_toggle_flipped_on_the_device_shows_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The tree is polled on the slow tier, so front-panel changes land."""
    entity_id = await _switch(hass, aioclient_mock)
    assert hass.states.get(entity_id).state == STATE_OFF

    aioclient_mock.clear_requests()
    prime_device(
        aioclient_mock, {GET_SYSTEM_SETTINGS: {"json": _tree_with_cd_auto_play(True)}}
    )
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert hass.states.get(entity_id).state == STATE_ON


async def test_a_write_is_shown_at_once_and_then_confirmed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``switch=true`` is rejected by the device (status 805), so send 1.

    Driven against a device that actually changes: the write flips what the
    settings tree then reports, which is the round trip the switch relies on.
    """
    device = _fake_device(aioclient_mock, cd_auto_play=False)
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_cd_auto_play")
    assert hass.states.get(entity_id).state == STATE_OFF

    reads = calls_to(aioclient_mock, GET_SYSTEM_SETTINGS)
    shown_before_confirming = await _turn(hass, entity_id, SERVICE_TURN_ON)

    assert device["writes"] == [{"switch": "1"}]
    # Shown while the confirming read was still in flight, not 30 s later.
    assert shown_before_confirming == STATE_ON
    assert calls_to(aioclient_mock, GET_SYSTEM_SETTINGS) == reads + 1
    assert hass.states.get(entity_id).state == STATE_ON


async def test_turning_it_off_sends_zero(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The off write is the same call with the flag cleared."""
    device = _fake_device(aioclient_mock, cd_auto_play=True)
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_cd_auto_play")
    assert hass.states.get(entity_id).state == STATE_ON

    await _turn(hass, entity_id, SERVICE_TURN_OFF)

    assert device["writes"] == [{"switch": "0"}]
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_a_write_the_device_ignores_snaps_back(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The guess is a courtesy; the device's own report is the truth."""
    prime_device(aioclient_mock)
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_cd_auto_play")

    # The seam answers "off" no matter what is written to it.
    shown_before_confirming = await _turn(hass, entity_id, SERVICE_TURN_ON)

    assert shown_before_confirming == STATE_ON
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_it_is_a_config_entity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """It configures the device rather than reporting on it."""
    entity_id = await _switch(hass, aioclient_mock)

    entry = er.async_get(hass).async_get(entity_id)
    assert entry.entity_category is EntityCategory.CONFIG


async def test_a_unit_without_a_cd_drive_never_gets_the_switch(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No disc slot, no CD controls."""
    prime_device(
        aioclient_mock,
        {GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_CD_AUTO_PLAY)}},
    )
    await setup_integration(hass)

    assert not [
        entity_id
        for entity_id in hass.states.async_entity_ids(SWITCH_DOMAIN)
        if "cd_auto_play" in entity_id
    ]


async def test_the_gen_two_audio_toggles_read_their_state_from_the_tree(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Gapless and the EOS engine are both on in the capture."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    assert hass.states.get(entity_id_for(hass, "_gapless")).state == STATE_ON
    assert hass.states.get(entity_id_for(hass, "_eos_engine")).state == STATE_ON


async def test_the_desired_state_is_sent_directly_not_toggled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``?switch=0`` is off.

    The vendor's app appends ``state ^ 1``, but its callers hand it the state
    the switch is *currently* in — that XOR is how it toggles, not the wire
    encoding. Reproducing it would turn gapless on when asked to turn it off.
    """
    writes: list[dict[str, str]] = []
    prime_device(aioclient_mock, {SET_GAPLESS: records_writes(writes)})
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_gapless")
    assert hass.states.get(entity_id).state == STATE_ON

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert writes == [{"switch": "0"}]


async def test_the_eos_engine_writes_its_own_endpoint(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Each toggle has its own setter; none of them share one."""
    writes: list[dict[str, str]] = []
    prime_device(aioclient_mock, {SET_EOS_ENGINE: records_writes(writes)})
    await setup_integration(hass)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id_for(hass, "_eos_engine")},
        blocking=True,
    )

    assert writes == [{"switch": "0"}]


async def test_the_subwoofer_toggle_comes_from_its_own_sub_page(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The main tree only points at the subwoofer page; the state is inside it."""
    writes: list[dict[str, str]] = []
    prime_device(aioclient_mock, {SET_SUBWOOFER: records_writes(writes)})
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_subwoofer_output")
    assert hass.states.get(entity_id).state == STATE_ON

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert writes == [{"switch": "0"}]


async def test_a_unit_without_a_subwoofer_output_never_gets_the_switch(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No subwoofer page, no subwoofer control — and no wasted poll for it."""
    prime_device(
        aioclient_mock,
        {GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_SUBWOOFER)}},
    )
    await setup_integration(hass)

    assert not [
        entity_id
        for entity_id in hass.states.async_entity_ids(SWITCH_DOMAIN)
        if "subwoofer" in entity_id
    ]
    assert calls_to(aioclient_mock, GET_SUB_OUTPUT) == 0


async def test_a_unit_without_gapless_never_gets_the_switch(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Tag presence is the capability signal, one toggle at a time."""
    prime_device(
        aioclient_mock,
        {GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_GAPLESS)}},
    )
    await setup_integration(hass)

    assert not [
        entity_id
        for entity_id in hass.states.async_entity_ids(SWITCH_DOMAIN)
        if "gapless" in entity_id
    ]


async def test_a_device_that_was_off_at_setup_still_gets_its_switch(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Restarting while the unit is off must not cost gated entities a reload."""
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})
    await setup_integration(hass)

    assert not hass.states.async_entity_ids(SWITCH_DOMAIN)

    # The unit comes back. What matters is that the switch turns up on its own,
    # without a reload — not which exact poll carries the capabilities.
    aioclient_mock.clear_requests()
    prime_device(aioclient_mock)
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    entity_id = entity_id_for(hass, "_cd_auto_play")
    assert hass.states.get(entity_id).state == STATE_OFF


async def _screen(hass: HomeAssistant, aioclient_mock, overrides=None) -> str:
    """Set the integration up and return the screen switch's entity_id."""
    prime_device(aioclient_mock, overrides)
    await setup_integration(hass)
    return entity_id_for(hass, "_screen")


async def _call(hass: HomeAssistant, entity_id: str, service: str) -> None:
    """Call a switch service without watching for a confirming read."""
    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def test_the_screen_starts_out_unknown_because_nothing_reports_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No payload anywhere says whether the front screen is lit.

    Not ``getState``, not the settings tree, not the power menu — so this
    switch assumes nothing rather than claiming a state it cannot have read.
    """
    entity_id = await _screen(hass, aioclient_mock)

    assert hass.states.get(entity_id).state == STATE_UNKNOWN


async def test_the_screen_switch_writes_the_power_menu_s_screen_tag(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """One momentary action does both directions — the device only toggles."""
    entity_id = await _screen(hass, aioclient_mock)

    await _call(hass, entity_id, SERVICE_TURN_OFF)

    assert query_of(aioclient_mock, SET_POWER_OPTION) == {"tag": "screen"}
    assert hass.states.get(entity_id).state == STATE_OFF

    await _call(hass, entity_id, SERVICE_TURN_ON)

    assert query_of(aioclient_mock, SET_POWER_OPTION) == {"tag": "screen"}
    assert hass.states.get(entity_id).state == STATE_ON


async def test_switching_the_screen_to_where_it_already_is_writes_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The call is a toggle, so a redundant one would do the opposite."""
    entity_id = await _screen(hass, aioclient_mock)
    await _call(hass, entity_id, SERVICE_TURN_OFF)
    writes = calls_to(aioclient_mock, SET_POWER_OPTION)

    await _call(hass, entity_id, SERVICE_TURN_OFF)

    assert calls_to(aioclient_mock, SET_POWER_OPTION) == writes
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_the_screen_switch_keeps_its_guess_across_a_settings_hiccup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Its guess is all there is; a poll can never confirm or correct it."""
    entity_id = await _screen(hass, aioclient_mock)
    await _call(hass, entity_id, SERVICE_TURN_OFF)

    aioclient_mock.clear_requests()
    prime_device(
        aioclient_mock,
        {GET_SYSTEM_SETTINGS: {"exc": aiohttp.ClientError("flaky")}},
    )
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert hass.states.get(entity_id).state == STATE_OFF


async def test_it_is_a_config_entity_too(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Blanking the screen configures the unit rather than reporting on it."""
    entity_id = await _screen(hass, aioclient_mock)

    entry = er.async_get(hass).async_get(entity_id)
    assert entry.entity_category is EntityCategory.CONFIG


async def test_a_unit_whose_power_menu_has_no_screen_never_gets_the_switch(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The power menu offering the tag is the only thing that says it has one."""
    menu = fixture_json("getpoweroption.json")
    menu["data"] = [item for item in menu["data"] if item["tag"] != "screen"]
    prime_device(aioclient_mock, {GET_POWER_OPTION: {"json": menu}})
    await setup_integration(hass)

    assert not [
        entity_id
        for entity_id in hass.states.async_entity_ids(SWITCH_DOMAIN)
        if "screen" in entity_id
    ]


async def test_the_screen_switch_says_it_is_assuming_its_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The frontend then offers on and off separately, not a lying toggle."""
    entity_id = await _screen(hass, aioclient_mock)

    assert hass.states.get(entity_id).attributes["assumed_state"] is True
    # The settings toggles do read their state, so they claim none of this.
    assert (
        "assumed_state"
        not in hass.states.get(entity_id_for(hass, "_cd_auto_play")).attributes
    )


async def test_the_screen_switch_picks_its_last_request_back_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Restarting must not re-arm the coin flip a blank guess would cost.

    Without this, a restart leaves the switch at unknown; the next
    ``turn_off`` then passes the "already there?" guard and toggles a screen
    that was already dark back on.
    """
    mock_restore_cache(hass, [State("switch.eversolo_dmp_a8_gen_2_screen", STATE_OFF)])
    entity_id = await _screen(hass, aioclient_mock)
    assert hass.states.get(entity_id).state == STATE_OFF

    await _call(hass, entity_id, SERVICE_TURN_OFF)

    assert calls_to(aioclient_mock, SET_POWER_OPTION) == 0
    assert hass.states.get(entity_id).state == STATE_OFF
