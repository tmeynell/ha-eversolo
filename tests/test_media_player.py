"""Media player tests: now-playing, transport, volume — against real captures."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_SEEK_POSITION,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_SELECT_SOURCE,
    MediaPlayerDeviceClass,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_NEXT_TRACK,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PREVIOUS_TRACK,
    SERVICE_MEDIA_SEEK,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_MUTE,
    SERVICE_VOLUME_SET,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo import wake_on_lan
from custom_components.eversolo.const import (
    CD_SOURCE,
    SETTING_TAG_CD_AUTO_PLAY,
    WAKE_ON_LAN_PORTS,
)

from .helpers import (
    GET_INPUT_OUTPUT,
    GET_MODEL,
    GET_STATE,
    GET_SYSTEM_SETTINGS,
    SET_INPUT,
    SET_POWER_OPTION,
    UNIQUE_ID,
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

PLAY_OR_PAUSE = "/ZidooMusicControl/v2/playOrPause"
PLAY_NEXT = "/ZidooMusicControl/v2/playNext"
PLAY_LAST = "/ZidooMusicControl/v2/playLast"
SEEK_TO = "/ZidooMusicControl/v2/seekTo"
SET_VOLUME = "/ZidooMusicControl/v2/setDevicesVolume"
SET_MUTE = "/ZidooMusicControl/v2/setMuteVolume"

TRANSPORT_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.SEEK
)


def _streaming() -> dict:
    """Override the seam to put the device on a streaming (non-CD) track."""
    return {GET_STATE: {"json": fixture_json("getstate_streaming.json")}}


def _cd() -> dict:
    """Override the seam with a genuine disc actually playing (playType 5)."""
    return {GET_STATE: {"json": fixture_json("getstate_cd.json")}}


async def _player(hass: HomeAssistant, aioclient_mock, overrides=None) -> str:
    """Set the integration up and return the media_player's entity_id."""
    prime_device(aioclient_mock, overrides)
    await setup_integration(hass)
    return entity_id_for(hass, "_media_player")


async def _call(hass: HomeAssistant, service: str, entity_id: str, **data) -> None:
    """Invoke a media_player service the way an automation would."""
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        service,
        {ATTR_ENTITY_ID: entity_id, **data},
        blocking=True,
    )


async def test_now_playing_reflects_the_captured_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The player is a receiver showing the track, art and volume it was given."""
    entity_id = await _player(hass, aioclient_mock, _streaming())

    state = hass.states.get(entity_id)
    assert state.state == MediaPlayerState.PLAYING
    assert state.attributes["device_class"] == MediaPlayerDeviceClass.RECEIVER
    assert state.attributes["media_title"] == "Brother, Do You Know the Road?"
    assert state.attributes["media_artist"] == "Hiss Golden Messenger"
    assert state.attributes["media_album_name"] == "Brother, Do You Know the Road?"
    assert state.attributes["media_duration"] == 369686 / 1000
    assert state.attributes["media_position"] == 159301 / 1000
    assert state.attributes["media_position_updated_at"] is not None
    # Art the device already publishes as an absolute URL is passed straight on.
    assert entity_object(hass, entity_id).media_image_url.startswith(
        "https://i.scdn.co"
    )
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == 127 / 200
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is False
    assert state.attributes[ATTR_SUPPORTED_FEATURES] & TRANSPORT_FEATURES == (
        TRANSPORT_FEATURES
    )


async def test_spotify_is_shown_despite_a_disc_in_the_tray(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """#02, the reported defect: a disc in the tray must not shadow Spotify.

    The default fixture has Spotify Connect audible (``playType`` 6) with a
    disc still sitting in the tray — ``extension == "cd"`` was always a proxy
    for what is playing, and it lied here. ``playType`` is the real rule.
    """
    entity_id = await _player(hass, aioclient_mock)

    state = hass.states.get(entity_id)
    assert state.attributes["media_title"] == "Brother, Do You Know the Road?"
    assert state.attributes["media_artist"] == "Hiss Golden Messenger"
    # Spotify's own cover, already in hand — not the disc's, and not fetched
    # by the disc's song id either.
    assert entity_object(hass, entity_id).media_image_url == (
        "https://i.scdn.co/image/ab67616d0000b2733c02bee9dcd95c0a58e2989e"
    )


async def test_a_genuine_disc_shows_its_own_track(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The CD capture (``playType`` 5) surfaces the disc's own track."""
    entity_id = await _player(hass, aioclient_mock, _cd())

    state = hass.states.get(entity_id)
    assert state.attributes["media_title"] == "Ich bin ein Ausländer"
    assert state.attributes["media_artist"] == "Pop Will Eat Itself"


