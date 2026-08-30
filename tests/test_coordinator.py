"""Coordinator tests: the two-speed poll, capability detection, availability."""

from __future__ import annotations

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.const import (
    DOMAIN,
    PROCESSING_GATE_CYCLES,
    SETTINGS_REFRESH_CYCLES,
)
from custom_components.eversolo.data import EversoloData

from .helpers import (
    GET_MODEL,
    GET_POWER_OPTION,
    GET_SCREEN_BRIGHTNESS,
    GET_STATE,
    advance_cycles as _advance,
    calls_to,
    fixture_json,
    prime_device,
    setup_integration as _setup,
    state_without,
)


def _select_states(hass: HomeAssistant) -> list:
    """Every select entity's state — the stand-in for 'the device's entities'."""
    return [
        hass.states.get(entity_id)
        for entity_id in hass.states.async_entity_ids("select")
    ]


async def test_both_tiers_land_in_runtime_data(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The coordinator hangs off the entry and carries live + settings + caps."""
    prime_device(aioclient_mock)

    entry = await _setup(hass)

    data = entry.runtime_data.data
    assert isinstance(data, EversoloData)
    # Default fixture is the Spotify-Connect-plus-disc-in-tray capture;
    # playType (6, streaming) says Spotify is what is audible (#02).
    assert data.playback.title == "Brother, Do You Know the Road?"
    assert data.settings["screen_brightness"] is not None
    assert data.settings["vu_mode_state"] is not None
    # getModel's identity is richer than the one getState carries, so it wins.
    assert data.device.android_version == "14"

    # Every platform reads that one snapshot without tripping over it.
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {entity.domain for entity in entities} == {
        "binary_sensor",
        "button",
        "camera",
        "image",
        "media_player",
        "number",
        "select",
        "sensor",
        "switch",
    }


async def test_settings_tier_refreshes_every_sixth_cycle(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The live tier is polled every cycle; the settings tier every sixth."""
    prime_device(aioclient_mock)
    await _setup(hass)

    assert calls_to(aioclient_mock, GET_STATE) == 1
    assert calls_to(aioclient_mock, GET_SCREEN_BRIGHTNESS) == 1

    await _advance(hass, freezer, SETTINGS_REFRESH_CYCLES - 1)

    assert calls_to(aioclient_mock, GET_STATE) == SETTINGS_REFRESH_CYCLES
    assert calls_to(aioclient_mock, GET_SCREEN_BRIGHTNESS) == 1

    await _advance(hass, freezer, 1)

    assert calls_to(aioclient_mock, GET_STATE) == SETTINGS_REFRESH_CYCLES + 1
    assert calls_to(aioclient_mock, GET_SCREEN_BRIGHTNESS) == 2


async def test_capabilities_are_detected_once_and_match_the_a8(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """What hardware the unit has is worked out at setup and then left alone.

    The settings tree it is derived from *is* re-read — it carries the live
    state of every toggle — but the profile behind it is read once.
    """
    prime_device(aioclient_mock)
    entry = await _setup(hass)

    capabilities = entry.runtime_data.data.capabilities
    assert capabilities.has_cd is True
    assert capabilities.has_subwoofer is True
    assert capabilities.has_master_clock is True
    assert capabilities.has_analog_panel is True
    assert capabilities.has_knob is False
    assert capabilities.has_screen_brightness is True
    assert capabilities.has_vu_style is True
    assert capabilities.has_spectrum_style is True
    assert capabilities.has_visualization is True
    # From the power menu rather than the tree — the screen is not in the tree.
    assert capabilities.has_screen_power is True

    await _advance(hass, freezer, SETTINGS_REFRESH_CYCLES * 2)

    assert calls_to(aioclient_mock, GET_MODEL) == 1
    assert entry.runtime_data.data.capabilities == capabilities


async def test_a_write_refreshes_the_settings_tier_immediately(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """async_refresh_settings re-reads the slow tier without a live poll."""
    prime_device(aioclient_mock)
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    await coordinator.async_refresh_settings()

    assert calls_to(aioclient_mock, GET_SCREEN_BRIGHTNESS) == 2
    assert calls_to(aioclient_mock, GET_STATE) == 1

    # A changed payload proves the refresh is published, not just fetched.
    aioclient_mock.clear_requests()
    prime_device(
        aioclient_mock,
        {GET_SCREEN_BRIGHTNESS: {"json": {"currentValue": 255, "maxValue": 255}}},
    )

    await coordinator.async_refresh_settings()

    assert coordinator.data.settings["screen_brightness"]["currentValue"] == 255


async def test_getstate_failure_makes_every_entity_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The live tier is hard: losing getState takes the whole device down."""
    prime_device(aioclient_mock)
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    aioclient_mock.clear_requests()
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    states = _select_states(hass)
    assert states
    assert all(state.state == STATE_UNAVAILABLE for state in states)


async def test_a_device_that_is_off_at_setup_still_loads(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A unit that is off when Home Assistant starts must not fail the entry.

    Nothing can be done to a device that is not answering, but the entry has to
    survive the outage and pick the device up on its own — capability-gated
    entities depend on that (they wait for the poll that finally reports), and
    a failed entry would need a manual reload once the unit came back.
    """
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})

    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.last_update_success is False
    # The entities that need no capability to exist were still created; the
    # gated platforms are the ones that wait for the device to say.
    assert hass.states.async_entity_ids("media_player")


async def test_identity_reaches_the_registry_once_the_device_answers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A device set up while off still gets its model, without a reload."""
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})
    entry = await _setup(hass)

    device = dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)})
    assert device.model is None

    # The unit comes back; the profile read is retried until it lands, so what
    # matters is that identity arrives on its own, not on which exact poll.
    aioclient_mock.clear_requests()
    prime_device(aioclient_mock)
    await _advance(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert entry.runtime_data.last_update_success is True
    device = dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)})
    assert device.model == "DMP-A8 Gen 2"
    assert device.sw_version == "v1.1.50"


async def test_a_rename_on_the_device_reaches_the_registry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Renaming the streamer in the Eversolo app updates HA's name (#73).

    Unlike ``model``/``net_mac``/``firmware`` — hardware facts read once and
    left alone — the name is the one identity field a user changes freely, so
    it is the one still re-read every live cycle.
    """
    prime_device(aioclient_mock)
    entry = await _setup(hass)

    device = dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)})
    assert device.name == "Eversolo DMP-A8 Gen 2"
    (media_player_entity_id,) = hass.states.async_entity_ids("media_player")

    renamed_state = fixture_json("getstate_spotify_disc_loaded.json")
    renamed_state["deviceInfo"]["deviceName"] = "Living Room Streamer"
    aioclient_mock.clear_requests()
    prime_device(aioclient_mock, {GET_STATE: {"json": renamed_state}})
    await _advance(hass, freezer, 1)

    device = dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)})
    assert device.name == "Eversolo Living Room Streamer"
    # The rename doesn't disturb anything else identity carries, nor the
    # entity_id automations key off (#73's explicit constraint) — only the
    # registry's display name moved.
    assert device.model == "DMP-A8 Gen 2"
    assert hass.states.async_entity_ids("media_player") == [media_player_entity_id]


