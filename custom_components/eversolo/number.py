"""Number platform for eversolo — the display's brightness sliders.

The device thinks in an index over its own range (0..255 for both screen and
knob) and reports that range alongside the value. Home Assistant is shown a
percentage instead, because an index means nothing to anyone reading a
dashboard, and because the unit's own settings screen shows a percentage too.

Both are slow-tier reads, so a drag is shown at once and then confirmed by
re-reading the tier rather than leaving the slider half a minute behind.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .api import EversoloApiClient
from .coordinator import EversoloConfigEntry, EversoloDataUpdateCoordinator
from .data import EversoloCapabilities, EversoloData, EversoloLevel
from .entity import EversoloEntity, async_add_capability_gated


@dataclass(frozen=True, kw_only=True)
class EversoloNumberDescription(NumberEntityDescription):
    """A device slider: where its range is read, and how a value is written."""

    is_supported: Callable[[EversoloCapabilities], bool]
    read: Callable[[EversoloData], EversoloLevel]
    write: Callable[[EversoloApiClient, EversoloLevel, int], Awaitable[None]]


# The knob's assumed range, matching the 0..255 scale every other brightness
# slider on this device reports. Only a knob-bearing unit's own ``maxValue``
# supersedes it — see ``EversoloLevel.assuming_maximum``.
KNOB_BRIGHTNESS_MAX = 255


def _slider(key: str) -> Callable[[EversoloData], EversoloLevel]:
    """Read one of the settings tier's slider payloads."""
    return lambda data: EversoloLevel.from_payload(data.settings.get(key))


async def _write_slider(
    client: EversoloApiClient, level: EversoloLevel, index: int
) -> None:
    """Write a value to the setter the slider endpoint named for itself."""
    if level.setter_url is None:
        raise HomeAssistantError("The device did not say where to write this setting")
    await client.async_write_setting(level.setter_url, index)


ENTITY_DESCRIPTIONS: tuple[EversoloNumberDescription, ...] = (
    EversoloNumberDescription(
        key="screen_brightness",
        translation_key="screen_brightness",
        icon="mdi:brightness-6",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        is_supported=lambda capabilities: capabilities.has_screen_brightness,
        read=_slider("screen_brightness"),
        write=_write_slider,
    ),
    EversoloNumberDescription(
        key="knob_brightness",
        translation_key="knob_brightness",
        icon="mdi:knob",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        is_supported=lambda capabilities: capabilities.has_knob,
        # Both halves hedge the same way, for the same reason: only the A6 has
        # a knob, so no capture shows whether ``getKnobBrightness`` reports a
        # range or carries a ``url``. The range and the setter both fall back
        # to an assumed default rather than leaving the slider unreadable and
        # unwritable on the one model that has it. Same reasoning as
        # ``async_set_knob_color``.
        read=lambda data: _slider("knob_brightness")(data).assuming_maximum(
            KNOB_BRIGHTNESS_MAX
        ),
        write=lambda client, _level, index: client.async_set_knob_brightness(index),
    ),
)


async def async_setup_entry(hass, entry: EversoloConfigEntry, async_add_devices):
    """Set up the Number platform."""
    coordinator = entry.runtime_data

    async_add_capability_gated(
        coordinator,
        async_add_devices,
        lambda capabilities: [
            EversoloNumber(coordinator, description)
            for description in ENTITY_DESCRIPTIONS
            if description.is_supported(capabilities)
        ],
    )


class EversoloNumber(EversoloEntity, NumberEntity):
    """One of the device's brightness sliders, shown as a percentage."""

    entity_description: EversoloNumberDescription

    def __init__(
        self,
        coordinator: EversoloDataUpdateCoordinator,
        entity_description: EversoloNumberDescription,
    ) -> None:
        """Initialize the number."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )
        # The value the device has not confirmed yet; None means "no guess".
        self._expected: float | None = None

    @property
    def _level(self) -> EversoloLevel:
        """This slider as the device last reported it."""
        return self.entity_description.read(self.coordinator.data)

    @property
    def native_value(self) -> float | None:
        """Where the slider sits, as far as anyone knows."""
        if self._expected is not None:
            return self._expected
        return self._level.percent

    def _handle_coordinator_update(self) -> None:
        """Take the device's word for it, dropping any guess."""
        self._expected = None
        super()._handle_coordinator_update()

    async def async_set_native_value(self, value: float) -> None:
        """Write the value, show it, then confirm it against the device."""
        level = self._level
        index = level.index_for(value)
        if index is None:
            raise HomeAssistantError(
                "The device has not said what range this setting has"
            )

        await self.entity_description.write(self.coordinator.client, level, index)
        self._expected = value
        self.async_write_ha_state()
        # Both sliders are slow-tier, so confirm the write instead of leaving
        # the number on a guess for up to 30 s.
        await self.coordinator.async_refresh_settings()