async def test_play_state_comes_from_playstatus_not_top_level_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Top-level ``state:3`` is stale; ``playStatus`` is what the player trusts."""
    stopped = fixture_json("getstate_streaming.json")
    stopped["state"] = 3
    stopped["everSoloPlayInfo"]["playStatus"] = 0

    entity_id = await _player(hass, aioclient_mock, {GET_STATE: {"json": stopped}})

    assert hass.states.get(entity_id).state == MediaPlayerState.PAUSED


async def test_nothing_loaded_reads_as_idle(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A reachable device with no media is idle, not paused on nothing."""
    empty = fixture_json("getstate_streaming.json")
    empty["playingMusic"] = {}
    empty["duration"] = 0
    empty["everSoloPlayInfo"] |= {
        "playStatus": 0,
        "duration": 0,
        "everSoloPlayAudioInfo": {},
    }

    entity_id = await _player(hass, aioclient_mock, {GET_STATE: {"json": empty}})

    assert hass.states.get(entity_id).state == MediaPlayerState.IDLE


async def test_pause_commands_the_device_and_updates_optimistically(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Pausing issues playOrPause, shows paused at once, then the poll confirms."""
    entity_id = await _player(hass, aioclient_mock, _streaming())
    polls = calls_to(aioclient_mock, GET_STATE)

    await _call(hass, SERVICE_MEDIA_PAUSE, entity_id)

    assert calls_to(aioclient_mock, PLAY_OR_PAUSE) == 1
    assert hass.states.get(entity_id).state == MediaPlayerState.PAUSED
    # Optimism is the point: no extra read was spent confirming it.
    assert calls_to(aioclient_mock, GET_STATE) == polls

    # The device catches up on its own schedule, and its word is final.
    paused = fixture_json("getstate_streaming.json")
    paused["everSoloPlayInfo"]["playStatus"] = 0
    aioclient_mock.clear_requests()
    prime_device(aioclient_mock, {GET_STATE: {"json": paused}})
    await advance_cycles(hass, freezer, 1)

    assert hass.states.get(entity_id).state == MediaPlayerState.PAUSED


async def test_a_poll_overrides_a_wrong_guess(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """If the device ignored the command, the next read says so."""
    entity_id = await _player(hass, aioclient_mock, _streaming())

    await _call(hass, SERVICE_MEDIA_PAUSE, entity_id)
    assert hass.states.get(entity_id).state == MediaPlayerState.PAUSED

    await advance_cycles(hass, freezer, 1)

    assert hass.states.get(entity_id).state == MediaPlayerState.PLAYING


async def test_track_skips_issue_the_expected_commands(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Next and previous map onto playNext/playLast and restart the seek bar."""
    entity_id = await _player(hass, aioclient_mock, _streaming())

    await _call(hass, SERVICE_MEDIA_NEXT_TRACK, entity_id)

    assert calls_to(aioclient_mock, PLAY_NEXT) == 1
    assert hass.states.get(entity_id).attributes["media_position"] == 0

    await _call(hass, SERVICE_MEDIA_PREVIOUS_TRACK, entity_id)

    assert calls_to(aioclient_mock, PLAY_LAST) == 1


async def test_seek_sends_milliseconds_and_moves_the_bar(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Seeking converts to the device's milliseconds and shows the new spot."""
    entity_id = await _player(hass, aioclient_mock, _streaming())
    stamped_at = hass.states.get(entity_id).attributes["media_position_updated_at"]
    freezer.tick(timedelta(seconds=1))

    await _call(hass, SERVICE_MEDIA_SEEK, entity_id, **{ATTR_MEDIA_SEEK_POSITION: 42})

    assert query_of(aioclient_mock, SEEK_TO) == {"time": "42000"}
    state = hass.states.get(entity_id)
    assert state.attributes["media_position"] == 42
    # The frontend extrapolates from the stamp, so the guess has to renew it.
    assert state.attributes["media_position_updated_at"] > stamped_at


async def test_volume_is_scaled_to_the_devices_range(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A half-volume request lands as 100 of the device's 0..200 steps."""
    entity_id = await _player(hass, aioclient_mock, _streaming())
    polls = calls_to(aioclient_mock, GET_STATE)

    await _call(hass, SERVICE_VOLUME_SET, entity_id, **{ATTR_MEDIA_VOLUME_LEVEL: 0.5})

    assert query_of(aioclient_mock, SET_VOLUME) == {"volume": "100"}
    assert hass.states.get(entity_id).attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.5
    assert calls_to(aioclient_mock, GET_STATE) == polls


async def test_mute_and_unmute_are_distinct_commands(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Mute and unmute must send opposite commands, not the same one twice."""
    entity_id = await _player(hass, aioclient_mock, _streaming())

    await _call(hass, SERVICE_VOLUME_MUTE, entity_id, **{ATTR_MEDIA_VOLUME_MUTED: True})

    assert query_of(aioclient_mock, SET_MUTE) == {"isMute": "1"}
    assert hass.states.get(entity_id).attributes[ATTR_MEDIA_VOLUME_MUTED] is True

    await _call(
        hass, SERVICE_VOLUME_MUTE, entity_id, **{ATTR_MEDIA_VOLUME_MUTED: False}
    )

    assert query_of(aioclient_mock, SET_MUTE) == {"isMute": "0"}
    assert hass.states.get(entity_id).attributes[ATTR_MEDIA_VOLUME_MUTED] is False


async def test_transport_is_inert_on_the_tv_input(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """On eARC the device drives nothing: controls disappear, calls do nothing."""
    entity_id = await _player(
        hass,
        aioclient_mock,
        {GET_STATE: {"json": fixture_json("getstate_earc.json")}},
    )

    state = hass.states.get(entity_id)
    assert state.state != STATE_UNAVAILABLE
    assert not state.attributes[ATTR_SUPPORTED_FEATURES] & TRANSPORT_FEATURES
    # Not "paused on the disc": nothing here would resume it.
    assert state.state == MediaPlayerState.IDLE

    # Anything that reaches the entity anyway is a no-op, not an error.
    player = entity_object(hass, entity_id)
    await player.async_media_play()
    await player.async_media_pause()
    await player.async_media_next_track()
    await player.async_media_previous_track()
    await player.async_media_seek(30)

    assert calls_to(aioclient_mock, PLAY_OR_PAUSE) == 0
    assert calls_to(aioclient_mock, PLAY_NEXT) == 0
    assert calls_to(aioclient_mock, PLAY_LAST) == 0
    assert calls_to(aioclient_mock, SEEK_TO) == 0


async def test_a_boot_capable_player_reads_off_rather_than_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The A8 reports ``ableRemoteBoot``, so losing getState reads as off.

    Unavailable would make ``turn_on`` undispatchable in exactly the state it
    exists for. Accepted cost, stated in ``media_player.py``: this makes a
    network fault and a powered-down unit indistinguishable from the entity's
    state alone.
    """
    prime_device(aioclient_mock, _streaming())
    entry = await setup_integration(hass)
    entity_id = entity_id_for(hass, "_media_player")
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == MediaPlayerState.OFF


async def test_a_player_that_cannot_be_woken_still_goes_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without ``ableRemoteBoot``, ``turn_on`` could do nothing anyway.

    So honest unavailability — the plain live-tier-outage rule — is kept.
    """
    model = fixture_json("getmodel.json")
    model["ableRemoteBoot"] = False
    prime_device(aioclient_mock, _streaming() | {GET_MODEL: {"json": model}})
    entry = await setup_integration(hass)
    entity_id = entity_id_for(hass, "_media_player")
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    prime_device(aioclient_mock, {GET_STATE: {"exc": aiohttp.ClientError("offline")}})
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_source_list_is_the_hardware_inputs_plus_the_synthetic_cd(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The CD is not an input, but the user picks it like one."""
    entity_id = await _player(hass, aioclient_mock)

    assert hass.states.get(entity_id).attributes["source_list"] == [
        "Internal player",
        "Bluetooth In",
        "Record Player",
        "TV",
        CD_SOURCE,
    ]


async def test_a_genuine_disc_reads_back_as_the_cd_source(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With the disc actually playing (``playType`` 5), the source is the CD."""
    entity_id = await _player(hass, aioclient_mock, _cd())

    assert hass.states.get(entity_id).attributes["source"] == CD_SOURCE


async def test_a_disc_in_the_tray_is_not_the_source_while_spotify_plays(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """#03, the reported defect: a disc merely sitting in the tray isn't the source.

    The default fixture has a disc loaded but Spotify Connect audible
    (``playType`` 6) through the same internal-player input — the source
    must follow ``playType``, the same rule ``media_title`` etc. already
    follow (#02), not ``is_cd``.
    """
    entity_id = await _player(hass, aioclient_mock)

    assert hass.states.get(entity_id).attributes["source"] == "Internal player"


async def test_without_a_disc_the_source_is_the_input_name(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No disc means the plain input name, not a phantom CD."""
    entity_id = await _player(hass, aioclient_mock, _streaming())

    assert hass.states.get(entity_id).attributes["source"] == "Internal player"


async def test_a_disc_is_not_the_source_while_the_tv_input_is_live(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The unit still reports the disc on eARC — but the TV is what is playing."""
    on_the_tv = fixture_json("getinputandoutputlist.json")
    on_the_tv["inputIndex"] = 3

    entity_id = await _player(
        hass,
        aioclient_mock,
        {
            GET_STATE: {"json": fixture_json("getstate_earc.json")},
            GET_INPUT_OUTPUT: {"json": on_the_tv},
        },
    )

    assert hass.states.get(entity_id).attributes["source"] == "TV"


async def test_now_playing_does_not_show_the_disc_while_the_tv_input_is_live(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """#22: title/artist must not describe the disc when the TV is what plays.

    The same disagreement as the source test above — a disc left in the tray
    while eARC is the live input — but checking the now-playing fields the
    original #05 guard never covered.
    """
    on_the_tv = fixture_json("getinputandoutputlist.json")
    on_the_tv["inputIndex"] = 3

    entity_id = await _player(
        hass,
        aioclient_mock,
        {
            GET_STATE: {"json": fixture_json("getstate_earc.json")},
            GET_INPUT_OUTPUT: {"json": on_the_tv},
        },
    )

    state = hass.states.get(entity_id)
    assert state.attributes.get("media_title") is None
    assert state.attributes.get("media_artist") is None
    assert entity_object(hass, entity_id).media_image_url is None


async def test_the_discs_cover_url_carries_the_params_the_device_needs(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """#22 F4: ``getImage`` 806s without ``musicType``/``type`` — both must be sent.

    Only a genuine disc (``playType`` 5) takes this path at all now (#02) — a
    disc merely sitting in the tray while something else plays has no song id
    to fetch by, since it is not what's audible.
    """
    entity_id = await _player(hass, aioclient_mock, _cd())

    url = entity_object(hass, entity_id).media_image_url
    assert url is not None
    assert "id=128670077" in url
    assert "musicType=4" in url
    assert "type=4" in url
    assert "target=16" in url


async def test_selecting_cd_switches_to_the_internal_player_without_playing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Transport only works on XMOS (#03); starting the disc is not our call."""
    entity_id = await _player(hass, aioclient_mock, _streaming())

    await _call(
        hass, SERVICE_SELECT_SOURCE, entity_id, **{ATTR_INPUT_SOURCE: CD_SOURCE}
    )

    assert query_of(aioclient_mock, SET_INPUT) == {"tag": "XMOS", "index": "0"}
    assert calls_to(aioclient_mock, PLAY_OR_PAUSE) == 0


async def test_selecting_an_input_sends_its_own_tag_and_index(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A renamed input is picked by its label and written by its tag.

    Driven against a device that actually switches, so the confirming read
    reports the new input rather than repeating the old one.
    """
    writes: list[dict[str, str]] = []
    live = fixture_json("getinputandoutputlist.json")

    def _accept(query: dict[str, str]) -> None:
        live["inputIndex"] = int(query["index"])

    prime_device(
        aioclient_mock,
        _streaming()
        | {
            GET_INPUT_OUTPUT: answers_with(lambda: live),
            SET_INPUT: records_writes(writes, _accept),
        },
    )
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_media_player")
    reads = calls_to(aioclient_mock, GET_INPUT_OUTPUT)

    await _call(
        hass, SERVICE_SELECT_SOURCE, entity_id, **{ATTR_INPUT_SOURCE: "Record Player"}
    )

    assert writes == [{"tag": "SPDIF", "index": "2"}]
    assert hass.states.get(entity_id).attributes["source"] == "Record Player"
    # Confirmed against the device rather than left on the guess.
    assert calls_to(aioclient_mock, GET_INPUT_OUTPUT) == reads + 1


async def test_the_new_source_shows_while_the_confirm_read_is_in_flight(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Re-reading the settings tier takes several round trips on a real unit.

    The guess is what the card shows for that whole stretch, so it has to be
    there — even though the device's own answer replaces it a moment later.
    """
    entity_id = await _player(hass, aioclient_mock, _streaming())
    coordinator = entity_object(hass, entity_id).coordinator
    confirming = coordinator.async_refresh_settings
    shown: list[str] = []

    async def _watch() -> None:
        shown.append(hass.states.get(entity_id).attributes["source"])
        await confirming()

    with patch.object(coordinator, "async_refresh_settings", _watch):
        await _call(hass, SERVICE_SELECT_SOURCE, entity_id, **{ATTR_INPUT_SOURCE: "TV"})

    assert shown == ["TV"]
    # And the seam, which never actually switches, has the last word.
    assert hass.states.get(entity_id).attributes["source"] == "Internal player"


async def test_an_input_the_user_named_cd_stays_selectable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Inputs are renameable, so one can already be called CD — it wins."""
    renamed = fixture_json("getinputandoutputlist.json")
    renamed["inputData"][2]["name"] = CD_SOURCE  # SPDIF, the renameable one
    writes: list[dict[str, str]] = []
    prime_device(
        aioclient_mock,
        _streaming()
        | {
            GET_INPUT_OUTPUT: {"json": renamed},
            SET_INPUT: records_writes(writes),
        },
    )
    await setup_integration(hass)
    entity_id = entity_id_for(hass, "_media_player")

    sources = hass.states.get(entity_id).attributes["source_list"]
    assert sources.count(CD_SOURCE) == 1

    await _call(
        hass, SERVICE_SELECT_SOURCE, entity_id, **{ATTR_INPUT_SOURCE: CD_SOURCE}
    )

    # The user's input, not the synthetic source's internal player.
    assert writes == [{"tag": "SPDIF", "index": "2"}]


async def test_an_unknown_source_is_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A source the device does not have is a mistake worth reporting."""
    entity_id = await _player(hass, aioclient_mock, _streaming())

    with pytest.raises(ServiceValidationError):
        await _call(
            hass, SERVICE_SELECT_SOURCE, entity_id, **{ATTR_INPUT_SOURCE: "Gramophone"}
        )

    assert calls_to(aioclient_mock, SET_INPUT) == 0


async def test_a_unit_without_a_cd_drive_is_offered_no_cd_source(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The synthetic source only exists where a disc could actually be put."""
    entity_id = await _player(
        hass,
        aioclient_mock,
        {GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_CD_AUTO_PLAY)}},
    )

    assert CD_SOURCE not in hass.states.get(entity_id).attributes["source_list"]
    # And the disc the capture still reports cannot masquerade as the source.
    assert hass.states.get(entity_id).attributes["source"] == "Internal player"


async def test_the_player_offers_power_control_matching_the_buttons(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``turn_on``/``turn_off`` mirror the Power On/Off buttons' own gates."""
    entity_id = await _player(hass, aioclient_mock, _streaming())

    features = hass.states.get(entity_id).attributes[ATTR_SUPPORTED_FEATURES]
    assert features & MediaPlayerEntityFeature.TURN_ON
    assert features & MediaPlayerEntityFeature.TURN_OFF


async def test_a_player_with_neither_power_flag_offers_no_power_control(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A unit that accepts neither command advertises neither feature."""
    model = fixture_json("getmodel.json")
    model["ableRemoteBoot"] = False
    model["ableRemoteShutdown"] = False
    entity_id = await _player(
        hass, aioclient_mock, _streaming() | {GET_MODEL: {"json": model}}
    )

    features = hass.states.get(entity_id).attributes[ATTR_SUPPORTED_FEATURES]
    assert not features & MediaPlayerEntityFeature.TURN_ON
    assert not features & MediaPlayerEntityFeature.TURN_OFF


async def test_turn_on_sends_a_magic_packet_and_no_http_command(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``turn_on`` is Wake-on-LAN — there is no HTTP power-on endpoint to call.

    Sent twice, see wake_on_lan.py's module docstring — a single send can
    silently miss the unit's post-power-off settling window.
    """
    calls: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        wake_on_lan.wakeonlan,
        "send_magic_packet",
        lambda mac, *, ip_address, port: calls.append((mac, ip_address, port)),
    )

    async def _no_op_sleep(_delay: float) -> None:
        pass

    monkeypatch.setattr(wake_on_lan.asyncio, "sleep", _no_op_sleep)
    entity_id = await _player(hass, aioclient_mock, _streaming())

    await _call(hass, SERVICE_TURN_ON, entity_id)

    one_round = [(UNIQUE_ID, "192.168.0.255", port) for port in WAKE_ON_LAN_PORTS]
    assert calls == one_round + one_round


async def test_turn_off_sends_the_poweroff_command_and_updates_optimistically(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``turn_off`` is the same command the Power Off button sends."""
    entity_id = await _player(hass, aioclient_mock, _streaming())

    await _call(hass, SERVICE_TURN_OFF, entity_id)

    assert query_of(aioclient_mock, SET_POWER_OPTION) == {"tag": "poweroff"}
    assert hass.states.get(entity_id).state == MediaPlayerState.OFF
