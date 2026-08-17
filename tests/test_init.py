"""Entry-migration tests: what a legacy entry looks like after upgrading."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.const import DOMAIN

from .helpers import HOST, PORT, fixture_json

UNIQUE_ID = format_mac("aa:bb:cc:00:00:01")


@pytest.fixture(autouse=True)
def _bypass_setup():
    """Run migration for real, but stop short of setting the platforms up."""
    with patch("custom_components.eversolo.async_setup_entry", return_value=True):
        yield


def _legacy_entry() -> MockConfigEntry:
    """Build the legacy entry shape: host + port, no identity."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={CONF_HOST: HOST, CONF_PORT: PORT},
        unique_id=None,
    )


async def _migrate(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_legacy_entry_gains_its_hardware_identity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Upgrading a reachable device anchors the entry and drops the port."""
    aioclient_mock.get(
        f"http://{HOST}:{PORT}/ControlCenter/getModel",
        json=fixture_json("getmodel.json"),
    )
    entry = _legacy_entry()

    await _migrate(hass, entry)

    assert entry.version == 3
    assert entry.unique_id == UNIQUE_ID
    assert entry.data == {CONF_HOST: HOST}


async def test_legacy_entry_migrates_while_device_is_offline(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unreachable device still migrates; identity waits for a reconfigure."""
    aioclient_mock.get(
        f"http://{HOST}:{PORT}/ControlCenter/getModel",
        exc=aiohttp.ClientError("offline"),
    )
    entry = _legacy_entry()

    await _migrate(hass, entry)

    assert entry.version == 3
    assert entry.unique_id is None
    assert entry.data == {CONF_HOST: HOST}
