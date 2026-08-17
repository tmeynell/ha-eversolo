"""Select tests: the device's option lists, and where a choice is written.

The recurring hazard here is the URL a write lands on. Setter names do not
follow their getters (``getXlrOutputPcmFilterList`` is written by
``setPcmFilter``), and output routing numbers its sockets over the raw list
including the disabled ones — both are silent wrong writes if got wrong, so
they are asserted on the query the seam actually received.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.eversolo.const import (
    SETTINGS_REFRESH_CYCLES,
    SETTING_TAG_ANALOG_PANEL,
    SETTING_TAG_MASTER_CLOCK,
    SETTING_TAG_SPECTRUM_MODE,
    SETTING_TAG_VU_MODE,
)

from .helpers import (
    CHANGE_VISUALIZATION,
    GET_DAC_FILTER,
    GET_INPUT_OUTPUT,
    GET_MASTER_CLOCK,
    GET_STATE,
    GET_SYSTEM_SETTINGS,
    SET_DAC_FILTER,
    SET_MASTER_CLOCK,
    SET_OUTPUT,
    SET_UPSAMPLING,
    SET_VU_MODE,
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

SELECT_DOMAIN = "select"


async def _selects(hass: HomeAssistant, aioclient_mock, overrides=None) -> None:
    """Set the integration up against a device primed with the captures."""
    prime_device(aioclient_mock, overrides)
    await setup_integration(hass)


def _outputs_with(**edits: Any) -> dict:
    """Return the captured socket list with named sockets disabled.

    ``_outputs_with(**{"BAL-XLR": False})`` is the shape that catches
    re-numbering: everything after the disabled socket keeps its own index.
    """
    payload = fixture_json("getinputandoutputlist.json")
    for output in payload["outputData"]:
        if output["name"] in edits:
            output["enable"] = edits[output["name"]]
    return payload


def _selects_matching(hass: HomeAssistant, key: str) -> list[str]:
    """Every select entity whose id carries a key — empty when it was gated off."""
    return [
        entity_id
        for entity_id in hass.states.async_entity_ids(SELECT_DOMAIN)
        if key in entity_id
    ]


async def _choose(hass: HomeAssistant, entity_id: str, option: str) -> None:
    """Pick an option the way the frontend would."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: option},
        blocking=True,
    )


