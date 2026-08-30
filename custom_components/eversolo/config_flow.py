"""Config flow for Eversolo.

Setup is a single question — the device's address — because the port-9529 API is
fixed and unauthenticated. One ``getModel`` round-trip does all the work: it
proves something is answering on 9529, sanity-checks the model string, and
yields the fixed ``net_mac`` the entry is anchored to. Anchoring to that named
field (rather than whatever MAC the active link uses) keeps the identity stable
as the unit moves between its wired, WiFi, and SFP interfaces.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.components import ssdp
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.ssdp import (
    ATTR_UPNP_MANUFACTURER,
    SsdpServiceInfo,
)

from .api import EversoloApiClient, EversoloApiClientCommunicationError
from .const import CONF_ENABLE_MUSICBRAINZ_LOOKUP, DEFAULT_PORT, DOMAIN, LOGGER
from .data import EversoloDevice

CONF_DEVICE = "device"

STEP_DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): cv.string})

OPTIONS_SCHEMA = vol.Schema(
    {vol.Optional(CONF_ENABLE_MUSICBRAINZ_LOOKUP, default=False): cv.boolean}
)

# The manual-add form: the host field plus the same MusicBrainz opt-in the
# Options flow offers post-setup, so a user can turn it on in one trip instead
# of a separate visit through Settings → Options afterward.
STEP_USER_DATA_SCHEMA = STEP_DATA_SCHEMA.extend(OPTIONS_SCHEMA.schema)

# Same manufacturer/deviceType pair the manifest's passive matcher requires
# (#24) — the discovery-cache lookup this module runs itself reuses it so a
# picked device passes through the identical admission rule as one found
# passively.
SSDP_MANUFACTURER = "EVERSOLO"
SSDP_DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaRenderer:1"

# Eversolo's DMP-A line — the DMP-A8 (Gen 1/2) this integration targets, plus the
# other A-series models the capability gates still serve with a reduced entity
# set. A box that doesn't call itself a DMP-A is rejected outright: anything else
# answering on 9529 is some other Zidoo-lineage device that would only produce a
# broken entry.
SUPPORTED_MODEL_PREFIX = "DMP-A"


def _user_schema(candidates: dict[str, str]) -> vol.Schema:
    """Build the manual-add schema, adding a picker when a search found hits.

    With no candidates this is exactly ``STEP_USER_DATA_SCHEMA`` — the host
    field stays required, unchanged from before the picker existed. With
    candidates, the host field becomes optional because picking a device
    supplies it instead; ``async_step_user`` requires one or the other be
    given.
    """
    if not candidates:
        return STEP_USER_DATA_SCHEMA
    return vol.Schema(
        {
            vol.Optional(CONF_DEVICE): vol.In(candidates),
            vol.Optional(CONF_HOST): cv.string,
        }
    ).extend(OPTIONS_SCHEMA.schema)


def _is_supported(device: EversoloDevice) -> bool:
    """Report whether ``getModel`` names an Eversolo DMP with a usable id.

    Both halves are admission requirements: a box that doesn't call itself a DMP
    isn't this integration's device, and one that won't hand back the fixed
    ``net_mac`` has no identity to anchor an entry to.
    """
    return bool(device.net_mac) and (device.model or "").strip().upper().startswith(
        SUPPORTED_MODEL_PREFIX
    )


class EversoloFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle the manual-IP setup and reconfigure flows."""

    VERSION = 3

    _discovered_host: str
    _discovered_name: str | None
    # Populated once per flow instance by the discovery-cache lookup in
    # ``async_step_user``, host -> label ("DMP-A8 Gen 2 (192.168.0.63)"). Kept
    # across re-renders of the same form (a validation error, say) so it
    # isn't looked up again just to redraw the picker.
    _discovered_candidates: dict[str, str]

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> EversoloOptionsFlowHandler:
        """Hand back the options flow for an already-configured entry."""
        return EversoloOptionsFlowHandler()

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Add a device by address, or by picking one already-discovered.

        Looked up once, on the form's first render (``_async_discover_candidates``):
        a device found and confirmed there is offered as a pick alongside the
        host field, so selecting it skips typing an IP; a network with none
        (or one HA's SSDP cache hasn't caught up to yet) falls straight back
        to a bare host field — no regression to that path. A second render of
        this same form (a validation error, say) reuses that first lookup's
        results rather than repeating it.
        """
        errors: dict[str, str] = {}

        if user_input is None:
            self._discovered_candidates = await self._async_discover_candidates()
        else:
            host = user_input.get(CONF_DEVICE) or user_input.get(CONF_HOST)
            device, errors = (
                await self._async_probe(host)
                if host
                else (None, {"base": "host_required"})
            )
            if device is not None:
                await self.async_set_unique_id(format_mac(device.net_mac))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=device.display_title,
                    data={CONF_HOST: host},
                    options={
                        CONF_ENABLE_MUSICBRAINZ_LOOKUP: user_input[
                            CONF_ENABLE_MUSICBRAINZ_LOOKUP
                        ]
                    },
                )

        return self._show_host_form(
            "user", _user_schema(self._discovered_candidates), user_input, errors
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Follow the same device to a new address.

        This is "my A8 moved," never "adopt a different box": the probed
        ``net_mac`` must match the entry's, or the flow aborts and the entry is
        left pointing where it was. An entry that has no identity yet — a
        legacy entry whose migration could not reach the device — adopts the
        probed one instead of being stranded.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            device, errors = await self._async_probe(user_input[CONF_HOST])
            if device is not None:
                await self.async_set_unique_id(format_mac(device.net_mac))
                if entry.unique_id is not None:
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=self.unique_id,
                    data_updates={CONF_HOST: user_input[CONF_HOST]},
                )

        return self._show_host_form(
            "reconfigure", STEP_DATA_SCHEMA, user_input or entry.data, errors
        )

    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> ConfigFlowResult:
        """Adopt a device the manifest's SSDP matcher found.

        The matcher only proves a Platinum/Plutinosoft UPnP MediaRenderer
        answered — that embedded SDK isn't Eversolo-specific, so the same
        ``getModel`` admission check ``async_step_user`` applies is what
        actually confirms an Eversolo DMP is behind it. No entry is created
        from the SSDP identity alone, nor from this step at all — a first
        sighting only leads to the confirm form; ``async_step_ssdp_confirm``
        is what creates the entry.
        """
        location = discovery_info.ssdp_location
        host = urlparse(location).hostname if location else None
        if host is None:
            return self.async_abort(reason="cannot_connect")

        device, errors = await self._async_probe(host)
        if device is None:
            return self.async_abort(reason=errors["base"])

        await self.async_set_unique_id(format_mac(device.net_mac))
        # Unlike the manual-entry path, SSDP re-fires on its own whenever the
        # device is up — the exact "moved between wired/WiFi/SFP" case this
        # module's docstring anchors on net_mac for. Passing the freshly
        # discovered host here lets that rediscovery heal a stale entry
        # instead of just reporting it configured and leaving it stranded.
        # This is an update to an existing entry, not a new admission, so it
        # stays automatic — only a device with no entry yet reaches the
        # confirm step below.
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._discovered_host = host
        self._discovered_name = device.name
        # ``device.name`` is the unit's own configurable name, falling back to
        # its model when unset (``EversoloDevice.from_model``) — using it here
        # rather than the bare model means a renamed unit shows its real name
        # on the discovery card and as the suggested entry title, not a
        # generic "Eversolo DMP-A8 Gen 2" indistinguishable from any other.
        self.context["title_placeholders"] = {"name": device.name}
        return await self.async_step_ssdp_confirm()

    async def async_step_ssdp_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before adopting a device newly found by SSDP.

        ``async_step_ssdp`` has already probed and validated the device by
        the time this runs — this step only gates entry creation on the
        user's say-so, so a device that's still powered on can't recreate an
        entry the user just deleted.
        """
        if user_input is not None:
            return self.async_create_entry(
                title=EversoloDevice(name=self._discovered_name).display_title,
                data={CONF_HOST: self._discovered_host},
                options={
                    CONF_ENABLE_MUSICBRAINZ_LOOKUP: user_input[
                        CONF_ENABLE_MUSICBRAINZ_LOOKUP
                    ]
                },
            )

        return self.async_show_form(
            step_id="ssdp_confirm",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, user_input or {}
            ),
            description_placeholders={"name": self._discovered_name},
        )

    @callback
    def _show_host_form(
        self,
        step_id: str,
        schema: vol.Schema,
        suggested: dict[str, Any] | None,
        errors: dict[str, str],
    ) -> ConfigFlowResult:
        """Show a host form, keeping whatever the user last typed."""
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested or {}),
            errors=errors,
        )

    async def _async_discover_candidates(self) -> dict[str, str]:
        """Look up Eversolo devices to offer as picks.

        Reads Home Assistant's own SSDP discovery cache — kept warm by both
        passive listening and the ``ssdp`` integration's own periodic active
        scans — rather than sending a fresh broadcast of this module's own,
        so a device that appeared moments before the form was opened may not
        show up yet; that is the same "none found" outcome as a quiet network,
        and both fall back to the bare host field.

        Filters the hits to the same manufacturer/deviceType pair the passive
        flow's manifest matcher requires, then confirms each with the same
        ``getModel`` admission check (``_async_probe``, which applies
        ``_is_supported``) the passive flow applies — a hit that only
        emulates the platform's UPnP MediaRenderer is filtered out here
        exactly as it would be there. Returns host -> label, e.g.
        ``{"192.168.0.63": "DMP-A8 Gen 2 (192.168.0.63)"}``.

        A ``hass`` with the ``ssdp`` integration not set up (never the case
        once any loaded integration's manifest declares an ``ssdp`` matcher,
        as this one's does, but true of a bare test ``hass``) has nothing to
        read — that is likewise treated as an empty result rather than an
        error.

        Candidates are probed concurrently, and a candidate whose probe fails
        for any reason — including a bug in that one device's response, which
        ``_async_probe`` deliberately does not catch (api.py) — is dropped
        rather than allowed to blow up the whole "Add Integration" flow for
        every device on the network.
        """
        if ssdp.DOMAIN not in self.hass.data:
            return {}

        discovered = await ssdp.async_get_discovery_info_by_st(
            self.hass, SSDP_DEVICE_TYPE
        )

        hosts: list[str] = []
        for info in discovered:
            if info.upnp.get(ATTR_UPNP_MANUFACTURER) != SSDP_MANUFACTURER:
                continue
            host = urlparse(info.ssdp_location).hostname if info.ssdp_location else None
            if host and host not in hosts:
                hosts.append(host)

        devices = await asyncio.gather(
            *(self._async_probe_candidate(host) for host in hosts)
        )
        return {
            host: f"{device.name} ({host})"
            for host, device in zip(hosts, devices, strict=True)
            if device is not None
        }

    async def _async_probe_candidate(self, host: str) -> EversoloDevice | None:
        """Probe one discovered host, swallowing any failure as "not a pick".

        Unlike the host a user types themselves, a candidate here was never
        asked for — it only showed up because something on the LAN answered
        SSDP's manufacturer/deviceType pair. A bug in that one device's
        response (the case ``_async_probe`` deliberately lets propagate,
        api.py) should drop it from the picker, not break the form for every
        other device found alongside it.
        """
        try:
            device, _errors = await self._async_probe(host)
        except Exception:  # noqa: BLE001
            LOGGER.debug(
                "Discovered candidate %s failed its probe", host, exc_info=True
            )
            return None
        return device

    async def _async_probe(
        self, host: str
    ) -> tuple[EversoloDevice | None, dict[str, str]]:
        """Read ``getModel`` from ``host`` and confirm it is a supported unit.

        Returns the device, or ``None`` plus the form errors explaining why the
        address can't be used.
        """
        client = EversoloApiClient(
            host=host,
            port=DEFAULT_PORT,
            session=async_get_clientsession(self.hass),
        )

        try:
            device = await client.async_read_device()
        except EversoloApiClientCommunicationError as exception:
            LOGGER.debug(
                "No Eversolo answered at %s:%s: %s", host, DEFAULT_PORT, exception
            )
            return None, {"base": "cannot_connect"}

        if not _is_supported(device):
            LOGGER.debug(
                "Unsupported device at %s: model=%s, net_mac=%s",
                host,
                device.model,
                device.net_mac,
            )
            return None, {"base": "unsupported_model"}

        return device, {}


class EversoloOptionsFlowHandler(OptionsFlow):
    """The one option this integration has: the MusicBrainz cover-art lookup.

    Off by default (#18) — enabling it is a real behaviour change from the
    LAN-only polling model every other read in this integration keeps to, so
    it is a deliberate opt-in rather than a silent default.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show, and save, the single MusicBrainz opt-in toggle."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )
