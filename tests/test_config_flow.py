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
from homeassistant.helpers.service_info.ssdp import (
    ATTR_UPNP_DEVICE_TYPE,
    ATTR_UPNP_MANUFACTURER,
    SsdpServiceInfo,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eversolo.const import CONF_ENABLE_MUSICBRAINZ_LOOKUP, DOMAIN

from .helpers import HOST, PORT, fixture_json

NET_MAC = "aa:bb:cc:00:00:01"
UNIQUE_ID = format_mac(NET_MAC)
OTHER_HOST = "192.168.0.61"

# What the manifest's ssdp matcher requires — from the A8's actual
# description.xml <manufacturer> tag, confirmed against the real device (#19
# regression: "Plutinosoft LLC" was a guess from modelDescription/modelURL
# and never matched real SSDP traffic).
SSDP_MANUFACTURER = "EVERSOLO"
SSDP_DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaRenderer:1"


def _ssdp_discovery(host: str = HOST) -> SsdpServiceInfo:
    """Build the discovery payload HA's ssdp integration would hand the flow."""
    return SsdpServiceInfo(
        ssdp_usn=f"uuid:{host}::urn:schemas-upnp-org:device:MediaRenderer:1",
        ssdp_st=SSDP_DEVICE_TYPE,
        ssdp_location=f"http://{host}:1900/description.xml",
        upnp={
            ATTR_UPNP_MANUFACTURER: SSDP_MANUFACTURER,
            ATTR_UPNP_DEVICE_TYPE: SSDP_DEVICE_TYPE,
        },
    )


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


async def test_user_form_asks_for_host_and_musicbrainz_toggle(
    hass: HomeAssistant,
) -> None:
    """No port and no credentials fields — the API is unauthenticated on 9529."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    keys = {str(key) for key in result["data_schema"].schema}
    assert keys == {CONF_HOST, CONF_ENABLE_MUSICBRAINZ_LOOKUP}


async def test_user_flow_enables_musicbrainz_lookup_when_checked(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Checking the toggle during add creates the entry with it already on."""
    _mock_getmodel(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_ENABLE_MUSICBRAINZ_LOOKUP: True},
    )
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST}
    assert result["options"] == {CONF_ENABLE_MUSICBRAINZ_LOOKUP: True}


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


async def test_ssdp_discovery_of_a_new_device_shows_confirm_form(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A real Eversolo answering the manifest's SSDP matcher is not auto-added.

    The SSDP identity alone (Platinum/Plutinosoft, generic MediaRenderer) is
    not enough — the flow still hits ``getModel`` on 9529 to identify the
    device, same anchor as the manual path (#19) — but first sightings stop
    at a confirmation form rather than creating the entry outright (#63).
    """
    _mock_getmodel(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=_ssdp_discovery(),
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "ssdp_confirm"
    assert result["description_placeholders"] == {"model": "DMP-A8 Gen 2"}


async def test_confirming_ssdp_discovery_creates_the_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Submitting the confirm form creates the entry, same shape as before."""
    _mock_getmodel(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=_ssdp_discovery(),
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST}
    assert result["result"].unique_id == UNIQUE_ID


async def test_declining_ssdp_confirm_creates_no_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Aborting the confirm form leaves no entry behind."""
    _mock_getmodel(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=_ssdp_discovery(),
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM

    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_ssdp_discovery_of_a_non_eversolo_device_is_rejected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The Platinum SDK is generic — a non-Eversolo hit fails admission.

    Manufacturer/deviceType alone can't tell an Eversolo apart from any other
    Zidoo-lineage box embedding the same UPnP SDK; ``getModel`` rejecting the
    model string aborts the flow instead of creating a broken entry.
    """
    _mock_getmodel(aioclient_mock, model="Z9X", disModel="Z9X", deviceName="Z9X")

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=_ssdp_discovery(),
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "unsupported_model"


async def test_ssdp_discovery_of_an_unreachable_host_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A discovered address that refuses the ``getModel`` probe aborts too."""
    aioclient_mock.get(
        f"http://{HOST}:{PORT}/ControlCenter/getModel",
        exc=aiohttp.ClientError("refused"),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=_ssdp_discovery(),
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_ssdp_discovery_of_an_already_configured_device_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The same net_mac discovered again does not duplicate the entry."""
    _existing_entry().add_to_hass(hass)
    _mock_getmodel(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=_ssdp_discovery(),
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_ssdp_rediscovery_at_a_new_host_heals_the_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A configured device rediscovered at a new address updates in place.

    Unlike a manual add, SSDP fires on its own whenever the unit is up — the
    same "moved between wired/WiFi/SFP" case ``net_mac`` anchoring exists
    for. Rediscovery should heal a stale host, not just report it configured.
    """
    entry = _existing_entry()
    entry.add_to_hass(hass)
    _mock_getmodel(aioclient_mock, host=OTHER_HOST)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=_ssdp_discovery(host=OTHER_HOST),
    )
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {CONF_HOST: OTHER_HOST}


async def test_ssdp_discovery_without_a_location_aborts(hass: HomeAssistant) -> None:
    """A malformed discovery with no address to probe can't be adopted."""
    discovery_info = _ssdp_discovery()
    discovery_info = SsdpServiceInfo(
        ssdp_usn=discovery_info.ssdp_usn,
        ssdp_st=discovery_info.ssdp_st,
        ssdp_location=None,
        upnp=discovery_info.upnp,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=discovery_info,
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


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
