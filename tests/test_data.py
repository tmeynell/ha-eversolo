"""Parsing tests for the typed data boundary, driven by real device captures."""

from __future__ import annotations

from custom_components.eversolo.const import SETTING_TAG_CD_AUTO_PLAY
from custom_components.eversolo.data import (
    EversoloCapabilities,
    EversoloData,
    EversoloDevice,
    EversoloInputs,
    EversoloLevel,
    EversoloOptionList,
    EversoloPlayback,
    EversoloProcessing,
    EversoloToggles,
    EversoloVisualization,
    EversoloVisualizationMode,
    EversoloVolume,
)

from .helpers import fixture_json, state_with, state_without


def test_device_parses_from_getmodel() -> None:
    """GetModel yields the full identity; what it can do is a capability."""
    device = EversoloDevice.from_model(fixture_json("getmodel.json"))

    assert device.model == "DMP-A8 Gen 2"
    assert device.net_mac == "aa:bb:cc:00:00:01"
    assert device.wif_mac == "aa:bb:cc:00:00:02"
    assert device.firmware == "v1.1.50"
    assert device.android_version == "14"


def test_playback_parses_cd_state() -> None:
    """A disc-loaded getState reads back as a CD with track metadata."""
    playback = EversoloPlayback.from_state(fixture_json("getstate_cd.json"))

    assert playback.is_cd is True
    assert playback.extension == "cd"
    assert playback.title == "Rabbit in Your Headlights"
    assert playback.artist == "UNKLE"
    assert playback.is_playing is True
    assert playback.duration == 369686
    assert playback.can_seek is True


def test_playback_parses_streaming_state() -> None:
    """A streaming getState is not a CD and carries stream format details."""
    playback = EversoloPlayback.from_state(fixture_json("getstate_streaming.json"))

    assert playback.is_cd is False
    assert playback.title == "Brother, Do You Know the Road?"
    assert playback.sample_rate == 44100
    assert playback.bit_depth == 16
    assert playback.format_label == "FLAC 44.1kHz/16bit"


def test_playback_on_the_tv_input_is_inert() -> None:
    """On eARC the disc is still reported, but nothing about it can be driven."""
    playback = EversoloPlayback.from_state(fixture_json("getstate_earc.json"))

    assert playback.can_change_play_status is False
    assert playback.can_seek is False
    assert playback.can_next is False
    assert playback.can_previous is False
    assert playback.is_playing is False


def test_a_stale_disc_is_not_trusted_while_another_input_is_live() -> None:
    """#22: ``playingMusic`` still names the disc on eARC — none of it is read.

    ``getstate_earc.json`` is the disc-loaded capture with ``intputTag``
    overridden to ``EARC-EARC`` (see ``fixtures/README.md``) — the exact
    disagreement #22 was found against. Every field sourced from
    ``playingMusic`` should read as absent, not as the disc's.
    """
    playback = EversoloPlayback.from_state(fixture_json("getstate_earc.json"))

    assert playback.is_cd is False
    assert playback.title is None
    assert playback.artist is None
    assert playback.album is None
    assert playback.song_id is None
    assert playback.music_type is None
    # bit_depth/bitrate have no output-block equivalent to fall back to on
    # this fixture, so both read as unknown rather than the disc's numbers.
    # (sample_rate is deliberately not asserted here: this derived fixture's
    # output block was left at the CD capture's own 44100 Hz — see
    # fixtures/README.md — so it cannot distinguish "read from output" from
    # "leaked from the disc". See the hand-built test below for that.)
    assert playback.bit_depth is None
    assert playback.bitrate is None


def test_a_disc_matching_the_live_input_is_trusted() -> None:
    """The counterpart to the above: matching input, disc data is read."""
    playback = EversoloPlayback.from_state(fixture_json("getstate_cd.json"))

    assert playback.is_cd is True
    assert playback.title == "Rabbit in Your Headlights"
    assert playback.music_type == 4
    assert playback.bitrate == "1.41 Mbps"


def test_a_disc_is_still_trusted_when_the_input_tag_is_unreported() -> None:
    """Missing ``volumeData``/``intputTag`` is "unknown", not "wrong input".

    A payload that never says which input is live has not contradicted a
    real, live disc — it has said nothing. Blanking ``playingMusic`` on that
    silence would hide a genuine CD session rather than a stale one.
    """
    state = fixture_json("getstate_cd.json")
    del state["volumeData"]

    playback = EversoloPlayback.from_state(state)

    assert playback.is_cd is True
    assert playback.title == "Rabbit in Your Headlights"


