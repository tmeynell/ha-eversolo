"""Sensor platform for eversolo — diagnostics about what the DAC is being fed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from .coordinator import EversoloConfigEntry, EversoloDataUpdateCoordinator
from .entity import EversoloEntity


async def async_setup_entry(hass, entry: EversoloConfigEntry, async_add_devices):
    """Set up the Sensor platform."""
    async_add_devices([EversoloAudioFormatSensor(entry.runtime_data)])


class EversoloAudioFormatSensor(EversoloEntity, SensorEntity):
    """The quality of the current stream, as one readable line."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:waveform"
    _attr_translation_key = "audio_format"

    def __init__(self, coordinator: EversoloDataUpdateCoordinator) -> None:
        """Initialize the Audio Format sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_audio_format"

    @property
    def native_value(self) -> str | None:
        """The format summary, e.g. ``PCM 44.1kHz/16bit``."""
        return self.coordinator.data.playback.format_label

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The parts the summary is built from, for templates and history."""
        playback = self.coordinator.data.playback
        return {
            "codec": playback.codec,
            "sample_rate": playback.sample_rate,
            "bit_depth": playback.bit_depth,
            "bitrate": playback.bitrate,
            "channels": playback.channels,
        }
