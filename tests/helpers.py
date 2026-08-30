"""Shared test helpers: load the real captured device fixtures."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_component import DATA_INSTANCES
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.eversolo.const import DOMAIN, LIVE_UPDATE_INTERVAL

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# The wired MAC of the captured device, which is what an entry is anchored to.
UNIQUE_ID = format_mac("aa:bb:cc:00:00:01")

# Base URL of the fake device the mocked HTTP seam answers for.
HOST = "192.168.0.60"
PORT = 9529
BASE_URL = f"http://{HOST}:{PORT}"

# Paths worth naming, because tests count the calls made to them.
GET_STATE = "/ZidooMusicControl/v2/getState"
GET_MODEL = "/ControlCenter/getModel"
GET_SYSTEM_SETTINGS = "/SystemSettings/getSystemSettings"
GET_SCREEN_BRIGHTNESS = "/SystemSettings/displaySettings/getScreenBrightness"
GET_INPUT_OUTPUT = "/ZidooMusicControl/v2/getInputAndOutputList"
GET_DAC_FILTER = (
    "/SystemSettings/audioSettings/xlrOutputOption/getXlrOutputPcmFilterList"
)
GET_UPSAMPLING = (
    "/SystemSettings/audioSettings/xlrOutputOption/getXlrOutputUpSamplingList"
)
GET_MASTER_CLOCK = "/SystemSettings/audioSettings/getMasterClockList"
GET_SUB_OUTPUT = "/SystemSettings/audioSettings/getSubOutputOption"
GET_POWER_OPTION = "/ZidooMusicControl/v2/getPowerOption"
GET_KNOB_BRIGHTNESS = "/SystemSettings/displaySettings/getKnobBrightness"
# Not polled — read on demand by the screensaver keep-alive (coordinator.py),
# so it is deliberately absent from ``DEVICE_ENDPOINTS`` below; a test that
# needs it registers it itself via ``prime_device``'s overrides.
GET_SCREENSAVER_TIME_LIST = "/SystemSettings/displaySettings/getScreensaverTimeList"
# Named rather than inlined below, because a test overriding it has to spell
# the same path: ``prime_device`` registers an unrecognised override path as a
# *new* endpoint rather than replacing one, so a near-miss would silently leave
# the captured payload answering instead of the override.
GET_KNOB_OPTION = "/SystemSettings/displaySettings/getKnobSettingOption"
# Read on demand by ``async_select_source(CD)``, not polled every cycle — so
# it is deliberately absent from ``DEVICE_ENDPOINTS`` below; a test that needs
# it registers it itself via ``prime_device``'s overrides.
GET_CD_LIST = "/ZidooMusicControl/v2/getCDList"
# Opened on demand by the panel camera (#38), not polled — the mocker matches
# on path alone (see ``calls_to``/``query_of``), so this one constant covers
# both the ``mode=1`` handshake and the ``mode=0`` teardown call.
SETCASTMODE = "/ZidooControlCenter/setcastmode"

# Every endpoint the coordinator polls, with the capture that answers it.
DEVICE_ENDPOINTS: dict[str, str] = {
    GET_STATE: "getstate_spotify_disc_loaded.json",
    GET_MODEL: "getmodel.json",
    GET_SYSTEM_SETTINGS: "getsystemsettings.json",
    GET_SCREEN_BRIGHTNESS: "getscreenbrightness.json",
    GET_KNOB_OPTION: "getknobsettingoption.json",
    "/SystemSettings/displaySettings/getVUModeList": "getvumodelist.json",
    "/SystemSettings/displaySettings/getSpPlayModeList": "getspplaymodelist.json",
    GET_INPUT_OUTPUT: "getinputandoutputlist.json",
    GET_DAC_FILTER: "getxlroutputpcmfilterlist.json",
    GET_UPSAMPLING: "getxlroutputupsamplinglist.json",
    GET_MASTER_CLOCK: "getmasterclocklist.json",
    GET_SUB_OUTPUT: "getsuboutputoption.json",
    GET_POWER_OPTION: "getpoweroption.json",
}

# Fire-and-forget writes. Registering the bare path matches any query string,
# and the client never parses the body, so one canned reply serves them all.
COMMAND_PATHS = (
    "/ZidooMusicControl/v2/playOrPause",
    "/ZidooMusicControl/v2/playNext",
    "/ZidooMusicControl/v2/playLast",
    "/ZidooMusicControl/v2/seekTo",
    "/ZidooMusicControl/v2/setDevicesVolume",
    "/ZidooMusicControl/v2/setMuteVolume",
    "/ZidooMusicControl/v2/setInputList",
    "/ZidooMusicControl/v2/setOutInputList",
    "/ZidooMusicControl/v2/setPowerOption",
    "/ZidooMusicControl/v2/playCDMusic",
    "/ZidooControlCenter/RemoteControl/sendkey",
    "/SystemSettings/playSettings/setCDAutoPlay",
    "/SystemSettings/playSettings/setGallessnewPlay",
    "/SystemSettings/playSettings/setEOSEngine",
    "/SystemSettings/audioSettings/subOutputOption/setSubSwitchEnable",
    "/SystemSettings/audioSettings/setInputMasterClock",
    "/SystemSettings/audioSettings/xlrOutputOption/setPcmFilter",
    "/SystemSettings/audioSettings/xlrOutputOption/setXlrOutputUpsampling",
    "/SystemSettings/displaySettings/setVUMode",
    "/SystemSettings/displaySettings/setSpPlayModeList",
    "/SystemSettings/displaySettings/setScreenBrightness",
    "/SystemSettings/displaySettings/setKnobBrightness",
)

# ``changVUDisplay`` is the one write whose reply the client parses, so it
# cannot share the canned command body.
CHANGE_VISUALIZATION = "/ZidooMusicControl/v2/changVUDisplay"

SET_INPUT = "/ZidooMusicControl/v2/setInputList"
SET_OUTPUT = "/ZidooMusicControl/v2/setOutInputList"
PLAY_CD_MUSIC = "/ZidooMusicControl/v2/playCDMusic"
SET_CD_AUTO_PLAY = "/SystemSettings/playSettings/setCDAutoPlay"
SET_POWER_OPTION = "/ZidooMusicControl/v2/setPowerOption"
SET_GAPLESS = "/SystemSettings/playSettings/setGallessnewPlay"
SET_EOS_ENGINE = "/SystemSettings/playSettings/setEOSEngine"
SET_AUTO_CHANGE_SOURCE = "/SystemSettings/playSettings/setAutoChangeSource"
SET_SUBWOOFER = "/SystemSettings/audioSettings/subOutputOption/setSubSwitchEnable"
SET_MASTER_CLOCK = "/SystemSettings/audioSettings/setInputMasterClock"
SET_DAC_FILTER = "/SystemSettings/audioSettings/xlrOutputOption/setPcmFilter"
SET_UPSAMPLING = "/SystemSettings/audioSettings/xlrOutputOption/setXlrOutputUpsampling"
SET_SCREEN_BRIGHTNESS = "/SystemSettings/displaySettings/setScreenBrightness"
SET_KNOB_BRIGHTNESS = "/SystemSettings/displaySettings/setKnobBrightness"
SET_VU_MODE = "/SystemSettings/displaySettings/setVUMode"
SET_SCREENSAVER_TIME = "/SystemSettings/displaySettings/setScreensaverTime"


def fixture_text(name: str) -> str:
    """Return the raw text of a captured fixture."""
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> Any:
    """Return a captured fixture parsed as JSON."""
    return json.loads(fixture_text(name))


def prime_device(
    mocker: AiohttpClientMocker,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Answer every endpoint the coordinator polls with its captured fixture.

    ``overrides`` maps a path to the kwargs handed to the mocker instead, so a
    test can make one endpoint fail (``{"exc": ...}``) or reshape one payload
    (``{"json": ...}``) while the rest of the device stays healthy. A path the
    captures do not cover is registered from its override alone — that is how a
    test stands up hardware nobody here has, such as the A6's knob.
    """
    overrides = overrides or {}
    for path, fixture in DEVICE_ENDPOINTS.items():
        kwargs = overrides.get(path) or {"json": fixture_json(fixture)}
        mocker.get(f"{BASE_URL}{path}", **kwargs)
    for path in COMMAND_PATHS:
        kwargs = overrides.get(path) or {"text": '{"status":200}'}
        mocker.get(f"{BASE_URL}{path}", **kwargs)
    # The visualization write answers with the screen's two flags, so its reply
    # has to be the shape the client parses rather than the canned command body.
    mocker.get(
        f"{BASE_URL}{CHANGE_VISUALIZATION}",
        **(
            overrides.get(CHANGE_VISUALIZATION)
            or {"json": {"status": 200, "vuDisplayMode": 0, "spDisplayMode": 0}}
        ),
    )
    known = set(DEVICE_ENDPOINTS) | set(COMMAND_PATHS) | {CHANGE_VISUALIZATION}
    for path, kwargs in overrides.items():
        if path not in known:
            mocker.get(f"{BASE_URL}{path}", **kwargs)


