"""Audio Format sensor tests: the diagnostic view of the live stream."""

from __future__ import annotations

import aiohttp
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .helpers import (
    GET_INPUT_OUTPUT,
    GET_STATE,
    entity_id_for,
    fixture_json,
    prime_device,
    setup_integration,
)


async def test_audio_format_renders_the_stream_quality(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The capture's FLAC stream reads as one line, with the parts as attributes."""
    prime_device(
        aioclient_mock, {GET_STATE: {"json": fixture_json("getstate_streaming.json")}}
    )
    await setup_integration(hass)

    state = hass.states.get(entity_id_for(hass, "_audio_format"))
    assert state.state == "FLAC 44.1kHz/16bit"
    assert state.attributes["codec"] == "FLAC"
    assert state.attributes["sample_rate"] == 44100
    assert state.attributes["bit_depth"] == 16
    assert state.attributes["bitrate"] == "1.41 Mbps"
    assert state.attributes["channels"] == 2


async def test_audio_format_does_not_report_a_stale_discs_numbers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """#22 F1: a disc left in the tray must not stand in for the live format.

    This is exactly what the live acceptance run saw: HA read
    ``PCM 44.1kHz/16bit`` off a disc sitting unplayed in the tray while the
    DAC was actually converting 48 kHz on the TV/eARC input. Hand-built with
    a distinct output rate, since the derived ``getstate_earc.json`` fixture
    coincidentally shares the disc's 44100 Hz for its output block (see
    ``fixtures/README.md``) and so cannot prove which one the sensor read.
    """
    on_the_tv = fixture_json("getinputandoutputlist.json")
    on_the_tv["inputIndex"] = 3
    state = fixture_json("getstate_spotify_disc_loaded.json")
    state["volumeData"]["intputTag"] = "EARC-EARC"
    state["everSoloPlayInfo"]["everSoloPlayAudioInfo"] = {}
    state["everSoloPlayInfo"]["everSoloPlayOutputInfo"]["outPutSampleRate"] = 48000
    prime_device(
        aioclient_mock,
        {
            GET_STATE: {"json": state},
            GET_INPUT_OUTPUT: {"json": on_the_tv},
        },
    )
    await setup_integration(hass)

    attributes = hass.states.get(entity_id_for(hass, "_audio_format")).attributes
    assert attributes["sample_rate"] == 48000
    assert attributes["bitrate"] is None
    assert attributes["bit_depth"] is None
    assert attributes["channels"] is None


async def test_audio_format_is_a_diagnostic_entity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """It is device detail, not a control, so it sits under diagnostics."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    entity_id = entity_id_for(hass, "_audio_format")
    entry = er.async_get(hass).async_get(entity_id)
    assert entry.entity_category is EntityCategory.DIAGNOSTIC


async def test_audio_format_is_unknown_with_nothing_playing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No stream means no format — the sensor says so rather than inventing one."""
    silent = fixture_json("getstate_streaming.json")
    silent["playingMusic"] = {}
    silent["everSoloPlayInfo"] |= {
        "everSoloPlayAudioInfo": {},
        "everSoloPlayOutputInfo": {},
    }
    prime_device(aioclient_mock, {GET_STATE: {"json": silent}})
    await setup_integration(hass)

    assert hass.states.get(entity_id_for(hass, "_audio_format")).state == STATE_UNKNOWN


async def test_audio_format_goes_unavailable_when_the_device_does(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """It reads the live tier, so it follows the player down."""
    prime_device(aioclient_mock)
    entry = await setup_integration(hass)
    entity_id = entity_id_for(hass, "_audio_format")
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
