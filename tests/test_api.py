"""Client tests through the single mocked HTTP seam (aioclient_mock)."""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.api import (
    EversoloApiClient,
    EversoloApiClientCommunicationError,
)
from custom_components.eversolo.data import (
    EversoloProcessing,
    EversoloVisualizationMode,
)

from .helpers import BASE_URL, HOST, PORT, fixture_json


def _client(hass: HomeAssistant) -> EversoloApiClient:
    return EversoloApiClient(HOST, PORT, async_get_clientsession(hass))


async def test_seam_getmodel_typed_parse(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Priming the mock with real getModel yields a typed device (seam works)."""
    aioclient_mock.get(
        f"{BASE_URL}/ControlCenter/getModel", json=fixture_json("getmodel.json")
    )

    device = await _client(hass).async_read_device()

    assert device.model == "DMP-A8 Gen 2"
    assert device.net_mac == "aa:bb:cc:00:00:01"
    assert device.firmware == "v1.1.50"


async def test_seam_getstate_typed_parse(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """GetState parses into a typed EversoloData through the transport."""
    aioclient_mock.get(
        f"{BASE_URL}/ZidooMusicControl/v2/getState",
        json=fixture_json("getstate_cd.json"),
    )

    data = await _client(hass).async_read_state()

    assert data.playback.is_cd is True
    assert data.playback.title == "Rabbit in Your Headlights"
    assert data.volume.current == 127
    assert data.device.model == "DMP-A8 Gen 2"


async def test_cover_url_carries_the_params_getimage_requires(
    hass: HomeAssistant,
) -> None:
    """#22 F4: ``id``+``target`` alone 806s; ``musicType``+``type=4`` fixes it.

    Recovered from the control app's own ``MusicImageLoader.music()`` and
    live-verified against the A8 (see ``api.py``'s docstring for both).
    """
    url = _client(hass).create_image_url_by_song_id(128670077, 4)

    assert url == (
        f"{BASE_URL}/ZidooMusicControl/v2/getImage"
        "?id=128670077&musicType=4&type=4&target=16"
    )


async def test_cover_url_defaults_a_missing_music_type_to_zero(
    hass: HomeAssistant,
) -> None:
    """A song whose ``playingMusic.type`` was never reported still gets a URL."""
    url = _client(hass).create_image_url_by_song_id(123, None)

    assert "musicType=0" in url


async def test_seam_profile_from_real_captures(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """One profile read yields identity and capabilities from five endpoints.

    Plus the DSP/EQ slice of the caller's own ``getState``, which is the only
    place those two gates are reported.
    """
    aioclient_mock.get(
        f"{BASE_URL}/SystemSettings/getSystemSettings",
        json=fixture_json("getsystemsettings.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/ControlCenter/getModel", json=fixture_json("getmodel.json")
    )
    aioclient_mock.get(
        f"{BASE_URL}/SystemSettings/displaySettings/getKnobSettingOption",
        json=fixture_json("getknobsettingoption.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/ZidooMusicControl/v2/getInputAndOutputList",
        json=fixture_json("getinputandoutputlist.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/ZidooMusicControl/v2/getPowerOption",
        json=fixture_json("getpoweroption.json"),
    )

    profile = await _client(hass).async_read_profile(
        EversoloProcessing.from_state(fixture_json("getstate_cd.json"))
    )

    assert profile.device.model == "DMP-A8 Gen 2"
    assert profile.capabilities.has_cd is True
    assert profile.capabilities.has_analog_panel is True
    assert profile.capabilities.has_knob is False
    assert profile.capabilities.has_knob_color is False
    assert profile.capabilities.has_dsp is True
    assert profile.capabilities.has_eq is False
    # Routing is gated on the socket list, which is not in the settings tree.
    assert profile.capabilities.has_output_routing is True
    # The power menu offers a ``screen`` tag, so the unit has a screen.
    assert profile.capabilities.has_screen_power is True


async def test_a_written_value_is_appended_to_the_url_the_device_gave(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The setter arrives complete up to its trailing ``?index=``."""
    setter = "/SystemSettings/audioSettings/xlrOutputOption/setPcmFilter?index="
    aioclient_mock.get(f"{BASE_URL}{setter}", text='{"status":200}')

    await _client(hass).async_write_setting(setter, 4)

    _, url, *_ = aioclient_mock.mock_calls[-1]
    assert url.path.endswith("/setPcmFilter")
    assert dict(url.query) == {"index": "4"}


async def test_the_screen_toggle_is_a_power_option_not_a_key_press(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Blanking the screen is ``setPowerOption`` with the tag the menu listed.

    An earlier design sent ``sendkey?key=Key.Screen.ON``; the app never does
    that.
    """
    path = "/ZidooMusicControl/v2/setPowerOption"
    aioclient_mock.get(f"{BASE_URL}{path}", text='{"status":200}')

    await _client(hass).async_toggle_screen()

    _, url, *_ = aioclient_mock.mock_calls[-1]
    assert url.path == path
    assert dict(url.query) == {"tag": "screen"}


async def test_changing_the_visualization_reports_what_it_did(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``changVUDisplay`` answers with both flags, whichever one was toggled.

    That reply is the only prompt reading of the front screen there is —
    ``getState`` carries the same pair, but only on the next poll.
    """
    path = "/ZidooMusicControl/v2/changVUDisplay"
    aioclient_mock.get(
        f"{BASE_URL}{path}",
        text='{"status":200,"vuDisplayMode":0,"spDisplayMode":1}',
    )

    visualization = await _client(hass).async_change_visualization(1)

    _, url, *_ = aioclient_mock.mock_calls[-1]
    assert url.path == path
    assert dict(url.query) == {"openType": "1"}
    assert visualization.mode is EversoloVisualizationMode.SPECTRUM


async def test_knob_colour_keeps_its_own_fixed_setter(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No knob-bearing unit has been captured, so this path is not list-driven.

    Only the A6 has a knob, so nothing here can show whether
    ``getKnobLightColorList`` carries a ``url``. Until one does, the write goes
    to a fixed endpoint instead.
    """
    path = "/SystemSettings/displaySettings/setKnobLightColor"
    aioclient_mock.get(f"{BASE_URL}{path}", text='{"status":200}')

    await _client(hass).async_set_knob_color(2)

    _, url, *_ = aioclient_mock.mock_calls[-1]
    assert url.path == path
    assert dict(url.query) == {"index": "2"}


async def test_seam_communication_error_is_typed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A transport failure raises the typed comms error, not a bare exception."""
    aioclient_mock.get(
        f"{BASE_URL}/ControlCenter/getModel", exc=aiohttp.ClientError("boom")
    )

    with pytest.raises(EversoloApiClientCommunicationError):
        await _client(hass).async_read_device()


async def test_seam_malformed_json_is_comms_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A truncated/HTML body is a comms fault, not an unhandled exception."""
    aioclient_mock.get(
        f"{BASE_URL}/ControlCenter/getModel", text="<html>502 Bad Gateway</html>"
    )

    with pytest.raises(EversoloApiClientCommunicationError):
        await _client(hass).async_read_device()


async def test_a_missing_power_menu_does_not_cost_the_profile(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The power menu gates one switch; the other four reads gate everything.

    A firmware that does not serve it must still yield capabilities, or the
    device would get no gated entity of any platform, ever.
    """
    for path, fixture in (
        ("/SystemSettings/getSystemSettings", "getsystemsettings.json"),
        ("/ControlCenter/getModel", "getmodel.json"),
        (
            "/SystemSettings/displaySettings/getKnobSettingOption",
            "getknobsettingoption.json",
        ),
        ("/ZidooMusicControl/v2/getInputAndOutputList", "getinputandoutputlist.json"),
    ):
        aioclient_mock.get(f"{BASE_URL}{path}", json=fixture_json(fixture))
    aioclient_mock.get(
        f"{BASE_URL}/ZidooMusicControl/v2/getPowerOption",
        exc=aiohttp.ClientError("no such endpoint"),
    )

    profile = await _client(hass).async_read_profile(
        EversoloProcessing.from_state(fixture_json("getstate_cd.json"))
    )

    assert profile.device.model == "DMP-A8 Gen 2"
    assert profile.capabilities.has_cd is True
    # Only the one capability the power menu answers for is lost.
    assert profile.capabilities.has_screen_power is False
