"""Select platform for eversolo — the device's one-of-these controls.

Every list here is fetched from an endpoint that answers with its own setter in
a ``url`` field, and that is the URL written back to; the reason the setter is
never derived from the getter's name is in :class:`EversoloOptionList`. Two
exceptions: output routing lives in ``getInputAndOutputList`` and is written by
``setOutInputList``, and knob colour keeps its own verified path because no
capture of a knob-bearing unit exists to read a ``url`` from.

All of them are slow-tier reads, so a choice is shown at once and then
confirmed by re-reading the tier rather than leaving the UI half a minute
behind the device.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .api import EversoloApiClient
from .coordinator import EversoloConfigEntry, EversoloDataUpdateCoordinator
from .data import (
    EversoloCapabilities,
    EversoloData,
    EversoloOption,
    EversoloOptionList,
    EversoloVisualizationMode,
    read_option_list,
)
from .entity import EversoloEntity, async_add_capability_gated

# ``changVUDisplay``'s only parameter names which visualization to toggle.
_OPEN_TYPE_VU = 0
_OPEN_TYPE_SPECTRUM = 1


@dataclass(frozen=True, kw_only=True)
class EversoloSelectDescription(SelectEntityDescription):
    """A device select: where its options live, and how a choice is written."""

    is_supported: Callable[[EversoloCapabilities], bool]
    read: Callable[[EversoloData], EversoloOptionList]
    write: Callable[
        [EversoloApiClient, EversoloOptionList, EversoloOption], Awaitable[None]
    ]


async def _write_listed(
    client: EversoloApiClient, options: EversoloOptionList, option: EversoloOption
) -> None:
    """Write a choice to the setter the list endpoint named for itself."""
    if options.setter_url is None:
        raise HomeAssistantError("The device did not say where to write this selection")
    await client.async_write_setting(options.setter_url, option.index)


ENTITY_DESCRIPTIONS: tuple[EversoloSelectDescription, ...] = (
    EversoloSelectDescription(
        key="output_routing",
        translation_key="output_routing",
        icon="mdi:export",
        entity_category=EntityCategory.CONFIG,
        is_supported=lambda capabilities: capabilities.has_output_routing,
        read=lambda data: EversoloOptionList.from_outputs(
            data.settings.get("input_output_state")
        ),
        write=lambda client, options, option: client.async_set_output(
            option.index, option.tag
        ),
    ),
    EversoloSelectDescription(
        key="dac_filter",
        translation_key="dac_filter",
        icon="mdi:sine-wave",
        entity_category=EntityCategory.CONFIG,
        is_supported=lambda capabilities: capabilities.has_analog_panel,
        read=read_option_list("dac_filter_state"),
        write=_write_listed,
    ),
    EversoloSelectDescription(
        key="upsampling",
        translation_key="upsampling",
        icon="mdi:chart-timeline-variant",
        entity_category=EntityCategory.CONFIG,
        is_supported=lambda capabilities: capabilities.has_analog_panel,
        read=read_option_list("upsampling_state"),
        write=_write_listed,
    ),
    EversoloSelectDescription(
        key="master_clock",
        translation_key="master_clock",
        icon="mdi:clock-outline",
        entity_category=EntityCategory.CONFIG,
        is_supported=lambda capabilities: capabilities.has_master_clock,
        read=read_option_list("master_clock_state"),
        write=_write_listed,
    ),
    EversoloSelectDescription(
        key="vu_style",
        translation_key="vu_style",
        icon="mdi:gauge-low",
        entity_category=EntityCategory.CONFIG,
        is_supported=lambda capabilities: capabilities.has_vu_style,
        read=read_option_list("vu_mode_state"),
        write=_write_listed,
    ),
    EversoloSelectDescription(
        key="spectrum_style",
        translation_key="spectrum_style",
        icon="mdi:chart-histogram",
        entity_category=EntityCategory.CONFIG,
        is_supported=lambda capabilities: capabilities.has_spectrum_style,
        read=read_option_list("spectrum_mode_state"),
        write=_write_listed,
    ),
    EversoloSelectDescription(
        key="knob_color",
        translation_key="knob_color",
        icon="mdi:palette",
        entity_category=EntityCategory.CONFIG,
        is_supported=lambda capabilities: capabilities.has_knob_color,
        read=read_option_list("knob_color_state"),
        # The one write that does not come off the list response: only the A6
        # has a knob, so there is no capture showing whether that list carries
        # a ``url``, and this path is verified on real hardware. See
        # ``async_set_knob_color``.
        write=lambda client, _options, option: client.async_set_knob_color(
            option.index
        ),
    ),
)


def _build(
    coordinator: EversoloDataUpdateCoordinator, capabilities: EversoloCapabilities
) -> list[SelectEntity]:
    """Build the selects this unit's hardware justifies."""
    selects: list[SelectEntity] = [
        EversoloSelect(coordinator, description)
        for description in ENTITY_DESCRIPTIONS
        if description.is_supported(capabilities)
    ]
    if capabilities.has_visualization:
        selects.append(EversoloVisualizationSelect(coordinator, capabilities))
    return selects


async def async_setup_entry(hass, entry: EversoloConfigEntry, async_add_devices):
    """Set up the Select platform."""
    coordinator = entry.runtime_data

    async_add_capability_gated(
        coordinator,
        async_add_devices,
        lambda capabilities: _build(coordinator, capabilities),
    )