def test_format_reads_the_live_output_not_a_stale_disc() -> None:
    """#22 F1: a disc sitting unplayed elsewhere must not win over the DAC.

    The live acceptance run saw exactly this — HA read the disc's
    ``44.1kHz/16bit`` while the DAC was converting 48 kHz. Hand-built with a
    distinct output rate/depth, unlike ``getstate_earc.json``, so the
    assertion cannot be satisfied by coincidence.
    """
    state = state_with()
    state["volumeData"]["intputTag"] = "EARC-EARC"
    state["everSoloPlayInfo"]["everSoloPlayAudioInfo"] = {}
    state["everSoloPlayInfo"]["everSoloPlayOutputInfo"]["outPutSampleRate"] = 48000
    state["everSoloPlayInfo"]["everSoloPlayOutputInfo"]["outPutBits"] = 24

    playback = EversoloPlayback.from_state(state)

    assert playback.sample_rate == 48000
    assert playback.bit_depth == 24


def test_playback_falls_back_to_the_output_codec() -> None:
    """With no streaming session, the format reads off the output block.

    Built by hand rather than from a capture: this pins the parsing rule (a
    zeroed streaming block is absence, not a 0 Hz reading), not a claim about
    what any particular input reports.
    """
    playback = EversoloPlayback.from_state(
        {
            "playingMusic": {},
            "everSoloPlayInfo": {
                "everSoloPlayAudioInfo": {
                    "audioDecodec": "",
                    "audioSampleRate": 0,
                    "audioBitsPerSample": 0,
                },
                "everSoloPlayOutputInfo": {
                    "outPutDecodec": "PCM",
                    "outPutSampleRate": 48000,
                    "outPutBits": 24,
                },
            },
        }
    )

    assert playback.codec == "PCM"
    assert playback.sample_rate == 48000
    assert playback.bit_depth == 24
    assert playback.format_label == "PCM 48kHz/24bit"


def test_bitrate_falls_back_to_the_streaming_block() -> None:
    """#22 F1: bitrate had no fallback at all before this — only the disc's.

    Built by hand: no capture has ever shown a nonzero ``audioBitrate``, but
    the chain should still prefer it the same way ``sample_rate`` prefers the
    streaming block over the disc.
    """
    playback = EversoloPlayback.from_state(
        {
            "playingMusic": {"bitrate": "1.41 Mbps"},
            "everSoloPlayInfo": {
                "everSoloPlayAudioInfo": {"audioBitrate": 320},
                "everSoloPlayOutputInfo": {},
            },
        }
    )

    assert playback.bitrate == 320


def test_playback_without_media_has_no_format_label() -> None:
    """A device with nothing loaded reports no format rather than a stub."""
    playback = EversoloPlayback.from_state({})

    assert playback.format_label is None
    assert playback.is_playing is False
    assert playback.has_media is False


def test_volume_parses_and_scales() -> None:
    """VolumeData parses into a 0..1 level and preserves the input tag."""
    volume = EversoloVolume.from_state(fixture_json("getstate_cd.json"))

    assert volume.current == 127
    assert volume.maximum == 200
    assert volume.is_muted is False
    assert volume.input_tag == "XMOS-XMOS"
    assert volume.level == 127 / 200


def test_data_from_state_populates_live_slice() -> None:
    """EversoloData.from_state fills playback/volume/device from one getState."""
    data = EversoloData.from_state(fixture_json("getstate_cd.json"))

    assert data.playback is not None
    assert data.volume is not None
    assert data.device is not None
    assert data.device.model == "DMP-A8 Gen 2"
    assert data.processing.dsp_active is True
    assert data.settings == {}
    assert data.capabilities is None


def test_inputs_parse_from_the_input_list() -> None:
    """The four hardware inputs parse, with the live one identified."""
    inputs = EversoloInputs.from_payload(fixture_json("getinputandoutputlist.json"))

    assert [entry.tag for entry in inputs.available] == ["XMOS", "BT", "SPDIF", "EARC"]
    assert [entry.name for entry in inputs.available] == [
        "Internal player",
        "Bluetooth In",
        "Record Player",
        "TV",
    ]
    assert inputs.current.tag == "XMOS"
    assert inputs.by_name("TV").index == 3
    assert inputs.by_tag("BT").name == "Bluetooth In"
    assert inputs.by_name("Nothing") is None


