"""Every capability gate, both ways, in one table.

The live acceptance run on the A8 can only ever prove the *positive* branch of
capability gating: the designed entities appear, and the ones for hardware the
A8 does not have (the A6's knob) stay away. It cannot prove the negative branch
— nobody is going to remove the master clock from the unit to watch its select
disappear — so that half is proved here instead, by mutating the captured
payload the gate actually reads and setting the integration up against it.

Per-gate coverage already exists scattered through the platform test modules,
where each case sits beside the entity it concerns. This module is the
*systematic* pass over the same ground: one row per capability, so that a
capability added later without a gating test fails :func:`test_every_capability_is_covered`
rather than quietly shipping ungated. The failure it guards against — an entity
that never appears at all — is the one that is hardest to notice in a manual
acceptance run, which is why it is worth having twice.

Each row says which payload carries the gate, how to mutate that payload in
both directions, and which entities hang off it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.const import (
    DOMAIN,
    POWER_TAG_SCREEN,
    SETTING_TAG_ANALOG_PANEL,
    SETTING_TAG_AUTO_CHANGE_SOURCE,
    SETTING_TAG_CD_AUTO_PLAY,
    SETTING_TAG_EOS_ENGINE,
    SETTING_TAG_GAPLESS,
    SETTING_TAG_KNOB_COLOR,
    SETTING_TAG_MASTER_CLOCK,
    SETTING_TAG_SCREEN_BRIGHTNESS,
    SETTING_TAG_SPECTRUM_MODE,
    SETTING_TAG_SUBWOOFER,
    SETTING_TAG_VU_MODE,
)
from custom_components.eversolo.data import EversoloCapabilities

from .helpers import (
    GET_INPUT_OUTPUT,
    GET_KNOB_BRIGHTNESS,
    GET_KNOB_OPTION,
    GET_MODEL,
    GET_POWER_OPTION,
    GET_STATE,
    GET_SYSTEM_SETTINGS,
    fixture_json,
    prime_device,
    settings_without,
    setup_integration,
    state_with,
)

GET_KNOB_COLOR = "/SystemSettings/displaySettings/getKnobLightColorList"

# One preview-image entity per option the captures list, keyed the way
# ``EversoloOptionPreviewImage`` builds its unique id — device ``index``, which
# these two fixtures happen to number the same as list position.
VU_STYLE_PREVIEWS = tuple(
    ("image", f"_vu_style_preview_{option['index']}")
    for option in fixture_json("getvumodelist.json")["data"]
)
SPECTRUM_STYLE_PREVIEWS = tuple(
    ("image", f"_spectrum_style_preview_{option['index']}")
    for option in fixture_json("getspplaymodelist.json")["data"]
)

# A knob-bearing unit, which only the A6 is, so no capture shows one. Two
# shapes: a plain knob, and a knob whose light takes a colour.
KNOB_ONLY = {
    "status": 200,
    "items": [{"tag": "SettingsItemTagKnobLight", "title": "Knob brightness"}],
}
KNOB_WITH_COLOR = {
    "status": 200,
    "items": [
        {"tag": "SettingsItemTagKnobLight", "title": "Knob brightness"},
        {"tag": SETTING_TAG_KNOB_COLOR, "title": "Knob light colour"},
    ],
}
KNOB_BRIGHTNESS = {"status": 200, "currentValue": 128, "minValue": 0, "maxValue": 255}
KNOB_COLOR_LIST = {
    "status": 200,
    "currentIndex": 0,
    "data": [{"index": 0, "title": "White"}, {"index": 1, "title": "Blue"}],
}


def _model_without(*flags: str) -> dict[str, Any]:
    """Return the captured ``getModel`` with power flags turned off."""
    model = fixture_json("getmodel.json")
    model.update(dict.fromkeys(flags, False))
    return model


def _power_option_without(tag: str) -> dict[str, Any]:
    """Return the captured power menu with one entry removed."""
    payload = fixture_json("getpoweroption.json")
    payload["data"] = [item for item in payload["data"] if item.get("tag") != tag]
    return payload


def _input_output_without_outputs() -> dict[str, Any]:
    """Return the socket list of a unit that offers no output routing."""
    payload = fixture_json("getinputandoutputlist.json")
    payload["outputData"] = []
    return payload


@dataclass(frozen=True)
class Gate:
    """One capability, the payload that decides it, and what hangs off it."""

    capability: str
    # Entities as ``domain.unique_id suffix`` — the suffix is what the entity
    # registry can be searched on without depending on the friendly name.
    entities: tuple[tuple[str, str], ...]
    # Mocker overrides that make the device *lack* the capability.
    absent: dict[str, dict[str, Any]]
    # Overrides that make it *have* it. Empty means the A8 capture already
    # does, which is the usual case.
    present: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Entities the ``absent`` mutation legitimately takes with it, beyond this
    # gate's own. Only two rows have any, and each says why: the assertion is
    # otherwise an exact set, so an over-broad gate cannot hide behind it.
    also_absent: tuple[tuple[str, str], ...] = ()
    # Entities the ``absent`` mutation adds, for a row whose negative case is
    # itself a piece of hardware rather than the plain capture.
    also_present: tuple[tuple[str, str], ...] = ()

    def __str__(self) -> str:
        """Name the parametrised case after the capability it covers."""
        return self.capability


# One row per field of ``EversoloCapabilities``. ``has_visualization`` is
# derived rather than detected, but it gates its own entity, so it is a row too.
GATES: tuple[Gate, ...] = (
    Gate(
        capability="has_cd",
        entities=(("switch", "_cd_auto_play"),),
        absent={
            GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_CD_AUTO_PLAY)}
        },
    ),
    Gate(
        capability="has_subwoofer",
        entities=(("switch", "_subwoofer_output"),),
        absent={GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_SUBWOOFER)}},
    ),
    Gate(
        capability="has_master_clock",
        entities=(("select", "_master_clock"),),
        absent={
            GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_MASTER_CLOCK)}
        },
    ),
    Gate(
        # One shared XLR+RCA page, so one tag decides both of its lists.
        capability="has_analog_panel",
        entities=(("select", "_dac_filter"), ("select", "_upsampling")),
        absent={
            GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_ANALOG_PANEL)}
        },
    ),
    Gate(
        capability="has_gapless",
        entities=(("switch", "_gapless"),),
        absent={GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_GAPLESS)}},
    ),
    Gate(
        capability="has_eos_engine",
        entities=(("switch", "_eos_engine"),),
        absent={
            GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_EOS_ENGINE)}
        },
    ),
    Gate(
        capability="has_auto_change_source",
        entities=(("switch", "_auto_change_source_internal_player"),),
        absent={
            GET_SYSTEM_SETTINGS: {
                "json": settings_without(SETTING_TAG_AUTO_CHANGE_SOURCE)
            }
        },
    ),
    Gate(
        capability="has_screen_brightness",
        entities=(("number", "_screen_brightness"),),
        absent={
            GET_SYSTEM_SETTINGS: {
                "json": settings_without(SETTING_TAG_SCREEN_BRIGHTNESS)
            }
        },
    ),
    Gate(
        capability="has_vu_style",
        entities=(("select", "_vu_style"), *VU_STYLE_PREVIEWS),
        absent={GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_VU_MODE)}},
    ),
    Gate(
        capability="has_spectrum_style",
        entities=(("select", "_spectrum_style"), *SPECTRUM_STYLE_PREVIEWS),
        absent={
            GET_SYSTEM_SETTINGS: {"json": settings_without(SETTING_TAG_SPECTRUM_MODE)}
        },
    ),
    Gate(
        # Derived from the two style tags: losing one leaves the mode select
        # standing, losing both takes it away.
        capability="has_visualization",
        entities=(("select", "_visualization"),),
        absent={
            GET_SYSTEM_SETTINGS: {
                "json": settings_without(SETTING_TAG_VU_MODE, SETTING_TAG_SPECTRUM_MODE)
            }
        },
        # The one mutation that must take other entities with it: there is no
        # tree without both style lists that still has either style select —
        # or their preview images, which the same two tags gate.
        also_absent=(
            ("select", "_vu_style"),
            ("select", "_spectrum_style"),
            *VU_STYLE_PREVIEWS,
            *SPECTRUM_STYLE_PREVIEWS,
        ),
    ),
    Gate(
        # Not in the settings tree at all — the socket list is its own gate.
        capability="has_output_routing",
        entities=(("select", "_output_routing"),),
        absent={GET_INPUT_OUTPUT: {"json": _input_output_without_outputs()}},
    ),
    Gate(
        capability="has_reboot",
        entities=(("button", "_reboot"),),
        absent={GET_MODEL: {"json": _model_without("ableRemoteReboot")}},
    ),
    Gate(
        capability="has_power_off",
        entities=(("button", "_power_off"),),
        absent={GET_MODEL: {"json": _model_without("ableRemoteShutdown")}},
    ),
    Gate(
        capability="has_power_on",
        entities=(("button", "_power_on"),),
        absent={GET_MODEL: {"json": _model_without("ableRemoteBoot")}},
    ),
    Gate(
        # Absent on the A8, so this row runs the other way round: the capture
        # is the negative case and the A6's knob has to be stood up.
        capability="has_knob",
        entities=(("number", "_knob_brightness"),),
        absent={},
        present={
            GET_KNOB_OPTION: {"json": KNOB_ONLY},
            GET_KNOB_BRIGHTNESS: {"json": KNOB_BRIGHTNESS},
        },
    ),
    Gate(
        capability="has_knob_color",
        entities=(("select", "_knob_color"),),
        # A knob without a colour list: proves the two gates are separate, not
        # one "has a knob" flag doing both jobs. That claim only holds if the
        # knob really was stood up, which is what ``also_present`` asserts —
        # without it, an override that silently stopped applying would leave
        # the A8's knobless capture answering and the row would pass for
        # exactly the wrong reason.
        absent={
            GET_KNOB_OPTION: {"json": KNOB_ONLY},
            GET_KNOB_BRIGHTNESS: {"json": KNOB_BRIGHTNESS},
        },
        also_present=(("number", "_knob_brightness"),),
        present={
            GET_KNOB_OPTION: {"json": KNOB_WITH_COLOR},
            GET_KNOB_BRIGHTNESS: {"json": KNOB_BRIGHTNESS},
            GET_KNOB_COLOR: {"json": KNOB_COLOR_LIST},
        },
    ),
    Gate(
        # The two gates the settings tree cannot answer for: they are in
        # getState and nowhere else.
        capability="has_dsp",
        entities=(("binary_sensor", "_dsp_active"),),
        absent={GET_STATE: {"json": state_with(hasDspSetting=False)}},
    ),
    Gate(
        capability="has_eq",
        entities=(("binary_sensor", "_eq_active"),),
        absent={},
        present={GET_STATE: {"json": state_with(hasEQSetting=True)}},
    ),
    Gate(
        # Nor can the tree answer for the screen: the only thing that says this
        # unit has one to switch off is the power menu offering the tag.
        capability="has_screen_power",
        entities=(("switch", "_screen"),),
        absent={GET_POWER_OPTION: {"json": _power_option_without(POWER_TAG_SCREEN)}},
    ),
)

# The entities no capability gates. At least one must survive every mutation
# above, or "the gate worked" and "setup fell over" look identical.
UNGATED = (
    ("image", "_panel_screenshot"),
    ("media_player", "_media_player"),
    ("sensor", "_audio_format"),
    ("sensor", "_input"),
)

# Exactly what the captured A8 justifies. This is the set the live acceptance
# checklist's gating section counts by hand against the real unit; pinning it
# here is what stops the checklist and the code drifting apart between runs.
A8_ENTITY_SET = {
    *UNGATED,
    ("binary_sensor", "_dsp_active"),
    ("button", "_reboot"),
    ("button", "_power_off"),
    ("button", "_power_on"),
    ("number", "_screen_brightness"),
    ("select", "_output_routing"),
    ("select", "_dac_filter"),
    ("select", "_upsampling"),
    ("select", "_master_clock"),
    ("select", "_vu_style"),
    ("select", "_spectrum_style"),
    ("select", "_visualization"),
    *VU_STYLE_PREVIEWS,
    *SPECTRUM_STYLE_PREVIEWS,
    ("switch", "_cd_auto_play"),
    ("switch", "_subwoofer_output"),
    ("switch", "_gapless"),
    ("switch", "_eos_engine"),
    ("switch", "_auto_change_source_internal_player"),
    ("switch", "_screen"),
}

# Hardware the A8 does not have, or a feature it reports off: gated away, and
# the checklist confirms their absence on the real unit.
A8_ABSENT = {
    ("number", "_knob_brightness"),
    ("select", "_knob_color"),
    ("binary_sensor", "_eq_active"),
}


def _registered(hass: HomeAssistant) -> set[tuple[str, str]]:
    """Every entity this integration created, as ``(domain, unique_id suffix)``.

    The suffix is the entity description's key, which is what the gate decides;
    reading the registry rather than ``hass.states`` also catches an entity that
    was created but never got a state.
    """
    registry = er.async_get(hass)
    entry_id = next(iter(hass.config_entries.async_entries(DOMAIN))).entry_id
    return {
        (entry.domain, entry.unique_id.removeprefix(entry_id))
        for entry in registry.entities.values()
        if entry.platform == DOMAIN
    }


async def _entities_for(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    overrides: dict[str, dict[str, Any]],
) -> set[tuple[str, str]]:
    """Set the integration up against a mutated device and list what it made."""
    prime_device(aioclient_mock, overrides)
    await setup_integration(hass)
    return _registered(hass)


@pytest.mark.parametrize("gate", GATES, ids=str)
async def test_a_capability_the_device_lacks_creates_no_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, gate: Gate
) -> None:
    """Remove what a gate reads, and *only* its entities stop being created.

    This is the branch the live acceptance run cannot reach: it needs hardware
    the A8 does not have, or the removal of hardware it does.

    The assertion is the whole entity set rather than "these are missing",
    because the failure worth catching cuts both ways. A gate that suppresses
    too much — one mistyped lambda taking a sibling entity down with it — is
    invisible to a test that only checks its own entities went away, and an
    entity that silently never appears is the hardest defect to notice in a
    manual acceptance pass. The two ``also_*`` fields carry the only
    legitimate departures, each with its reason at the row.
    """
    created = await _entities_for(hass, aioclient_mock, gate.absent)

    expected = (A8_ENTITY_SET - set(gate.entities) - set(gate.also_absent)) | set(
        gate.also_present
    )
    assert created == expected, f"{gate.capability} gated the wrong entity set"


@pytest.mark.parametrize(
    # Rows whose ``present`` is empty are the plain capture, which
    # ``test_the_captured_a8_gets_exactly_the_designed_entity_set`` already
    # asserts in full — and so does every ``absent`` row above, since that one
    # now pins the whole set. Re-running them here would buy nothing but a
    # Home Assistant setup each.
    "gate",
    [gate for gate in GATES if gate.present],
    ids=str,
)
async def test_a_capability_the_device_has_creates_its_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, gate: Gate
) -> None:
    """Stand up hardware the A8 lacks, and the entities behind it appear."""
    created = await _entities_for(hass, aioclient_mock, gate.present)

    for entity in gate.entities:
        assert entity in created, f"{gate.capability} did not create {entity}"


async def test_the_captured_a8_gets_exactly_the_designed_entity_set(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The unmutated capture yields the designed entity set, and nothing else.

    The live acceptance run counts this set by eye on the real unit. An entity
    that appears here and not on the checklist — or the other way round — is
    exactly the drift this pins down, and it is cheaper to catch in CI than
    halfway through an acceptance pass.
    """
    created = await _entities_for(hass, aioclient_mock, {})

    assert created == A8_ENTITY_SET


