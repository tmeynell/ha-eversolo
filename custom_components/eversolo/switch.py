"""Switch platform for eversolo — the device's ``?switch=`` toggles.

These live in the ``getSystemSettings`` tree — or, for the subwoofer, in a
sub-page that tree points at — which is both where the device says a toggle
exists and where it reports whether it is on. There is no per-toggle getter, so
the tree is polled on the settings tier and each write is shown optimistically
and then confirmed by re-reading it.

``?switch=1`` is on and ``?switch=0`` is off — the desired state, sent
directly. The vendor's app appends ``state ^ 1`` instead, but its callers hand
it the *current* state, so that XOR is how it toggles, not the wire encoding.
Reproducing it here would invert every switch on this platform.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .api import EversoloApiClient
from .const import (
    SETTING_TAG_CD_AUTO_PLAY,
    SETTING_TAG_EOS_ENGINE,
    SETTING_TAG_GAPLESS,
    SETTING_TAG_SUBWOOFER_SWITCH,
)
from .coordinator import EversoloConfigEntry, EversoloDataUpdateCoordinator
from .data import EversoloCapabilities
from .entity import EversoloEntity, async_add_capability_gated


@dataclass(frozen=True, kw_only=True)
class EversoloSwitchDescription(SwitchEntityDescription):
    """A device toggle: where its state is read, and how it is written."""

    setting_tag: str
    is_supported: Callable[[EversoloCapabilities], bool]
    write: Callable[[EversoloApiClient, bool], Awaitable[None]]


ENTITY_DESCRIPTIONS: tuple[EversoloSwitchDescription, ...] = (
    EversoloSwitchDescription(
        key="cd_auto_play",
        translation_key="cd_auto_play",
        icon="mdi:disc-player",
        entity_category=EntityCategory.CONFIG,
        setting_tag=SETTING_TAG_CD_AUTO_PLAY,
        is_supported=lambda capabilities: capabilities.has_cd,
        write=lambda client, enabled: client.async_set_cd_auto_play(enabled),
    ),
    EversoloSwitchDescription(
        key="subwoofer_output",
        translation_key="subwoofer_output",
        icon="mdi:speaker",
        entity_category=EntityCategory.CONFIG,
        # The main tree only points at the subwoofer page; the toggle itself is
        # inside it, which is why the tag is not the one the gate reads.
        setting_tag=SETTING_TAG_SUBWOOFER_SWITCH,
        is_supported=lambda capabilities: capabilities.has_subwoofer,
        write=lambda client, enabled: client.async_set_subwoofer_output(enabled),
    ),
    EversoloSwitchDescription(
        key="gapless",
        translation_key="gapless",
        icon="mdi:transition",
        entity_category=EntityCategory.CONFIG,
        setting_tag=SETTING_TAG_GAPLESS,
        is_supported=lambda capabilities: capabilities.has_gapless,
        write=lambda client, enabled: client.async_set_gapless(enabled),
    ),
    EversoloSwitchDescription(
        key="eos_engine",
        translation_key="eos_engine",
        icon="mdi:waveform",
        entity_category=EntityCategory.CONFIG,
        setting_tag=SETTING_TAG_EOS_ENGINE,
        is_supported=lambda capabilities: capabilities.has_eos_engine,
        write=lambda client, enabled: client.async_set_eos_engine(enabled),
    ),
)


def _build(
    coordinator: EversoloDataUpdateCoordinator, capabilities: EversoloCapabilities
) -> list[SwitchEntity]:
    """Build the switches this unit's hardware justifies."""
    switches: list[SwitchEntity] = [
        EversoloSwitch(coordinator, description)
        for description in ENTITY_DESCRIPTIONS
        if description.is_supported(capabilities)
    ]
    if capabilities.has_screen_power:
        switches.append(EversoloScreenSwitch(coordinator))
    return switches


async def async_setup_entry(hass, entry: EversoloConfigEntry, async_add_devices):
    """Set up the Switch platform."""
    coordinator = entry.runtime_data

    async_add_capability_gated(
        coordinator,
        async_add_devices,
        lambda capabilities: _build(coordinator, capabilities),
    )