async def test_dac_filter_lists_the_device_s_own_options(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The six reconstruction filters, with the one the DAC is using."""
    await _selects(hass, aioclient_mock)
    state = hass.states.get(entity_id_for(hass, "_dac_filter"))

    assert state.attributes["options"] == [
        "Sharp Roll-off",
        "Slow Roll-off",
        "Short Delay Sharp Roll-off",
        "Short Delay Slow Roll-off",
        "Super Slow Roll-off",
        "Low Dispersion Short Delay",
    ]
    # currentIndex 2 in the capture.
    assert state.state == "Short Delay Sharp Roll-off"


async def test_dac_filter_writes_the_setter_the_device_named(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``getXlrOutputPcmFilterList`` is written by ``setPcmFilter``.

    Nothing about the getter's name says so, which is why the setter is read
    out of the response rather than built here.
    """
    await _selects(hass, aioclient_mock)

    await _choose(hass, entity_id_for(hass, "_dac_filter"), "Slow Roll-off")

    assert query_of(aioclient_mock, SET_DAC_FILTER) == {"index": "1"}


async def test_upsampling_writes_the_setter_with_the_lower_case_s(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``getXlrOutputUpSamplingList`` is written by ``setXlrOutputUpsampling``."""
    await _selects(hass, aioclient_mock)
    entity_id = entity_id_for(hass, "_upsampling")
    assert hass.states.get(entity_id).state == "Off (Original)"

    await _choose(hass, entity_id, "4x (High Resolution)")

    assert query_of(aioclient_mock, SET_UPSAMPLING) == {"index": "2"}


async def test_master_clock_offers_the_three_clocks(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Internal OCXO plus the two external clock rates."""
    await _selects(hass, aioclient_mock)
    entity_id = entity_id_for(hass, "_master_clock")

    assert hass.states.get(entity_id).attributes["options"] == [
        "OCXO 10M",
        "50Ω 10M",
        "50Ω 25M",
    ]
    assert hass.states.get(entity_id).state == "OCXO 10M"

    await _choose(hass, entity_id, "50Ω 25M")

    assert query_of(aioclient_mock, SET_MASTER_CLOCK) == {"index": "2"}


async def test_output_routing_offers_the_sockets_that_are_connected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The USB DAC socket has nothing plugged into it, so it is not offered."""
    await _selects(hass, aioclient_mock)
    state = hass.states.get(entity_id_for(hass, "_output_routing"))

    assert state.attributes["options"] == [
        "BAL-XLR",
        "Analog-RCA",
        "XLR/RCA",
        "IIS",
        "OPT/COAX/AES",
    ]
    # outputIndex 1 in the capture.
    assert state.state == "Analog-RCA"


async def test_output_routing_writes_tag_and_index(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Routing is a ``/ZidooMusicControl/v2/`` call, not a settings list."""
    await _selects(hass, aioclient_mock)

    await _choose(hass, entity_id_for(hass, "_output_routing"), "BAL-XLR")

    assert query_of(aioclient_mock, SET_OUTPUT) == {"tag": "XLR", "index": "0"}


async def test_a_disabled_socket_does_not_shift_the_ones_after_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The device numbers sockets over the raw list, disabled ones included.

    Re-numbering over the offered sockets alone — which the inherited helper
    did — would send index 1 here and route audio to the wrong output.
    """
    await _selects(
        hass,
        aioclient_mock,
        {GET_INPUT_OUTPUT: {"json": _outputs_with(**{"BAL-XLR": False})}},
    )
    entity_id = entity_id_for(hass, "_output_routing")
    assert hass.states.get(entity_id).attributes["options"][0] == "Analog-RCA"

    await _choose(hass, entity_id, "XLR/RCA")

    assert query_of(aioclient_mock, SET_OUTPUT) == {"tag": "XLRRCA", "index": "2"}


async def test_a_choice_is_shown_at_once_and_then_confirmed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The lists are slow-tier, so a write must not wait 30 s to show."""
    device = {"index": 2}

    def _accept(query: dict[str, str]) -> None:
        device["index"] = int(query["index"])

    def _list() -> dict:
        payload = fixture_json("getxlroutputpcmfilterlist.json")
        payload["currentIndex"] = device["index"]
        return payload

    prime_device(
        aioclient_mock,
        {
            GET_DAC_FILTER: answers_with(_list),
            SET_DAC_FILTER: records_writes([], _accept),
        },
    )
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_dac_filter")

    coordinator = entity_object(hass, entity_id).coordinator
    confirming = coordinator.async_refresh_settings
    shown: list[str] = []

    async def _watch() -> None:
        shown.append(hass.states.get(entity_id).state)
        await confirming()

    with patch.object(coordinator, "async_refresh_settings", _watch):
        await _choose(hass, entity_id, "Super Slow Roll-off")

    # Shown while the confirming read was still in flight, and still shown
    # afterwards because the device really did move.
    assert shown == ["Super Slow Roll-off"]
    assert hass.states.get(entity_id).state == "Super Slow Roll-off"


async def test_a_choice_the_device_ignores_snaps_back(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The guess is a courtesy; the device's own report is the truth."""
    await _selects(hass, aioclient_mock)
    entity_id = entity_id_for(hass, "_master_clock")

    # The seam keeps answering currentIndex 0 whatever is written to it.
    await _choose(hass, entity_id, "50Ω 10M")

    assert hass.states.get(entity_id).state == "OCXO 10M"


async def test_a_choice_made_on_the_device_shows_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The lists are polled, so a change made on the unit itself lands."""
    await _selects(hass, aioclient_mock)
    entity_id = entity_id_for(hass, "_master_clock")
    assert hass.states.get(entity_id).state == "OCXO 10M"

    moved = fixture_json("getmasterclocklist.json")
    moved["currentIndex"] = 1
    aioclient_mock.clear_requests()
    prime_device(aioclient_mock, {GET_MASTER_CLOCK: {"json": moved}})
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert hass.states.get(entity_id).state == "50Ω 10M"


async def test_they_are_config_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """These configure the device rather than reporting on it."""
    await _selects(hass, aioclient_mock)
    registry = er.async_get(hass)

    for key in ("_output_routing", "_dac_filter", "_upsampling", "_master_clock"):
        entry = registry.async_get(entity_id_for(hass, key))
        assert entry.entity_category is EntityCategory.CONFIG


async def test_a_unit_without_the_analog_panel_gets_neither_of_its_selects(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """DAC filter and upsampling both live on that one shared page."""
    await _selects(
        hass,
        aioclient_mock,
        {GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_ANALOG_PANEL)}},
    )

    assert not _selects_matching(hass, "dac_filter")
    assert not _selects_matching(hass, "upsampling")


async def test_a_unit_without_a_master_clock_never_gets_the_select(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No clock input, no clock control."""
    await _selects(
        hass,
        aioclient_mock,
        {GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_MASTER_CLOCK)}},
    )

    assert not _selects_matching(hass, "master_clock")


async def test_a_unit_that_lists_no_outputs_never_gets_the_routing_select(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Routing is gated on the socket list, which is not in the settings tree."""
    payload = fixture_json("getinputandoutputlist.json")
    del payload["outputData"]
    await _selects(hass, aioclient_mock, {GET_INPUT_OUTPUT: {"json": payload}})

    assert not _selects_matching(hass, "output_routing")


async def test_a_device_that_was_off_at_setup_still_gets_its_selects(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Restarting while the unit is off must not cost gated entities a reload."""
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})
    await setup_integration(hass)

    assert not hass.states.async_entity_ids(SELECT_DOMAIN)

    aioclient_mock.clear_requests()
    prime_device(aioclient_mock)
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert hass.states.get(entity_id_for(hass, "_master_clock")).state == "OCXO 10M"


