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
    SCREENSAVER_KEEPALIVE_CYCLES,
    SETTINGS_REFRESH_CYCLES,
    SETTING_TAG_AUTO_CHANGE_SOURCE,
    SETTING_TAG_CD_AUTO_PLAY,
    SETTING_TAG_GAPLESS,
    SETTING_TAG_SCREENSAVER,
    SETTING_TAG_SUBWOOFER,
)

from .helpers import (
    GET_POWER_OPTION,
    GET_SCREENSAVER_TIME_LIST,
    GET_STATE,
    GET_SUB_OUTPUT,
    GET_SYSTEM_SETTINGS,
    SET_AUTO_CHANGE_SOURCE,
    SET_CD_AUTO_PLAY,
    SET_EOS_ENGINE,
    SET_GAPLESS,
    SET_POWER_OPTION,
    SET_SCREENSAVER_TIME,
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


async def test_auto_change_source_reads_its_state_from_the_tree(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Auto Change Source is on in the capture."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    entity_id = entity_id_for(hass, "_auto_change_source_internal_player")
    assert hass.states.get(entity_id).state == STATE_ON


async def test_auto_change_source_writes_its_own_endpoint(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """It has its own setter, distinct from the other tree toggles."""
    writes: list[dict[str, str]] = []
    prime_device(aioclient_mock, {SET_AUTO_CHANGE_SOURCE: records_writes(writes)})
    await setup_integration(hass)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id_for(hass, "_auto_change_source_internal_player")},
        blocking=True,
    )

    assert writes == [{"switch": "0"}]


async def test_a_unit_without_auto_change_source_never_gets_the_switch(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Tag presence is the capability signal, one toggle at a time."""
    prime_device(
        aioclient_mock,
        {
            GET_SYSTEM_SETTINGS: {
                "json": settings_without(SETTING_TAG_AUTO_CHANGE_SOURCE)
            }
        },
    )
    await setup_integration(hass)

    assert not [
        entity_id
        for entity_id in hass.states.async_entity_ids(SWITCH_DOMAIN)
        if "auto_change_source" in entity_id
    ]


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


# The capture's default label ("Screen off") reads as lit — see
# docs/screen-power-label-locales.md: the label names the state the *next*
# press would leave, not the one the screen is in now.
LABEL_SCREEN_ON = "Screen off"
LABEL_SCREEN_OFF = "Screen on"
LABEL_UNRECOGNISED = "Bildschirm an"  # not a translation the table carries


def _power_option_with_screen(label: str) -> dict:
    """Build the power menu as the device reports it with the screen label set."""
    menu = fixture_json("getpoweroption.json")
    for item in menu["data"]:
        if item["tag"] == "screen":
            item["name"] = label
    return menu


def _fake_screen_device(aioclient_mock, *, on: bool) -> dict:
    """Prime a device whose screen label flips when the toggle is written."""
    device = {"on": on, "writes": []}

    def _flip(query: dict[str, str]) -> None:
        if query.get("tag") == "screen":
            device["on"] = not device["on"]

    prime_device(
        aioclient_mock,
        {
            GET_POWER_OPTION: answers_with(
                lambda: _power_option_with_screen(
                    LABEL_SCREEN_ON if device["on"] else LABEL_SCREEN_OFF
                )
            ),
            SET_POWER_OPTION: records_writes(device["writes"], _flip),
        },
    )
    return device


async def test_the_screen_switch_reads_the_devices_real_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The capture's label is read straight off, no memory involved."""
    entity_id = await _screen(hass, aioclient_mock)

    assert hass.states.get(entity_id).state == STATE_ON


async def test_a_screen_change_at_the_front_panel_shows_up_within_one_poll(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The power menu is polled on the settings tier, so this is not a guess."""
    entity_id = await _screen(hass, aioclient_mock)
    assert hass.states.get(entity_id).state == STATE_ON

    aioclient_mock.clear_requests()
    prime_device(
        aioclient_mock,
        {GET_POWER_OPTION: {"json": _power_option_with_screen(LABEL_SCREEN_OFF)}},
    )
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert hass.states.get(entity_id).state == STATE_OFF


async def test_the_screen_switch_writes_the_power_menu_s_screen_tag(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """One momentary action does both directions — the device only toggles."""
    _fake_screen_device(aioclient_mock, on=True)
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_screen")
    assert hass.states.get(entity_id).state == STATE_ON

    await _call(hass, entity_id, SERVICE_TURN_OFF)

    assert query_of(aioclient_mock, SET_POWER_OPTION) == {"tag": "screen"}
    assert hass.states.get(entity_id).state == STATE_OFF

    await _call(hass, entity_id, SERVICE_TURN_ON)

    assert query_of(aioclient_mock, SET_POWER_OPTION) == {"tag": "screen"}
    assert hass.states.get(entity_id).state == STATE_ON


async def test_switching_the_screen_to_where_it_already_reads_writes_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The guard compares against the reading, so a no-op cannot invert it."""
    _fake_screen_device(aioclient_mock, on=True)
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_screen")

    await _call(hass, entity_id, SERVICE_TURN_ON)

    assert calls_to(aioclient_mock, SET_POWER_OPTION) == 0
    assert hass.states.get(entity_id).state == STATE_ON


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
        if entity_id.endswith("_screen")
    ]


async def test_the_screen_switch_does_not_assume_its_state_for_a_recognised_label(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A real reading means a real toggle, not a pair of guessing buttons."""
    entity_id = await _screen(hass, aioclient_mock)

    assert "assumed_state" not in hass.states.get(entity_id).attributes


async def test_an_unrecognised_label_falls_back_to_assuming_its_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A locale the table has never seen degrades to the old guess-only design."""
    entity_id = await _screen(
        hass,
        aioclient_mock,
        {GET_POWER_OPTION: {"json": _power_option_with_screen(LABEL_UNRECOGNISED)}},
    )

    assert hass.states.get(entity_id).state == STATE_UNKNOWN
    assert hass.states.get(entity_id).attributes["assumed_state"] is True


async def test_an_unrecognised_label_still_picks_its_last_request_back_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Restarting must not re-arm the coin flip a blank guess would cost.

    Without this, a restart leaves the switch at unknown; the next
    ``turn_off`` then passes the "already there?" guard and toggles a screen
    that was already dark back on.
    """
    mock_restore_cache(hass, [State("switch.eversolo_dmp_a8_gen_2_screen", STATE_OFF)])
    entity_id = await _screen(
        hass,
        aioclient_mock,
        {GET_POWER_OPTION: {"json": _power_option_with_screen(LABEL_UNRECOGNISED)}},
    )
    assert hass.states.get(entity_id).state == STATE_OFF

    await _call(hass, entity_id, SERVICE_TURN_OFF)

    assert calls_to(aioclient_mock, SET_POWER_OPTION) == 0
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_an_unrecognised_label_keeps_its_guess_across_a_settings_hiccup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Its guess is all there is; a failed poll can never confirm or correct it."""
    entity_id = await _screen(
        hass,
        aioclient_mock,
        {GET_POWER_OPTION: {"json": _power_option_with_screen(LABEL_UNRECOGNISED)}},
    )
    await _call(hass, entity_id, SERVICE_TURN_OFF)

    aioclient_mock.clear_requests()
    prime_device(
        aioclient_mock,
        {GET_POWER_OPTION: {"exc": aiohttp.ClientError("flaky")}},
    )
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert hass.states.get(entity_id).state == STATE_OFF


async def _suppress_screensaver(
    hass: HomeAssistant, aioclient_mock, overrides=None
) -> str:
    """Set the integration up and return the switch's entity_id."""
    prime_device(aioclient_mock, overrides)
    await setup_integration(hass)
    return entity_id_for(hass, "_suppress_screensaver")


async def test_suppress_screensaver_starts_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """It is a request the integration makes, not a device reading — off by default."""
    entity_id = await _suppress_screensaver(hass, aioclient_mock)

    assert hass.states.get(entity_id).state == STATE_OFF
    assert "assumed_state" not in hass.states.get(entity_id).attributes


async def test_turning_it_on_does_not_touch_the_device_by_itself(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Flipping the switch only arms the keep-alive; it fires on later cycles."""
    entity_id = await _suppress_screensaver(hass, aioclient_mock)

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert hass.states.get(entity_id).state == STATE_ON
    assert calls_to(aioclient_mock, GET_SCREENSAVER_TIME_LIST) == 0
    assert calls_to(aioclient_mock, SET_SCREENSAVER_TIME) == 0


async def test_the_keepalive_fires_once_the_interval_elapses_while_playing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Suppression on, and the device playing, resets the idle clock on schedule."""
    entity_id = await _suppress_screensaver(
        hass,
        aioclient_mock,
        {
            GET_SCREENSAVER_TIME_LIST: {
                "json": fixture_json("getscreensavertimelist.json")
            },
            SET_SCREENSAVER_TIME: {"text": '{"status":200}'},
        },
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    await advance_cycles(hass, freezer, SCREENSAVER_KEEPALIVE_CYCLES - 1)
    assert calls_to(aioclient_mock, SET_SCREENSAVER_TIME) == 0

    await advance_cycles(hass, freezer, 1)

    assert calls_to(aioclient_mock, GET_SCREENSAVER_TIME_LIST) == 1
    # The captured list's own current index (unchanged) — a same-value write.
    assert query_of(aioclient_mock, SET_SCREENSAVER_TIME) == {"index": "5"}


async def test_the_keepalive_does_not_fire_while_switched_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The default state: nothing is touched unless the switch says so."""
    await _suppress_screensaver(
        hass,
        aioclient_mock,
        {
            GET_SCREENSAVER_TIME_LIST: {
                "json": fixture_json("getscreensavertimelist.json")
            }
        },
    )

    await advance_cycles(hass, freezer, SCREENSAVER_KEEPALIVE_CYCLES)

    assert calls_to(aioclient_mock, SET_SCREENSAVER_TIME) == 0


async def test_the_keepalive_does_not_fire_while_nothing_is_playing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """On, but idle: the device's own configured timeout is left to apply."""
    stopped = fixture_json("getstate_spotify_disc_loaded.json")
    stopped["everSoloPlayInfo"]["playStatus"] = 0
    entity_id = await _suppress_screensaver(
        hass,
        aioclient_mock,
        {
            GET_STATE: {"json": stopped},
            GET_SCREENSAVER_TIME_LIST: {
                "json": fixture_json("getscreensavertimelist.json")
            },
        },
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    await advance_cycles(hass, freezer, SCREENSAVER_KEEPALIVE_CYCLES)

    assert calls_to(aioclient_mock, SET_SCREENSAVER_TIME) == 0


async def test_a_volume_change_keeps_the_screensaver_at_bay_on_an_inert_input(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Ticket 19: eARC has no "carrying audio" signal — a volume nudge stands in.

    Unlike the playback-driven keepalive, a single volume change touches the
    device immediately rather than waiting out ``SCREENSAVER_KEEPALIVE_CYCLES``
    of "changed" — a one-off nudge would never accumulate that many.
    """
    current = {"state": fixture_json("getstate_earc_stuck_playing.json")}
    entity_id = await _suppress_screensaver(
        hass,
        aioclient_mock,
        {
            GET_STATE: answers_with(lambda: current["state"]),
            GET_SCREENSAVER_TIME_LIST: {
                "json": fixture_json("getscreensavertimelist.json")
            },
            SET_SCREENSAVER_TIME: {"text": '{"status":200}'},
        },
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await advance_cycles(hass, freezer, 1)
    assert calls_to(aioclient_mock, SET_SCREENSAVER_TIME) == 0

    louder = fixture_json("getstate_earc_stuck_playing.json")
    louder["volumeData"]["currenttVolume"] += 1
    current["state"] = louder
    await advance_cycles(hass, freezer, 1)

    assert calls_to(aioclient_mock, SET_SCREENSAVER_TIME) == 1


async def test_a_volume_change_while_switched_off_does_not_misfire_once_turned_on(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Volume is tracked even while the switch is off.

    Otherwise turning the switch on later would compare the next reading
    against a stale pre-off baseline and touch the device immediately even
    though nothing changed since the switch came on.
    """
    current = {"state": fixture_json("getstate_earc_stuck_playing.json")}
    entity_id = await _suppress_screensaver(
        hass,
        aioclient_mock,
        {
            GET_STATE: answers_with(lambda: current["state"]),
            GET_SCREENSAVER_TIME_LIST: {
                "json": fixture_json("getscreensavertimelist.json")
            },
            SET_SCREENSAVER_TIME: {"text": '{"status":200}'},
        },
    )

    louder = fixture_json("getstate_earc_stuck_playing.json")
    louder["volumeData"]["currenttVolume"] += 1
    current["state"] = louder
    await advance_cycles(hass, freezer, 1)
    assert calls_to(aioclient_mock, SET_SCREENSAVER_TIME) == 0

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await advance_cycles(hass, freezer, 1)

    assert calls_to(aioclient_mock, SET_SCREENSAVER_TIME) == 0


async def test_suppress_screensaver_picks_its_last_request_back_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A restart must not silently drop back to "not suppressing"."""
    mock_restore_cache(
        hass,
        [
            State(
                "switch.eversolo_dmp_a8_gen_2_suppress_screensaver_during_playback",
                STATE_ON,
            )
        ],
    )
    entity_id = await _suppress_screensaver(
        hass,
        aioclient_mock,
        {
            GET_SCREENSAVER_TIME_LIST: {
                "json": fixture_json("getscreensavertimelist.json")
            },
            SET_SCREENSAVER_TIME: {"text": '{"status":200}'},
        },
    )

    assert hass.states.get(entity_id).state == STATE_ON

    await advance_cycles(hass, freezer, SCREENSAVER_KEEPALIVE_CYCLES)

    assert calls_to(aioclient_mock, SET_SCREENSAVER_TIME) == 1


async def test_suppress_screensaver_is_a_config_entity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """It configures integration behaviour rather than reporting on the device."""
    entity_id = await _suppress_screensaver(hass, aioclient_mock)

    entry = er.async_get(hass).async_get(entity_id)
    assert entry.entity_category is EntityCategory.CONFIG


async def test_a_unit_without_a_screensaver_setting_never_gets_the_switch(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No tag in the tree, no switch — same detection style as every other gate."""
    prime_device(
        aioclient_mock,
        {GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_SCREENSAVER)}},
    )
    await setup_integration(hass)

    assert not [
        entity_id
        for entity_id in hass.states.async_entity_ids(SWITCH_DOMAIN)
        if "suppress_screensaver" in entity_id
    ]
