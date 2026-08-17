"""Binary sensor platform for eversolo — whether DSP or EQ is engaged right now.

Both readings come out of ``getState``, which is the live tier, so they go
unavailable with the media player rather than soft-keeping a stale value.

**Neither is a global switch.** The device holds a separate profile assignment
and enable flag per source, and ``getState`` reports the one belonging to
whatever is selected: DSP follows the selected *input*, EQ the digital
*outputs*. Changing source can flip DSP Active with nothing else having
changed — which is why the sensor also names the input its reading is about.

Read-only. Choosing a profile or editing its bands is not exposed here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .coordinator import EversoloConfigEntry, EversoloDataUpdateCoordinator
from .data import EversoloCapabilities, EversoloData
from .entity import EversoloEntity, async_add_capability_gated


@dataclass(frozen=True, kw_only=True)
class EversoloBinarySensorDescription(BinarySensorEntityDescription):
    """A read-only device flag: what gates it, and where its state is read."""

    is_supported: Callable[[EversoloCapabilities], bool]
    read: Callable[[EversoloData], bool | None]
    attributes: Callable[[EversoloData], dict[str, Any]] | None = None


ENTITY_DESCRIPTIONS: tuple[EversoloBinarySensorDescription, ...] = (
    EversoloBinarySensorDescription(
        key="dsp_active",
        translation_key="dsp_active",
        icon="mdi:tune-vertical-variant",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_supported=lambda capabilities: capabilities.has_dsp,
        read=lambda data: data.processing.dsp_active,
        # Named in the UI rather than only in the docs: a user who reads this
        # as a global "DSP is on" will call correct behaviour a bug the first
        # time changing source flips it. Both come off the same payload, so
        # the input named is the one the reading is about.
        attributes=lambda data: {"input": data.live_input_name},
    ),
    EversoloBinarySensorDescription(
        key="eq_active",
        translation_key="eq_active",
        icon="mdi:equalizer",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_supported=lambda capabilities: capabilities.has_eq,
        read=lambda data: data.processing.eq_active,
    ),
)


async def async_setup_entry(hass, entry: EversoloConfigEntry, async_add_devices):
    """Set up the Binary Sensor platform."""
    coordinator = entry.runtime_data

    async_add_capability_gated(
        coordinator,
        async_add_devices,
        lambda capabilities: [
            EversoloBinarySensor(coordinator, description)
            for description in ENTITY_DESCRIPTIONS
            if description.is_supported(capabilities)
        ],
    )


class EversoloBinarySensor(EversoloEntity, BinarySensorEntity):
    """One of the device's read-only processing flags."""

    entity_description: EversoloBinarySensorDescription

    def __init__(
        self,
        coordinator: EversoloDataUpdateCoordinator,
        entity_description: EversoloBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )

    @property
    def is_on(self) -> bool | None:
        """Whether the device is applying this processing to what is selected."""
        return self.entity_description.read(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """What the reading is scoped to, where the flag has a scope to state."""
        if self.entity_description.attributes is None:
            return None
        return self.entity_description.attributes(self.coordinator.data)