class EversoloSwitch(EversoloEntity, SwitchEntity):
    """One of the device's settings toggles."""

    entity_description: EversoloSwitchDescription

    def __init__(
        self,
        coordinator: EversoloDataUpdateCoordinator,
        entity_description: EversoloSwitchDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )
        # The write the device has not confirmed yet; None means "no guess".
        self._expected: bool | None = None

    @property
    def is_on(self) -> bool | None:
        """Whether the toggle is on, as far as anyone knows."""
        if self._expected is not None:
            return self._expected
        return self.coordinator.data.toggles.is_on(self.entity_description.setting_tag)

    def _handle_coordinator_update(self) -> None:
        """Take the device's word for it, dropping any guess."""
        self._expected = None
        super()._handle_coordinator_update()

    async def async_turn_on(self, **_: object) -> None:
        """Turn the toggle on."""
        await self._async_write(True)

    async def async_turn_off(self, **_: object) -> None:
        """Turn the toggle off."""
        await self._async_write(False)

    async def _async_write(self, enabled: bool) -> None:
        """Write the toggle, show it, then confirm it against the device."""
        await self.entity_description.write(self.coordinator.client, enabled)
        self._expected = enabled
        self.async_write_ha_state()
        # The tree is slow-tier, so confirm the write instead of leaving the
        # switch on a guess for up to 30 s.
        await self.coordinator.async_refresh_settings()


class EversoloScreenSwitch(EversoloEntity, SwitchEntity, RestoreEntity):
    """The front screen: lit, or blanked.

    The odd one out on this platform, in two ways. It is not a settings toggle
    — it is the power menu's ``screen`` action, the same call the Reboot and
    Power Off buttons use with a different tag — and **no field the device
    reports says whether the screen is lit**. Not ``getState``, not the
    settings tree.

    The device does nonetheless report the state, in a place that is not a
    field: ``getPowerOption``'s ``screen`` entry carries a *label*, and the
    firmware computes it per request from the system property
    ``zidoo.close.screen.mode`` — "Screen off" while lit, "Screen on" while
    blanked. Confirmed 2026-08-23 by blanking the screen at the front panel
    and watching the value flip, so it is a real read-back and not a client
    remembering itself. An earlier design read exactly this label, matching it
    against a hard-coded list of seven localised strings, and was removed as
    unverified; the label was right, the way of reading it is the problem.
    That string is the *only* exposure of the property anywhere in the API,
    and it is rendered in the device's own UI locale — so reading it means a
    string-compare against localised text. Until that is solved this switch
    stays on its own memory rather than guessing from a translation.

    Three things follow, and each is deliberate:

    * ``assumed_state`` — the frontend then offers separate on and off
      buttons rather than a toggle that looks like a reading.
    * The last request is **restored across restarts**. It is a guess either
      way, but a guess that survives a Home Assistant restart is right far
      more often than one that resets to "unknown" and then guesses again.
    * A request for the state already shown is not sent. The one call
      available *toggles*, so the device would take a redundant request as
      "change" and do the opposite of what was asked.

    The cost of the arrangement is a screen switched at the unit itself: this
    switch cannot notice, and its next press will be a beat out of step.
    Nothing the device exposes can fix that.
    """

    _attr_assumed_state = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:monitor"
    _attr_translation_key = "screen"

    def __init__(self, coordinator: EversoloDataUpdateCoordinator) -> None:
        """Initialize the screen switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_screen"
        # What was last asked for. None until something is: no read can supply it.
        self._expected: bool | None = None

    async def async_added_to_hass(self) -> None:
        """Pick the last request back up, since no read can re-establish it."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state == STATE_ON:
                self._expected = True
            elif last.state == STATE_OFF:
                self._expected = False

    @property
    def is_on(self) -> bool | None:
        """Whether the screen was last asked to be lit, or None if never."""
        return self._expected

    async def async_turn_on(self, **_: object) -> None:
        """Wake the screen."""
        await self._async_write(True)

    async def async_turn_off(self, **_: object) -> None:
        """Blank the screen."""
        await self._async_write(False)

    async def _async_write(self, enabled: bool) -> None:
        """Toggle the screen, unless it is already where it was asked to go."""
        if self._expected is enabled:
            return
        await self.coordinator.client.async_toggle_screen()
        self._expected = enabled
        self.async_write_ha_state()