async def test_a_list_the_device_stopped_answering_keeps_its_last_value(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A settings endpoint going quiet is a nuisance, not an outage."""
    await _selects(hass, aioclient_mock)
    entity_id = entity_id_for(hass, "_dac_filter")

    aioclient_mock.clear_requests()
    prime_device(
        aioclient_mock, {GET_DAC_FILTER: {"exc": aiohttp.ClientError("no answer")}}
    )
    reads = calls_to(aioclient_mock, GET_DAC_FILTER)
    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES)

    assert calls_to(aioclient_mock, GET_DAC_FILTER) > reads
    assert hass.states.get(entity_id).state == "Short Delay Sharp Roll-off"


def _screen_showing(vu: int, spectrum: int) -> dict:
    """Return the captured getState with the front screen's two flags set."""
    state = fixture_json("getstate_cd.json")
    state["vuDisplayMode"] = vu
    state["spDisplayMode"] = spectrum
    return state


def _visualization_device(vu: int = 0, spectrum: int = 0) -> dict:
    """Mocker overrides for a fake screen that answers writes with its own state.

    ``changVUDisplay`` steps the flag for whichever visualization was asked
    for and drops the other, which is what the real unit does, and reports the
    pair back — so a test exercises the write path the entity actually reads.
    """
    screen = {"vu": vu, "spectrum": spectrum}

    async def _toggle(method, url, data):
        asked = "spectrum" if url.query.get("openType") == "1" else "vu"
        other = "vu" if asked == "spectrum" else "spectrum"
        screen[asked] = -1 if screen[asked] >= 1 else screen[asked] + 1
        screen[other] = 0
        return AiohttpClientMockResponse(
            method,
            url,
            json={
                "status": 200,
                "vuDisplayMode": screen["vu"],
                "spDisplayMode": screen["spectrum"],
            },
        )

    return {
        CHANGE_VISUALIZATION: {"side_effect": _toggle},
        GET_STATE: answers_with(
            lambda: _screen_showing(screen["vu"], screen["spectrum"])
        ),
    }


async def test_the_visualization_select_offers_the_three_screen_modes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """One control, rather than the two blind "Cycle" buttons of an earlier design."""
    await _selects(hass, aioclient_mock)
    state = hass.states.get(entity_id_for(hass, "_visualization"))

    assert state.attributes["options"] == ["off", "vu_meter", "spectrum"]
    # Both flags are 0 in the capture, which is the "neither showing" reading.
    assert state.state == "off"


async def test_the_visualization_reads_whichever_side_the_screen_is_showing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The flag pair rides in getState, so the live tier carries it."""
    await _selects(hass, aioclient_mock, {GET_STATE: {"json": _screen_showing(0, 1)}})

    assert hass.states.get(entity_id_for(hass, "_visualization")).state == "spectrum"


async def test_choosing_a_visualization_toggles_the_side_it_belongs_to(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``openType=0`` is the VU meter, ``openType=1`` the spectrum."""
    await _selects(hass, aioclient_mock, _visualization_device())
    entity_id = entity_id_for(hass, "_visualization")

    await _choose(hass, entity_id, "spectrum")
    assert query_of(aioclient_mock, CHANGE_VISUALIZATION) == {"openType": "1"}
    assert hass.states.get(entity_id).state == "spectrum"

    await _choose(hass, entity_id, "vu_meter")
    assert query_of(aioclient_mock, CHANGE_VISUALIZATION) == {"openType": "0"}
    assert hass.states.get(entity_id).state == "vu_meter"


async def test_switching_a_visualization_off_uses_the_side_that_is_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """There is no "switch it off" call: Off toggles whichever one is up."""
    await _selects(hass, aioclient_mock, _visualization_device(spectrum=1))
    entity_id = entity_id_for(hass, "_visualization")
    assert hass.states.get(entity_id).state == "spectrum"

    await _choose(hass, entity_id, "off")

    assert query_of(aioclient_mock, CHANGE_VISUALIZATION) == {"openType": "1"}
    assert hass.states.get(entity_id).state == "off"


async def test_choosing_what_is_already_showing_writes_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The endpoint only toggles, so a redundant write would switch it off."""
    await _selects(hass, aioclient_mock, _visualization_device(vu=1))
    entity_id = entity_id_for(hass, "_visualization")
    assert hass.states.get(entity_id).state == "vu_meter"

    await _choose(hass, entity_id, "vu_meter")

    assert calls_to(aioclient_mock, CHANGE_VISUALIZATION) == 0
    assert hass.states.get(entity_id).state == "vu_meter"


async def test_a_screen_that_needs_a_second_step_gets_one(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """From ``-1`` the first toggle only reaches ``0``, which is still not showing.

    The device's own reply is what says so, so the select steps again rather
    than reporting a visualization the screen is not showing. It is bounded:
    a device that never reaches the choice is left reporting the truth.
    """
    await _selects(hass, aioclient_mock, _visualization_device(vu=-1, spectrum=-1))
    entity_id = entity_id_for(hass, "_visualization")
    assert hass.states.get(entity_id).state == "off"

    await _choose(hass, entity_id, "vu_meter")

    assert calls_to(aioclient_mock, CHANGE_VISUALIZATION) == 2
    assert hass.states.get(entity_id).state == "vu_meter"


async def test_a_screen_that_never_gets_there_stops_and_tells_the_truth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A unit that ignores the toggle must not be written to indefinitely."""
    await _selects(
        hass,
        aioclient_mock,
        {
            CHANGE_VISUALIZATION: {
                "json": {"status": 200, "vuDisplayMode": 0, "spDisplayMode": 0}
            }
        },
    )
    entity_id = entity_id_for(hass, "_visualization")

    await _choose(hass, entity_id, "vu_meter")

    assert calls_to(aioclient_mock, CHANGE_VISUALIZATION) == 3
    assert hass.states.get(entity_id).state == "off"


async def test_a_unit_whose_screen_shows_neither_never_gets_the_select(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No VU list and no spectrum list means nothing to visualize."""
    tree = settings_without(SETTING_TAG_VU_MODE, SETTING_TAG_SPECTRUM_MODE)
    await _selects(hass, aioclient_mock, {GET_SYSTEM_SETTINGS: {"json": tree}})

    assert not _selects_matching(hass, "visualization")
    assert not _selects_matching(hass, "vu_style")
    assert not _selects_matching(hass, "spectrum_style")


async def test_the_vu_style_select_populates_from_the_device_s_own_list(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Twelve meter faces, written to the setter the list named for itself."""
    await _selects(hass, aioclient_mock)
    entity_id = entity_id_for(hass, "_vu_style")
    state = hass.states.get(entity_id)

    assert len(state.attributes["options"]) == 12
    # currentIndex 10 in the capture.
    assert state.state == "VU meter 11"

    await _choose(hass, entity_id, "VU meter 1")

    assert query_of(aioclient_mock, SET_VU_MODE) == {"index": "0"}


async def test_a_screen_with_only_a_vu_list_is_not_offered_the_spectrum(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Offering it would fire the step loop at hardware that cannot obey it."""
    await _selects(
        hass,
        aioclient_mock,
        {GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_SPECTRUM_MODE)}},
    )
    entity_id = entity_id_for(hass, "_visualization")

    assert hass.states.get(entity_id).attributes["options"] == ["off", "vu_meter"]

    with pytest.raises(ServiceValidationError):
        await _choose(hass, entity_id, "spectrum")

    assert calls_to(aioclient_mock, CHANGE_VISUALIZATION) == 0


async def test_switching_off_a_screen_that_reports_nothing_writes_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Off toggles whatever is up, so not knowing means not guessing.

    A blind toggle here would switch the VU meter *on* in answer to a request
    to switch the screen off.
    """
    state = fixture_json("getstate_cd.json")
    del state["vuDisplayMode"]
    del state["spDisplayMode"]
    await _selects(hass, aioclient_mock, {GET_STATE: {"json": state}})
    entity_id = entity_id_for(hass, "_visualization")
    assert hass.states.get(entity_id).state == STATE_UNKNOWN

    await _choose(hass, entity_id, "off")

    assert calls_to(aioclient_mock, CHANGE_VISUALIZATION) == 0
    assert hass.states.get(entity_id).state == STATE_UNKNOWN