def answers_with(build: Callable[[], Any]) -> dict[str, Any]:
    """Mocker kwargs that answer with whatever ``build()`` returns right now.

    ``prime_device`` freezes each payload when it registers it, which cannot
    show a write taking effect. This defers the payload to call time, so a test
    can wire a write to change what the next read reports — the round trip the
    optimistic-then-confirm path actually depends on.
    """

    async def _answer(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(method, url, json=build())

    return {"side_effect": _answer}


def records_writes(
    sink: list[dict[str, str]],
    apply: Callable[[dict[str, str]], None] | None = None,
) -> dict[str, Any]:
    """Mocker kwargs for a command that files its query and answers OK.

    ``apply`` lets the write change the fake device's state first, so a later
    read can report it.
    """

    async def _answer(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        query = dict(url.query)
        if apply is not None:
            apply(query)
        sink.append(query)
        return AiohttpClientMockResponse(method, url, text='{"status":200}')

    return {"side_effect": _answer}


def state_with(**flags: Any) -> dict[str, Any]:
    """Return the captured ``getState`` with top-level flags overridden.

    The A8 reports ``hasEQSetting: false`` and had DSP engaged on the selected
    input when every capture was taken, so the off state and the EQ side can
    only be reached by mutating what the device said.
    """
    state = fixture_json("getstate_spotify_disc_loaded.json")
    state.update(flags)
    return state


def state_without(*keys: str) -> dict[str, Any]:
    """Return the captured ``getState`` with top-level fields removed entirely.

    Distinct from ``state_with(field=False)``: a device that omits a flag has
    not said no, it has said nothing, and the two are meant to be handled
    differently. No captured payload omits any of them, so the only way to
    stand up the silent case is to take one away.
    """
    state = fixture_json("getstate_spotify_disc_loaded.json")
    for key in keys:
        state.pop(key, None)
    return state


def settings_without(*tags: str) -> dict[str, Any]:
    """Return the A8's settings tree with those features' entries removed.

    Capability detection reads tag presence, so dropping an entry is how a test
    stands in for a unit that does not have that feature at all. Takes several
    because some capabilities are derived from more than one tag — losing
    either VU or spectrum leaves the visualization select standing, and only
    losing both takes it away.
    """
    tree = fixture_json("getsystemsettings.json")
    for group in tree.get("settings", []):
        group["items"] = [
            item for item in group.get("items", []) if item.get("tag") not in tags
        ]
    return tree


def calls_to(mocker: AiohttpClientMocker, path: str) -> int:
    """How many times the mocked seam has been asked for a given path."""
    return sum(1 for _, url, *_ in mocker.mock_calls if url.path == path)


def query_of(mocker: AiohttpClientMocker, path: str) -> dict[str, str]:
    """Return the query of the last call to a path — what a command carried."""
    queries = [dict(url.query) for _, url, *_ in mocker.mock_calls if url.path == path]
    assert queries, f"nothing was sent to {path}"
    return queries[-1]


async def setup_integration(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Set the integration up against whatever the seam is primed with."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        title="Eversolo DMP-A8 Gen 2",
        data={CONF_HOST: HOST},
        unique_id=UNIQUE_ID,
        options=options or {},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def advance_cycles(hass: HomeAssistant, freezer, cycles: int) -> None:
    """Run the poller forward by whole live cycles.

    Ticks a hair past the interval so a refresh scheduled a few microseconds
    into the future is unambiguously due; the coordinator reschedules from each
    refresh, so this is still exactly one poll per cycle.
    """
    for _ in range(cycles):
        freezer.tick(timedelta(seconds=LIVE_UPDATE_INTERVAL, milliseconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def entity_id_for(hass: HomeAssistant, unique_id_suffix: str) -> str:
    """Return the entity_id HA gave the entity whose unique_id ends so."""
    registry = er.async_get(hass)
    matches = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DOMAIN and entry.unique_id.endswith(unique_id_suffix)
    ]
    assert len(matches) == 1, f"expected one {unique_id_suffix} entity, got {matches}"
    return matches[0]


def entity_object(hass: HomeAssistant, entity_id: str) -> Entity:
    """Return the live entity, to exercise a method HA would not call."""
    component = hass.data[DATA_INSTANCES][entity_id.partition(".")[0]]
    entity = component.get_entity(entity_id)
    assert entity is not None, f"{entity_id} is not set up"
    return entity
