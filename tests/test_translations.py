"""Every string the UI shows is a translated string, not a key.

Two different failures hide behind "the translations are fine". One is a key
that exists in ``strings.json`` and was never copied to ``translations/en.json``
— the file HA actually loads — so the UI shows ``dsp_active`` where the name
should be. The other is an entity that carries a ``translation_key`` nothing
defines, which fails the same way and only in the frontend, where no unit test
looks.

So this module checks the two files against each other, checks every key the
code declares against the file, and then sets the integration up and reads the
friendly names HA resolved — the last of which is the only check that would
survive someone changing how names are produced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import UNDEFINED
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.eversolo import binary_sensor, button, number, select, switch
from custom_components.eversolo.data import EversoloVisualizationMode

from .helpers import prime_device, setup_integration

COMPONENT_DIR = Path(__file__).parents[1] / "custom_components" / "eversolo"
STRINGS = COMPONENT_DIR / "strings.json"
EN = COMPONENT_DIR / "translations" / "en.json"

# The platforms whose entities are built from a description tuple, paired with
# the section of ``strings.json`` that has to name them.
DESCRIBED_PLATFORMS = (
    ("binary_sensor", binary_sensor.ENTITY_DESCRIPTIONS),
    ("button", button.ENTITY_DESCRIPTIONS),
    ("number", number.ENTITY_DESCRIPTIONS),
    ("select", select.ENTITY_DESCRIPTIONS),
    ("switch", switch.ENTITY_DESCRIPTIONS),
)

# The entities that are their own class rather than a description, so nothing
# iterable declares their key. This list is a convenience, not the net — a new
# standalone class that nobody adds here is caught by
# ``test_every_entity_name_comes_from_the_translations``, which reads the names
# off entities HA has actually set up and does not consult this list at all.
STANDALONE_KEYS = {
    "select": ("visualization",),
    "sensor": ("audio_format",),
    "switch": ("screen",),
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_strings_and_english_translations_are_the_same_file() -> None:
    """``en.json`` is the copy HA loads; a drift between them is invisible."""
    assert _load(EN) == _load(STRINGS)


def test_every_described_entity_has_a_name() -> None:
    """Each platform's descriptions are named in ``strings.json``."""
    entity = _load(STRINGS)["entity"]

    missing = [
        f"{platform}.{description.translation_key}"
        for platform, descriptions in DESCRIBED_PLATFORMS
        for description in descriptions
        if "name" not in entity.get(platform, {}).get(description.translation_key, {})
    ]

    assert not missing


def test_every_standalone_entity_has_a_name() -> None:
    """The hand-written entity classes are named too."""
    entity = _load(STRINGS)["entity"]

    missing = [
        f"{platform}.{key}"
        for platform, keys in STANDALONE_KEYS.items()
        for key in keys
        if "name" not in entity.get(platform, {}).get(key, {})
    ]

    assert not missing


def test_descriptions_carry_a_translation_key_and_no_hardcoded_name() -> None:
    """A ``name=`` on a description silently wins over the translation.

    An unset name is ``UNDEFINED`` rather than ``None`` — ``None`` is the
    meaningful value that makes an entity take the device's own name, which is
    the media player and nothing on these platforms.
    """
    offenders = [
        f"{platform}.{description.key}"
        for platform, descriptions in DESCRIBED_PLATFORMS
        for description in descriptions
        if description.translation_key is None or description.name is not UNDEFINED
    ]

    assert not offenders


def test_visualization_options_are_translated() -> None:
    """The one option list we own — the device supplies all the others."""
    states = _load(STRINGS)["entity"]["select"]["visualization"]["state"]

    assert set(states) == {mode.value for mode in EversoloVisualizationMode}


def test_config_flow_strings_cover_every_outcome() -> None:
    """Every reason and error the flow can produce is worded."""
    config = _load(STRINGS)["config"]

    assert set(config["step"]) == {"user", "reconfigure"}
    assert {"cannot_connect", "unsupported_model"} <= set(config["error"])
    assert {
        "already_configured",
        "wrong_device",
        "reconfigure_successful",
    } <= set(config["abort"])


async def test_every_entity_name_comes_from_the_translations(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Every entity HA set up resolved a name, and it came from the file.

    A missing translation does **not** leave the raw key showing — Home
    Assistant falls back to the description's ``name``, which is now unset on
    every one of them, so the entity ends up with no name of its own and its
    friendly name collapses to the bare device name. Eighteen entities called
    "Eversolo DMP-A8 Gen 2" with colliding ids is the failure to catch, and a
    check for key-shaped text would sail past it.

    So the assertion is positive: each entity's friendly name is the device's
    name plus one of the names in ``strings.json``, and only the media player —
    which *is* the device — carries the device name alone.
    """
    prime_device(aioclient_mock)
    entry = await setup_integration(hass)
    device_name = entry.title

    known = {
        strings["name"]
        for platform in _load(STRINGS)["entity"].values()
        for strings in platform.values()
    }
    resolved = {
        state.entity_id: state.attributes.get("friendly_name")
        for state in hass.states.async_all()
    }
    assert resolved, "the integration set up no entities"

    media_players = [
        entity_id for entity_id in resolved if entity_id.startswith("media_player.")
    ]
    assert len(media_players) == 1
    assert resolved[media_players[0]] == device_name

    prefix = f"{device_name} "
    unresolved = {
        entity_id: name
        for entity_id, name in resolved.items()
        if entity_id not in media_players
        and (
            name is None
            or not name.startswith(prefix)
            or name.removeprefix(prefix) not in known
        )
    }
    assert not unresolved


@pytest.mark.parametrize("path", [STRINGS, EN])
def test_translation_files_are_utf8_json(path: Path) -> None:
    """Both files parse, so a broken one fails here and not at runtime."""
    assert isinstance(_load(path), dict)
