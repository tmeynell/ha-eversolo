"""Sensor platform for eversolo — diagnostics about what the DAC is being fed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from .coordinator import EversoloConfigEntry, EversoloDataUpdateCoordinator
from .entity import EversoloEntity


async def async_setup_entry(hass, entry: EversoloConfigEntry, async_add_devices):
    """Set up the Sensor platform."""
    async_add_devices(
        [
            EversoloAudioFormatSensor(entry.runtime_data),
            EversoloInputSensor(entry.runtime_data),
        ]
    )


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


class EversoloInputSensor(EversoloEntity, SensorEntity):
    """Which input ``getState`` says is live, with the device's own icon.

    #16: exists to give ``volumeData.intputIcon`` an ``entity_picture`` to
    live on. The media player already models "current input" as its own
    ``source``/``source_list``, but its ``entity_picture`` is already the
    now-playing art (``media_image_url``) — the two icons are different
    signals, so this stands beside it rather than fighting over one slot.
    """

    _attr_translation_key = "input"

    def __init__(self, coordinator: EversoloDataUpdateCoordinator) -> None:
        """Initialize the Input sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_input"

    @property
    def native_value(self) -> str | None:
        """The input ``getState`` says is live right now.

        ``EversoloData.live_input_name``, not the settings-tier
        ``inputs.current`` the source select reads — the *tag* this names
        comes off the same ``volumeData`` block ``intputIcon`` does, so the
        two are never describing two different inputs. Resolving that tag to
        a label is a separate step that waits on the settings-tier input
        list, though (see ``live_input_name``'s own docstring), which is why
        ``entity_picture`` below still has its own guard.
        """
        return self.coordinator.data.live_input_name

    @property
    def entity_picture(self) -> str | None:
        """The device's own icon for the live input, or None until it says.

        Withheld while ``native_value`` is still unresolved even if
        ``input_icon`` itself has arrived — the two ride different tiers
        (``input_icon`` is live-tier, ``native_value`` waits on the
        settings-tier input list, see its own docstring), and a picture with
        no name to go with it is the mismatch this entity exists to avoid.
        """
        if self.native_value is None:
            return None
        return self.coordinator.client.create_image_url_or_none(
            self.coordinator.data.volume.input_icon
        )