class EversoloSelect(EversoloEntity, SelectEntity):
    """One of the device's option lists."""

    entity_description: EversoloSelectDescription

    def __init__(
        self,
        coordinator: EversoloDataUpdateCoordinator,
        entity_description: EversoloSelectDescription,
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )
        # The choice the device has not confirmed yet; None means "no guess".
        self._expected: str | None = None

    @property
    def _options(self) -> EversoloOptionList:
        """This select's list as the device last reported it."""
        return self.entity_description.read(self.coordinator.data)

    @property
    def options(self) -> list[str]:
        """The labels to offer, empty until the device has listed them."""
        return self._options.titles

    @property
    def current_option(self) -> str | None:
        """The choice that is live, as far as anyone knows."""
        if self._expected is not None:
            return self._expected
        current = self._options.current
        return current.title if current is not None else None

    def _handle_coordinator_update(self) -> None:
        """Take the device's word for it, dropping any guess."""
        self._expected = None
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Write the choice, show it, then confirm it against the device."""
        options = self._options
        chosen = options.by_title(option)
        if chosen is None:
            raise ServiceValidationError(f"{option} is not offered by this device")

        await self.entity_description.write(self.coordinator.client, options, chosen)
        self._expected = option
        self.async_write_ha_state()
        # Every list here is slow-tier, so confirm the write instead of leaving
        # the select on a guess for up to 30 s.
        await self.coordinator.async_refresh_settings()


class EversoloVisualizationSelect(EversoloEntity, SelectEntity):
    """What the front screen shows: nothing, the VU meter, or the spectrum.

    The device has no "set the visualization" call — only ``changVUDisplay``,
    which *toggles* one of the two sides and drops the other. Choosing a mode
    is therefore working out which side to toggle, and Off is toggling
    whichever one is up. This replaces the two blind "Cycle Screen Mode"
    buttons of an earlier design, which could not say what the screen was
    showing and could only step it.

    Two things make that honest rather than hopeful. The write's own reply
    carries both display flags, so the result is known immediately instead of
    a poll later. And the toggle is known to step through an intermediate
    value — from ``-1`` one call only reaches ``0``, which is still not
    showing — so the write repeats while the device says it has not arrived,
    bounded by :data:`_MAX_STEPS`. A unit that never arrives is left reporting
    what it actually says, not what was asked for.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:chart-bell-curve"
    _attr_translation_key = "visualization"

    # Enough for the one intermediate step live testing found, plus a spare.
    # Higher would keep writing to a device that is plainly not listening.
    _MAX_STEPS = 3

    def __init__(
        self,
        coordinator: EversoloDataUpdateCoordinator,
        capabilities: EversoloCapabilities,
    ) -> None:
        """Initialize the visualization select.

        Only the sides this unit's settings tree lists are offered: a unit with
        a VU list and no spectrum list has no spectrum to switch to, and
        offering it would fire three writes at hardware that cannot obey them.
        """
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_visualization"
        self._attr_options = [EversoloVisualizationMode.OFF.value] + [
            mode.value
            for mode, present in (
                (EversoloVisualizationMode.VU_METER, capabilities.has_vu_style),
                (EversoloVisualizationMode.SPECTRUM, capabilities.has_spectrum_style),
            )
            if present
        ]
        # What the last write's reply said, until a poll supersedes it.
        self._expected: EversoloVisualizationMode | None = None

    @property
    def _mode(self) -> EversoloVisualizationMode | None:
        """What the screen is showing, as far as anyone knows."""
        if self._expected is not None:
            return self._expected
        return self.coordinator.data.visualization.mode

    @property
    def current_option(self) -> str | None:
        """The mode as a label, or None while the device has not reported one."""
        mode = self._mode
        return mode.value if mode is not None else None

    def _handle_coordinator_update(self) -> None:
        """Take the device's word for it, dropping any guess."""
        self._expected = None
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Toggle the screen until it shows the chosen mode, or give up saying so."""
        if option not in self.options:
            raise ServiceValidationError(f"{option} is not offered by this device")
        target = EversoloVisualizationMode(option)

        mode = self._mode
        if target is EversoloVisualizationMode.OFF and mode is None:
            # Off is toggling whatever is up, and nothing here knows what that
            # is — a device that never reports the flags would have the VU
            # meter switched *on* by a request to switch the screen off.
            return

        for _ in range(self._MAX_STEPS):
            if mode is target:
                break
            visualization = await self.coordinator.client.async_change_visualization(
                _open_type_for(target, showing=mode)
            )
            mode = visualization.mode

        self._expected = mode
        self.async_write_ha_state()


def _open_type_for(
    target: EversoloVisualizationMode, showing: EversoloVisualizationMode | None
) -> int:
    """Which side to toggle to move from what is showing towards a choice.

    Switching a visualization on toggles its own side. Switching one off has
    no call of its own, so it toggles the side that is up. The caller does not
    ask for Off unless it knows what is showing, so the VU fallback here is
    reached only for a screen already reporting Off, where the loop has
    already stopped.
    """
    if target is EversoloVisualizationMode.SPECTRUM:
        return _OPEN_TYPE_SPECTRUM
    if target is EversoloVisualizationMode.VU_METER:
        return _OPEN_TYPE_VU
    return (
        _OPEN_TYPE_SPECTRUM
        if showing is EversoloVisualizationMode.SPECTRUM
        else _OPEN_TYPE_VU
    )