def test_inputs_are_empty_before_the_list_is_read() -> None:
    """An unread input list is no inputs, not a crash."""
    inputs = EversoloInputs.from_payload(None)

    assert inputs.available == ()
    assert inputs.current is None


def test_toggles_read_off_the_settings_tree() -> None:
    """Every ``?switch=`` toggle reports its state in the settings tree."""
    toggles = EversoloToggles.from_settings(fixture_json("getsystemsettings.json"))

    assert toggles.is_on(SETTING_TAG_CD_AUTO_PLAY) is False
    assert toggles.is_on("SettingsItemTagGallessnewPlay") is True
    # Entries that are not toggles have no state to report.
    assert toggles.is_on("SettingsItemTagMasterClock") is None
    assert EversoloToggles.from_settings(None).states == {}


def test_toggles_merge_the_sub_pages_with_the_main_tree() -> None:
    """The subwoofer's on/off is inside a sub-page, not in the tree itself."""
    toggles = EversoloToggles.from_settings(
        fixture_json("getsystemsettings.json"),
        fixture_json("getsuboutputoption.json"),
    )

    assert toggles.is_on("SettingsItemTagSubSwitchEnable") is True
    # The main tree's own toggles survive the merge.
    assert toggles.is_on(SETTING_TAG_CD_AUTO_PLAY) is False


def test_merge_publishes_inputs_and_toggles() -> None:
    """Entities read the parsed inputs and toggles, not the raw blobs."""
    data = EversoloData.from_state(fixture_json("getstate_cd.json")).merge(
        settings={
            "input_output_state": fixture_json("getinputandoutputlist.json"),
            "system_settings": fixture_json("getsystemsettings.json"),
            "sub_output_option": fixture_json("getsuboutputoption.json"),
        }
    )

    assert data.inputs.current.name == "Internal player"
    assert data.toggles.is_on(SETTING_TAG_CD_AUTO_PLAY) is False
    assert data.toggles.is_on("SettingsItemTagSubSwitchEnable") is True


def test_an_option_list_carries_its_own_setter() -> None:
    """The setter is data the device supplies, not a name derived from the getter."""
    options = EversoloOptionList.from_payload(
        fixture_json("getxlroutputpcmfilterlist.json")
    )

    assert options.titles[0] == "Sharp Roll-off"
    assert options.current.title == "Short Delay Sharp Roll-off"
    assert options.by_title("Slow Roll-off").index == 1
    assert options.by_title("Nothing") is None
    # getXlrOutputPcmFilterList is written by setPcmFilter — nothing about the
    # getter's name says so.
    assert (
        options.setter_url
        == "/SystemSettings/audioSettings/xlrOutputOption/setPcmFilter?index="
    )


def test_an_unread_option_list_is_empty_not_a_crash() -> None:
    """A list the device has not answered for yet offers nothing."""
    options = EversoloOptionList.from_payload(None)

    assert options.options == ()
    assert options.current is None
    assert options.setter_url is None


def test_outputs_keep_the_device_s_own_numbering() -> None:
    """A socket that is not offered still counts toward every later index."""
    payload = fixture_json("getinputandoutputlist.json")
    # Disable the first socket: the app numbers over the raw list, so the ones
    # after it must keep the indices they already had.
    payload["outputData"][0]["enable"] = False

    options = EversoloOptionList.from_outputs(payload)

    assert options.titles == ["Analog-RCA", "XLR/RCA", "IIS", "OPT/COAX/AES"]
    assert options.by_title("XLR/RCA").index == 2
    assert options.by_title("XLR/RCA").tag == "XLRRCA"
    # outputIndex 1 in the capture, and it still resolves.
    assert options.current.title == "Analog-RCA"
    # Routing has no url of its own; setOutInputList writes it.
    assert options.setter_url is None


