"""Config-flow tests driven through the mocked HTTP seam.

Everything here asserts what the HA user or HA core observes — the flow's
result type, the created entry's ``unique_id``/``data``, and the abort/error
reasons — never the flow's internals.
"""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.const import CONF_ENABLE_MUSICBRAINZ_LOOKUP, DOMAIN

from .helpers import HOST, PORT, fixture_json

NET_MAC = "aa:bb:cc:00:00:01"
UNIQUE_ID = format_mac(NET_MAC)
OTHER_HOST = "192.168.0.61"


def _mock_getmodel(
    aioclient_mock: AiohttpClientMocker,
    host: str = HOST,
    **overrides: object,
) -> None:
    """Prime the seam so ``getModel`` on ``host`` answers with real capture."""
    payload = {**fixture_json("getmodel.json"), **overrides}
    aioclient_mock.get(
        f"http://{host}:{PORT}/ControlCenter/getModel",
        json=payload,
    )


@pytest.fixture(autouse=True)
def _bypass_setup():
    """Stop a successful flow from actually setting the integration up."""
    with patch(
        "custom_components.eversolo.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


def _existing_entry(host: str = HOST) -> MockConfigEntry:
    """Build an entry as the flow would have created it."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: host},
        unique_id=UNIQUE_ID,
    )


async def test_user_flow_creates_entry_anchored_to_net_mac(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Adding by host alone creates a host-only entry keyed to the fixed MAC."""
    _mock_getmodel(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST}
    assert result["result"].unique_id == UNIQUE_ID


async def test_user_form_asks_for_host_only(hass: HomeAssistant) -> None:
    """No port and no credentials fields — the API is unauthenticated on 9529."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    keys = {str(key) for key in result["data_schema"].schema}
    assert keys == {CONF_HOST}


async def test_second_add_of_same_device_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The same net_mac can never be added twice, even from a different host."""
    _existing_entry().add_to_hass(hass)
    _mock_getmodel(aioclient_mock, host=OTHER_HOST)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST}
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_unreachable_host_shows_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refused/timed-out probe is reported as an address problem."""
    aioclient_mock.get(
        f"http://{HOST}:{PORT}/ControlCenter/getModel",
        exc=aiohttp.ClientError("refused"),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unrecognised_model_shows_unsupported_model(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A reachable but non-Eversolo box on 9529 is rejected, not adopted."""
    _mock_getmodel(aioclient_mock, model="Z9X", disModel="Z9X", deviceName="Z9X")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "unsupported_model"}


async def test_device_without_net_mac_is_rejected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No fixed MAC means no stable identity to anchor the entry to."""
    _mock_getmodel(aioclient_mock, net_mac="")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "unsupported_model"}


async def test_cannot_connect_is_recoverable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """After a failed probe the user can correct the host and still succeed."""
    aioclient_mock.get(
        f"http://{OTHER_HOST}:{PORT}/ControlCenter/getModel",
        exc=aiohttp.ClientError("refused"),
    )
    _mock_getmodel(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST}


async def test_reconfigure_follows_device_to_new_host(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A DHCP move updates the host in place, keeping the entry and its id."""
    entry = _existing_entry()
    entry.add_to_hass(hass)
    _mock_getmodel(aioclient_mock, host=OTHER_HOST)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is data_entry_flow.FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST}
    )
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_HOST: OTHER_HOST}
    assert entry.unique_id == UNIQUE_ID


async def test_reconfigure_refuses_a_different_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Pointing reconfigure at another A8 aborts instead of adopting it."""
    entry = _existing_entry()
    entry.add_to_hass(hass)
    _mock_getmodel(aioclient_mock, host=OTHER_HOST, net_mac="aa:bb:cc:00:00:03")

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST}
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert entry.data == {CONF_HOST: HOST}


async def test_reconfigure_unreachable_host_shows_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A wrong address in reconfigure re-prompts rather than stranding the entry."""
    entry = _existing_entry()
    entry.add_to_hass(hass)
    aioclient_mock.get(
        f"http://{OTHER_HOST}:{PORT}/ControlCenter/getModel",
        exc=aiohttp.ClientError("refused"),
    )

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST}
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data == {CONF_HOST: HOST}


async def test_reconfigure_adopts_an_entry_that_has_no_identity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A legacy entry with no unique_id gains one instead of being stranded."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, unique_id=None)
    entry.add_to_hass(hass)
    _mock_getmodel(aioclient_mock, host=OTHER_HOST)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST}
    )
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == UNIQUE_ID
    assert entry.data == {CONF_HOST: OTHER_HOST}


async def test_no_reauth_step(hass: HomeAssistant) -> None:
    """There are no credentials to renew."""
    handler = config_entries.HANDLERS[DOMAIN]

    assert not hasattr(handler, "async_step_reauth")


async def test_options_flow_defaults_musicbrainz_lookup_off(
    hass: HomeAssistant,
) -> None:
    """The one option this integration has starts disabled (#18)."""
    entry = _existing_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"
    schema = result["data_schema"].schema
    (default,) = (
        field.default() for field in schema if field == CONF_ENABLE_MUSICBRAINZ_LOOKUP
    )
    assert default is False


async def test_options_flow_saves_the_musicbrainz_lookup_toggle(
    hass: HomeAssistant,
) -> None:
    """Enabling the toggle persists it onto the entry's options."""
    entry = _existing_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ENABLE_MUSICBRAINZ_LOOKUP: True}
    )
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_ENABLE_MUSICBRAINZ_LOOKUP: True}


async def test_options_flow_reopens_showing_what_was_saved(
    hass: HomeAssistant,
) -> None:
    """The form suggests the entry's current option, not the bare default."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: HOST},
        unique_id=UNIQUE_ID,
        options={CONF_ENABLE_MUSICBRAINZ_LOOKUP: True},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    schema = result["data_schema"].schema
    (suggested,) = (
        field.description["suggested_value"]
        for field in schema
        if field == CONF_ENABLE_MUSICBRAINZ_LOOKUP
    )
    assert suggested is True
