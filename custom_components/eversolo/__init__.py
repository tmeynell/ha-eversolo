"""Eversolo integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac

from .api import EversoloApiClient, EversoloApiClientError
from .const import DEFAULT_PORT, LOGGER, NAME
from .coordinator import EversoloConfigEntry, EversoloDataUpdateCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to a new version."""
    LOGGER.debug("Migrating from version %s", entry.version)

    if entry.version == 1:
        # Version 2: Update title from host IP to "Eversolo {model}" for proper
        # device naming with _attr_has_entity_name = True.
        title = NAME
        try:
            client = EversoloApiClient(
                host=entry.data[CONF_HOST],
                port=DEFAULT_PORT,
                session=async_get_clientsession(hass),
            )
            device_info = await client.async_get_device_model()
            model = device_info.get("model")
            if model:
                title = f"{NAME} {model}"
        except Exception as exception:
            LOGGER.warning(
                "Could not fetch device model during migration, "
                "using default title: %s",
                exception,
            )

        hass.config_entries.async_update_entry(entry, title=title, version=2)
        LOGGER.info("Migration to version 2 successful, title set to '%s'", title)

    if entry.version == 2:
        # Version 3: identity moves out of entry data. The entry is anchored to
        # the device's fixed net_mac as unique_id and stores the host alone —
        # the port is fixed at 9529 and everything else is re-derived at setup.
        unique_id = entry.unique_id
        if unique_id is None:
            unique_id = await _async_read_unique_id(hass, entry.data[CONF_HOST])

        hass.config_entries.async_update_entry(
            entry,
            data={CONF_HOST: entry.data[CONF_HOST]},
            unique_id=unique_id,
            version=3,
        )
        LOGGER.info("Migration to version 3 successful, unique_id is %s", unique_id)

    return True


async def _async_read_unique_id(hass: HomeAssistant, host: str) -> str | None:
    """Read the device's fixed net_mac to anchor a legacy entry to.

    Best-effort: a device that is offline (or has moved) leaves the entry
    without an identity, which the reconfigure flow then adopts.
    """
    try:
        client = EversoloApiClient(
            host=host,
            port=DEFAULT_PORT,
            session=async_get_clientsession(hass),
        )
        device = await client.async_read_device()
    except EversoloApiClientError as exception:
        LOGGER.warning(
            "Could not read the device identity during migration; "
            "reconfigure the entry once the device is reachable: %s",
            exception,
        )
        return None

    return format_mac(device.net_mac) if device.net_mac else None


async def async_setup_entry(hass: HomeAssistant, entry: EversoloConfigEntry) -> bool:
    """Set up this integration using UI."""
    coordinator = EversoloDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        client=EversoloApiClient(
            # Port is fixed by design: the integration is 9529-only, so entry
            # data carries the host alone.
            host=entry.data[CONF_HOST],
            port=DEFAULT_PORT,
            session=async_get_clientsession(hass),
        ),
    )

    # A unit that is off keeps its entry: entities either wait for the device
    # to report (the capability-gated ones) or sit unavailable until it does,
    # so a failed setup would only cost the user a manual reload later.
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        LOGGER.info("Eversolo device is offline, integration set up will continue")

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: EversoloConfigEntry) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: EversoloConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