def test_a_socket_that_is_selected_and_disabled_reports_no_current_option() -> None:
    """Not offering a dead socket costs the reading when the unit sits on one.

    The device would have to contradict itself to get here — routed to a
    socket it also calls disabled — so saying nothing beats naming a choice
    that cannot be chosen back.
    """
    payload = fixture_json("getinputandoutputlist.json")
    payload["outputData"][1]["enable"] = False  # Analog-RCA, and outputIndex is 1.

    options = EversoloOptionList.from_outputs(payload)

    assert "Analog-RCA" not in options.titles
    assert options.current is None


def test_the_live_input_name_comes_from_the_state_read() -> None:
    """Live readings need the live tag, not the settings tier's lagging index.

    ``inputs.current`` deliberately trails a write; anything describing what
    ``getState`` just reported has to move in the same poll, so it resolves
    ``volumeData.intputTag`` against the input list instead.
    """
    settings = {"input_output_state": fixture_json("getinputandoutputlist.json")}
    state = fixture_json("getstate_cd.json")
    state["volumeData"]["intputTag"] = "EARC-EARC"

    data = EversoloData.from_state(state).merge(settings=settings)

    # The settings tier still says XMOS; the live tag has already moved on.
    assert data.inputs.current.name == "Internal player"
    assert data.live_input_name == "TV"


def test_the_live_input_name_falls_back_to_the_bare_tag() -> None:
    """Before the input list is read there is no label, but there is a tag."""
    data = EversoloData.from_state(fixture_json("getstate_cd.json"))

    assert data.inputs.available == ()
    assert data.live_input_name == "XMOS"
    assert EversoloData().live_input_name is None


def test_capabilities_detected_from_real_a8_tree() -> None:
    """The real A8 capture yields CD/sub/clock/analog present, knob absent."""
    caps = EversoloCapabilities.detect(
        system_settings=fixture_json("getsystemsettings.json"),
        model=fixture_json("getmodel.json"),
        knob_option=fixture_json("getknobsettingoption.json"),
    )

    assert caps.has_cd is True
    assert caps.has_subwoofer is True
    assert caps.has_master_clock is True
    assert caps.has_analog_panel is True
    assert caps.has_reboot is True
    assert caps.has_power_off is True
    assert caps.has_gapless is True
    assert caps.has_eos_engine is True
    # A8 has no knob; getKnobSettingOption returns an empty items list.
    assert caps.has_knob is False
    # Routing is gated on the socket list, which was not handed over here.
    assert caps.has_output_routing is False


def test_processing_parses_the_dsp_and_eq_block() -> None:
    """The state read says which of the two sides exist, and whether each is on."""
    processing = EversoloProcessing.from_state(fixture_json("getstate_cd.json"))

    assert processing.has_dsp is True
    assert processing.dsp_active is True
    # The A8's EQ side (the digital outputs) is absent, not merely idle.
    assert processing.has_eq is False
    assert processing.eq_active is False


def test_processing_is_unknown_before_the_device_answers() -> None:
    """No reading is not "DSP is off"; the sensor should say nothing yet.

    All four fields, not just the two ``*_active`` ones: an unreported
    ``hasDspSetting`` is the device staying silent about what hardware it has,
    and reading that as "it has none" is what latched the gate off for good.
    """
    processing = EversoloProcessing()

    assert processing.has_dsp is None
    assert processing.has_eq is None
    assert processing.dsp_active is None
    assert processing.eq_active is None
    assert processing.reports_capabilities is False


def test_a_flag_the_device_omits_stays_unknown() -> None:
    """A payload missing the field is not the device saying it is off."""
    state = fixture_json("getstate_cd.json")
    del state["dspActive"]

    processing = EversoloProcessing.from_state(state)

    assert processing.dsp_active is None
    # A field that *is* reported still parses either way.
    assert processing.eq_active is False


def test_a_capability_flag_the_device_omits_stays_unknown() -> None:
    """Absent is not false — the distinction the gate latch depends on."""
    processing = EversoloProcessing.from_state(state_without("hasDspSetting"))

    assert processing.has_dsp is None
    # The A8 reports no EQ side, which is an answer and parses as one.
    assert processing.has_eq is False
    # Half a block is not the pair: nothing here has said anything about DSP.
    assert processing.reports_capabilities is False


def test_a_state_carrying_both_flags_counts_as_an_answer() -> None:
    """Including when both answers are no, which is a device saying so."""
    processing = EversoloProcessing.from_state(
        state_with(hasDspSetting=False, hasEQSetting=False)
    )

    assert processing.reports_capabilities is True
    assert processing.has_dsp is False