async def test_a_blank_live_device_name_does_not_wipe_the_tracked_name(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A live cycle reporting no name is silence, not a rename to nothing.

    ``EversoloDevice.from_state`` applies no model fallback for a blank
    ``deviceInfo.deviceName``, unlike ``from_model`` — so an empty string
    here must be read the same as it not being reported at all.
    """
    prime_device(aioclient_mock)
    entry = await _setup(hass)

    blanked_state = fixture_json("getstate_spotify_disc_loaded.json")
    blanked_state["deviceInfo"]["deviceName"] = ""
    aioclient_mock.clear_requests()
    prime_device(aioclient_mock, {GET_STATE: {"json": blanked_state}})
    await _advance(hass, freezer, 1)

    device = dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)})
    assert device.name == "Eversolo DMP-A8 Gen 2"


async def test_a_failing_settings_endpoint_does_not_blank_the_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The settings tier is soft: one dead endpoint keeps its last value."""
    prime_device(aioclient_mock)
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    last_known = coordinator.data.settings["screen_brightness"]

    aioclient_mock.clear_requests()
    prime_device(
        aioclient_mock,
        {GET_SCREEN_BRIGHTNESS: {"exc": aiohttp.ClientError("flaky")}},
    )
    await _advance(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert coordinator.last_update_success is True
    assert coordinator.data.settings["screen_brightness"] == last_known
    # Its neighbours still refreshed, and nothing went unavailable.
    assert coordinator.data.settings["vu_mode_state"] is not None
    states = _select_states(hass)
    assert states
    assert all(state.state != STATE_UNAVAILABLE for state in states)


async def test_a_state_without_the_processing_flags_does_not_latch_them_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The device is allowed to answer the DSP question late.

    Every other capability comes from an endpoint that either answers or
    raises, and a failed profile read is retried. ``hasDspSetting`` is a field
    of ``getState``, so a payload that simply leaves it out looks exactly like
    the device saying no — and the profile is read once, which would make that
    mishearing permanent: no error, no log line, no DSP sensor, and nothing to
    reload short of deleting the config entry.

    So the gate stays open rather than being guessed shut, and the entity it
    gates is added on the cycle the device finally answers.
    """
    prime_device(
        aioclient_mock,
        {GET_STATE: {"json": state_without("hasDspSetting", "hasEQSetting")}},
    )
    entry = await _setup(hass)

    assert entry.runtime_data.last_update_success is True
    assert entry.runtime_data.capabilities_settled is False
    assert not hass.states.async_entity_ids("binary_sensor")

    # The device reports the pair; the gates settle on that cycle, with no
    # reload and no second profile read.
    aioclient_mock.clear_requests()
    prime_device(aioclient_mock)
    await _advance(hass, freezer, 1)

    assert entry.runtime_data.capabilities_settled is True
    assert entry.runtime_data.data.capabilities.has_dsp is True
    assert hass.states.async_entity_ids("binary_sensor")
    assert calls_to(aioclient_mock, GET_MODEL) == 0


async def test_waiting_on_the_dsp_gate_costs_no_other_entity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only the undecided gate waits; everything already known is published.

    Holding the whole capability set back for company would take every
    unrelated entity down with it for the length of the wait, on every restart
    — and ``media_player`` reads ``has_cd`` at runtime, so the synthetic CD
    source would vanish from ``source_list`` too. That trades a slow entity for
    a broken integration.
    """
    prime_device(
        aioclient_mock,
        {GET_STATE: {"json": state_without("hasDspSetting", "hasEQSetting")}},
    )
    entry = await _setup(hass)

    capabilities = entry.runtime_data.data.capabilities
    assert capabilities is not None
    assert capabilities.has_cd is True
    assert capabilities.has_screen_power is True
    assert _select_states(hass)
    assert hass.states.async_entity_ids("switch")


async def test_one_flag_alone_is_not_enough_to_settle_the_pair(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Both flags share a block, so half of it is still an incomplete answer.

    Settling ``has_dsp`` off a payload that only carried ``hasEQSetting`` would
    be the same mistake one field along.
    """
    prime_device(aioclient_mock, {GET_STATE: {"json": state_without("hasDspSetting")}})
    entry = await _setup(hass)

    assert entry.runtime_data.capabilities_settled is False


async def test_flags_answered_one_cycle_apart_are_not_forgotten(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """An answer given during the wait counts, even if never repeated.

    A device that reports the pair one field at a time — never both together —
    answers the question in full, just not in one payload. Settling from
    whichever cycle happens to land on the bound would throw that away and
    latch ``has_dsp`` off a payload that merely did not mention it, which is
    the original defect wearing the fix as a disguise.
    """
    prime_device(
        aioclient_mock,
        # DSP answered here, EQ omitted...
        {GET_STATE: {"json": state_without("hasEQSetting")}},
    )
    entry = await _setup(hass)
    assert entry.runtime_data.capabilities_settled is False

    # ...and every cycle from here on mentions neither, right through the bound.
    aioclient_mock.clear_requests()
    prime_device(
        aioclient_mock,
        {GET_STATE: {"json": state_without("hasDspSetting", "hasEQSetting")}},
    )
    await _advance(hass, freezer, PROCESSING_GATE_CYCLES)

    assert entry.runtime_data.capabilities_settled is True
    assert entry.runtime_data.data.capabilities.has_dsp is True
    assert hass.states.async_entity_ids("binary_sensor")


async def test_a_unit_that_never_reports_the_flags_settles_after_the_bound(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Waiting is bounded: a silence that never ends is taken as "neither".

    Unbounded retry is not the safer default it looks like. The gates are
    latched once so entities do not appear and vanish under a running Home
    Assistant, so a device that never answers would otherwise sit undecided
    forever, with the platform still subscribed and still hoping.
    """
    prime_device(
        aioclient_mock,
        {GET_STATE: {"json": state_without("hasDspSetting", "hasEQSetting")}},
    )
    entry = await _setup(hass)

    await _advance(hass, freezer, PROCESSING_GATE_CYCLES - 1)

    assert entry.runtime_data.capabilities_settled is True
    capabilities = entry.runtime_data.data.capabilities
    assert capabilities.has_dsp is False
    assert capabilities.has_eq is False
    assert not hass.states.async_entity_ids("binary_sensor")
    # The bound cost this unit its DSP sensor, not its entity set.
    assert capabilities.has_cd is True
    assert _select_states(hass)


async def test_identity_lands_while_the_gates_are_still_waiting(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Who the device is and what it can do are latched separately.

    Identity is stable and should be read once; the two ``getState`` gates may
    arrive late. Postponing both together would trade a missing entity for a
    missing device, which is worse.
    """
    prime_device(
        aioclient_mock,
        {GET_STATE: {"json": state_without("hasDspSetting", "hasEQSetting")}},
    )
    entry = await _setup(hass)

    assert entry.runtime_data.capabilities_settled is False
    assert entry.runtime_data.data.device.model == "DMP-A8 Gen 2"
    device = dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)})
    assert device.model == "DMP-A8 Gen 2"
    assert device.sw_version == "v1.1.50"


async def test_the_power_menu_is_read_once_not_polled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """It says what the unit accepts, and nothing about what it is doing.

    An earlier design polled it every settings cycle to guess at screen state
    from the wording of a menu label. Nothing reads it that way now, so it
    belongs with the setup-time profile — one fewer request in every 30 s
    cycle.
    """
    prime_device(aioclient_mock)
    await _setup(hass)

    assert calls_to(aioclient_mock, GET_POWER_OPTION) == 1

    await _advance(hass, freezer, SETTINGS_REFRESH_CYCLES * 2)

    assert calls_to(aioclient_mock, GET_POWER_OPTION) == 1
    assert calls_to(aioclient_mock, GET_SCREEN_BRIGHTNESS) > 1
