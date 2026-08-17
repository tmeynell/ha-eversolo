"""Button platform for eversolo — the three power actions.

All three are gated on what the unit says it accepts (``ableRemoteReboot`` /
``ableRemoteShutdown`` / ``ableRemoteBoot``). Power On sends a Wake-on-LAN
magic packet rather than a device command — the app's only wake mechanism
(see ``.wake_on_lan``) — which only became worth shipping once the unit moved
to Ethernet and #10's F5 proved the packet actually wakes it; ``getModel``'s
``ableRemoteSleep`` flag, which an earlier version of this docstring cited to
rule Wake-on-LAN out, is about sleep, not boot, and was never the right gate.

Nothing else is a button. The screen and its visualizations were five buttons
in an earlier design, which could only step the screen blindly and never say
what it was showing; they are a switch, a number and a select now.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory

from .coordinator import EversoloConfigEntry, EversoloDataUpdateCoordinator
from .data import EversoloCapabilities
from .entity import EversoloEntity, async_add_capability_gated


@dataclass(frozen=True, kw_only=True)
class EversoloButtonDescription(ButtonEntityDescription):
    """A device action: whether this unit accepts it, and what it sends.

    ``is_supported`` has no default on purpose: a gate that could be left off
    would ship a button unconditionally, which is the one thing this class
    exists to prevent.
    """

    is_supported: Callable[[EversoloCapabilities], bool]
    press: Callable[[EversoloDataUpdateCoordinator], Awaitable[None]]


ENTITY_DESCRIPTIONS: tuple[EversoloButtonDescription, ...] = (
    EversoloButtonDescription(
        key="reboot",
        translation_key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        is_supported=lambda capabilities: capabilities.has_reboot,
        press=lambda coordinator: coordinator.client.async_trigger_reboot(),
    ),
    EversoloButtonDescription(
        key="power_off",
        translation_key="power_off",
        icon="mdi:power-off",
        entity_category=EntityCategory.CONFIG,
        is_supported=lambda capabilities: capabilities.has_power_off,
        press=lambda coordinator: coordinator.client.async_trigger_power_off(),
    ),
    EversoloButtonDescription(
        key="power_on",
        translation_key="power_on",
        icon="mdi:power-on",
        entity_category=EntityCategory.CONFIG,
        is_supported=lambda capabilities: capabilities.has_power_on,
        # Wake-on-LAN, not a device command — the unit cannot answer an HTTP
        # request while off, which is the one state this button is for.
        press=lambda coordinator: coordinator.async_wake(),
    ),
)


async def async_setup_entry(hass, entry: EversoloConfigEntry, async_add_devices):
    """Set up the Button platform."""
    coordinator = entry.runtime_data

    async_add_capability_gated(
        coordinator,
        async_add_devices,
        lambda capabilities: [
            EversoloButton(coordinator, description)
            for description in ENTITY_DESCRIPTIONS
            if description.is_supported(capabilities)
        ],
    )


class EversoloButton(EversoloEntity, ButtonEntity):
    """One of the device's power actions."""

    entity_description: EversoloButtonDescription

    def __init__(
        self,
        coordinator: EversoloDataUpdateCoordinator,
        entity_description: EversoloButtonDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )

    async def async_press(self) -> None:
        """Send the action this button stands for."""
        await self.entity_description.press(self.coordinator)