def test_a_later_reading_keeps_gates_an_earlier_one_answered() -> None:
    """Hardware answered once stays answered; a silence does not unsay it."""
    earlier = EversoloProcessing.from_state(state_without("hasEQSetting"))
    later = EversoloProcessing.from_state(
        state_without("hasDspSetting", "hasEQSetting")
    )

    combined = later.retaining_gates_from(earlier)

    assert combined.has_dsp is True
    # Still unanswered — neither payload ever mentioned it.
    assert combined.has_eq is None
    assert combined.reports_capabilities is False


def test_a_later_reading_wins_where_both_answered() -> None:
    """Accumulating fills gaps; it does not pin the first answer forever."""
    earlier = EversoloProcessing.from_state(state_with(hasDspSetting=False))
    later = EversoloProcessing.from_state(state_with(hasDspSetting=True))

    assert later.retaining_gates_from(earlier).has_dsp is True


def test_the_moment_readings_are_not_accumulated() -> None:
    """``dsp_active`` describes now, so a remembered one would be a lie."""
    earlier = EversoloProcessing.from_state(fixture_json("getstate_cd.json"))
    assert earlier.dsp_active is True
    later = EversoloProcessing.from_state(state_with(dspActive=False))

    assert later.retaining_gates_from(earlier).dsp_active is False


def test_settling_the_gates_leaves_every_other_capability_alone() -> None:
    """The two getState gates are decided apart from the rest, not instead."""
    caps = EversoloCapabilities.detect(
        system_settings=fixture_json("getsystemsettings.json"),
        model=fixture_json("getmodel.json"),
        processing=EversoloProcessing.from_state(state_without("hasDspSetting")),
    )
    assert caps.has_dsp is False  # provisional: nothing was reported
    assert caps.has_cd is True

    settled = caps.with_processing(
        EversoloProcessing.from_state(fixture_json("getstate_cd.json"))
    )

    assert settled.has_dsp is True
    assert settled.has_eq is False
    assert settled.has_cd is True
    assert settled.has_reboot == caps.has_reboot


def test_capabilities_read_the_dsp_and_eq_gates_from_state() -> None:
    """The two flags live in getState alone — the settings tree has no DSP."""
    caps = EversoloCapabilities.detect(
        system_settings=fixture_json("getsystemsettings.json"),
        model=fixture_json("getmodel.json"),
        processing=EversoloProcessing.from_state(fixture_json("getstate_cd.json")),
    )

    assert caps.has_dsp is True
    assert caps.has_eq is False


def test_capabilities_pick_up_an_eq_side_when_a_unit_has_one() -> None:
    """Gated off on the A8, so the positive case comes from a mutated capture."""
    caps = EversoloCapabilities.detect(
        processing=EversoloProcessing.from_state(state_with(hasEQSetting=True)),
    )

    assert caps.has_eq is True


def test_capabilities_gate_dsp_off_without_a_state_read() -> None:
    """A unit that has not answered yet has no DSP as far as anyone knows."""
    caps = EversoloCapabilities.detect(
        system_settings=fixture_json("getsystemsettings.json"),
        model=fixture_json("getmodel.json"),
    )

    assert caps.has_dsp is False
    assert caps.has_eq is False


def test_capabilities_gate_off_when_tag_absent() -> None:
    """Removing the Master Clock tag drops the capability (gating works)."""
    settings = fixture_json("getsystemsettings.json")

    def strip_tag(node, tag):
        if isinstance(node, dict):
            return {k: strip_tag(v, tag) for k, v in node.items() if v != tag}
        if isinstance(node, list):
            return [strip_tag(i, tag) for i in node]
        return node

    mutated = strip_tag(settings, "SettingsItemTagMasterClock")
    caps = EversoloCapabilities.detect(
        system_settings=mutated,
        model=fixture_json("getmodel.json"),
    )

    assert caps.has_master_clock is False
    # Unrelated capabilities are unaffected.
    assert caps.has_cd is True


def test_visualization_reads_both_display_flags() -> None:
    """The front screen's state is the pair of flags getState carries.

    Both are ``0`` in every capture, which is the "neither" reading — see
    :class:`EversoloVisualization` for why ``0`` and ``-1`` are treated alike.
    """
    visualization = EversoloVisualization.from_payload(fixture_json("getstate_cd.json"))

    assert visualization.vu_mode == 0
    assert visualization.spectrum_mode == 0
    assert visualization.mode is EversoloVisualizationMode.OFF