def test_the_absent_set_is_not_quietly_part_of_the_designed_one() -> None:
    """The two hand-written sets must stay disjoint, or both stop meaning much.

    A pure bookkeeping check on the table itself, which is why it needs no
    device: it guards the constants the checklist is read against.
    """
    assert not A8_ABSENT & A8_ENTITY_SET


async def test_every_capability_is_covered() -> None:
    """A capability added without a row here is a capability nobody gated.

    The table is only systematic if it stays exhaustive, and the cost of it
    silently not being is an entity that never appears on someone's unit.
    """
    covered = {gate.capability for gate in GATES}
    declared = {
        name for name in EversoloCapabilities.__slots__ if name.startswith("has_")
    }
    # ``has_visualization`` is a property rather than a field, so it is not in
    # __slots__; it gates an entity, so the table carries it anyway.
    assert declared - covered == set(), f"ungated capabilities: {declared - covered}"
    assert covered - declared == {"has_visualization"}


async def test_a_device_that_answers_nothing_optional_still_loads(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Strip every gate at once: the entry loads with the ungated core only.

    The floor of the integration — what a unit sharing nothing but the
    transport would still get.
    """
    tree = fixture_json("getsystemsettings.json")
    for group in tree.get("settings", []):
        group["items"] = []

    created = await _entities_for(
        hass,
        aioclient_mock,
        {
            GET_SYSTEM_SETTINGS: {"json": tree},
            GET_MODEL: {
                "json": _model_without(
                    "ableRemoteReboot", "ableRemoteShutdown", "ableRemoteBoot"
                )
            },
            GET_POWER_OPTION: {"json": _power_option_without("screen")},
            GET_INPUT_OUTPUT: {"json": _input_output_without_outputs()},
            GET_STATE: {"json": state_with(hasDspSetting=False, hasEQSetting=False)},
        },
    )

    assert created == set(UNGATED)