def test_visualization_reports_whichever_side_is_up() -> None:
    """A flag at 1 or above is that visualization showing."""
    assert (
        EversoloVisualization.from_payload(
            {"vuDisplayMode": 1, "spDisplayMode": 0}
        ).mode
        is EversoloVisualizationMode.VU_METER
    )
    assert (
        EversoloVisualization.from_payload(
            {"vuDisplayMode": 0, "spDisplayMode": 1}
        ).mode
        is EversoloVisualizationMode.SPECTRUM
    )
    # -1/-1 is the state the device reports after switching the last one off.
    assert (
        EversoloVisualization.from_payload(
            {"vuDisplayMode": -1, "spDisplayMode": -1}
        ).mode
        is EversoloVisualizationMode.OFF
    )


def test_visualization_is_unknown_before_the_device_answers() -> None:
    """No flags is no reading — distinct from the device saying "neither"."""
    assert EversoloVisualization.from_payload({}).mode is None
    assert EversoloVisualization().mode is None


def test_a_level_reads_as_percent_of_its_own_maximum() -> None:
    """Brightness is a 0..255 index the device also renders as a percentage.

    The vendor's app computes ``currentValue / maxValue * 100`` and ignores
    ``minValue``; the capture agrees, reporting "11%" for 30 of 255.
    """
    level = EversoloLevel.from_payload(fixture_json("getscreenbrightness.json"))

    assert level.current == 30
    assert level.maximum == 255
    assert round(level.percent) == 12
    assert (
        level.setter_url == "/SystemSettings/displaySettings/setScreenBrightness?index="
    )


def test_a_level_maps_a_percentage_back_to_the_device_s_index() -> None:
    """What the slider writes is an index in the device's own range."""
    level = EversoloLevel.from_payload(fixture_json("getscreenbrightness.json"))

    assert level.index_for(0) == 0
    assert level.index_for(100) == 255
    assert level.index_for(50) == 128


def test_an_unread_level_reports_nothing_and_writes_nothing() -> None:
    """Before the device has said what its range is, there is nothing to scale."""
    level = EversoloLevel.from_payload(None)

    assert level.current is None
    assert level.percent is None
    assert level.index_for(50) is None
    assert level.setter_url is None


def test_screen_power_is_gated_on_the_tag_the_device_offers() -> None:
    """``getPowerOption`` listing a ``screen`` tag is what says the unit has one."""
    power_option = fixture_json("getpoweroption.json")

    assert (
        EversoloCapabilities.detect(power_option=power_option).has_screen_power is True
    )

    power_option["data"] = [
        item for item in power_option["data"] if item["tag"] != "screen"
    ]
    assert (
        EversoloCapabilities.detect(power_option=power_option).has_screen_power is False
    )
    assert EversoloCapabilities.detect().has_screen_power is False


def test_the_live_slice_carries_the_visualization() -> None:
    """The screen flags ride in getState, so they land on the live tier."""
    data = EversoloData.from_state(fixture_json("getstate_cd.json"))

    assert data.visualization.mode is EversoloVisualizationMode.OFF


def test_a_level_reports_a_whole_percentage() -> None:
    """The slider steps in ones, so a state between steps would never rest."""
    level = EversoloLevel.from_payload(fixture_json("getscreenbrightness.json"))

    assert level.percent == 12


def test_a_level_never_writes_below_the_floor_the_device_declared() -> None:
    """``percent`` ignores ``minValue``; the write side cannot afford to."""
    level = EversoloLevel.from_payload(
        {"currentValue": 20, "minValue": 10, "maxValue": 255}
    )

    assert level.index_for(0) == 10
    assert level.index_for(100) == 255


def test_a_level_can_assume_a_range_the_device_did_not_report() -> None:
    """A slider on uncaptured hardware works from a verified range, not nothing."""
    reported = EversoloLevel.from_payload({"currentValue": 128, "maxValue": 100})
    silent = EversoloLevel.from_payload({"currentValue": 128})

    # A device that said what its range is keeps it.
    assert reported.assuming_maximum(255).maximum == 100
    assert silent.assuming_maximum(255).maximum == 255
    assert silent.assuming_maximum(255).percent == 50
