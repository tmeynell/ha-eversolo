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
    SETTING_TAG_AUTO_CHANGE_SOURCE,
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
    EversoloSwitchDescription(
        key="auto_change_source_internal_player",
        translation_key="auto_change_source_internal_player",
        icon="mdi:swap-horizontal",
        entity_category=EntityCategory.CONFIG,
        setting_tag=SETTING_TAG_AUTO_CHANGE_SOURCE,
        is_supported=lambda capabilities: capabilities.has_auto_change_source,
        write=lambda client, enabled: client.async_set_auto_change_source(enabled),
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
    if capabilities.has_screensaver:
        switches.append(EversoloScreensaverSuppressSwitch(coordinator))
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

    The odd one out on this platform in how it is written: it is not a
    settings toggle but the power menu's ``screen`` action, the same call the
    Reboot and Power Off buttons use with a different tag, and it is
    momentary — the single call available *toggles*, and reports nothing back.

    It is not odd in how it is read, though not for want of trying elsewhere:
    ``getPowerOption``'s ``screen`` entry carries a *label*, and the firmware
    computes it per request from the system property
    ``zidoo.close.screen.mode`` — confirmed 2026-08-23 by blanking the screen
    at the front panel and watching the value flip, so it is a real read-back
    and not a client remembering itself (RESEARCH.md, "Screen power state is
    reported"). The catch is that the label is rendered in the *device's* own
    UI locale, which the integration cannot ask for. Ticket 18
    (docs/screen-power-label-locales.md) recovered every translation the app
    ships and confirmed a locale-blind set-membership match — is the label in
    the "on" set or the "off" set — is safe across all 13 locales and free of
    collisions with any other UI text; :class:`~.data.EversoloScreenState`
    does that match on the settings tier every cycle.

    A label the table has never seen is the one case that reading cannot
    resolve — an unlisted device UI locale. There this switch falls back to
    its pre-reading design: :attr:`assumed_state` goes true so the frontend
    offers on and off separately rather than a toggle that looks like a
    reading it cannot make, and the last request (restored across restarts,
    same as before) stands in until a recognised label turns up.

    Same optimistic-then-confirmed shape as :class:`EversoloSwitch`: a write
    shows at once via ``_expected`` and is confirmed by the next settings
    read, which the coordinator callback drops ``_expected`` in favour of.
    ``_guess`` is a second, longer-lived memory underneath that — what
    ``_expected`` degrades to once confirmed, and all there is while the
    label goes unrecognised.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:monitor"
    _attr_translation_key = "screen"

    def __init__(self, coordinator: EversoloDataUpdateCoordinator) -> None:
        """Initialize the screen switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_screen"
        # The write the device has not confirmed yet; None means "no guess".
        self._expected: bool | None = None
        # The fallback guess, used only while the device's own label goes
        # unrecognised. None until either a request or a restored state
        # supplies one.
        self._guess: bool | None = None

    async def async_added_to_hass(self) -> None:
        """Pick the last request back up, for a locale the table cannot read."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state == STATE_ON:
                self._guess = True
            elif last.state == STATE_OFF:
                self._guess = False

    def _handle_coordinator_update(self) -> None:
        """Take the device's word for it, dropping any unconfirmed write."""
        self._expected = None
        super()._handle_coordinator_update()

    @property
    def _reading(self) -> bool | None:
        """The device's own report, or None if its label matched nothing."""
        return self.coordinator.data.screen.is_on

    @property
    def is_on(self) -> bool | None:
        """The pending write if there is one, else the reading, else the guess."""
        if self._expected is not None:
            return self._expected
        reading = self._reading
        return reading if reading is not None else self._guess

    @property
    def assumed_state(self) -> bool:
        """True only while there is no reading to show instead of a guess."""
        return self._reading is None

    async def async_turn_on(self, **_: object) -> None:
        """Wake the screen."""
        await self._async_write(True)

    async def async_turn_off(self, **_: object) -> None:
        """Blank the screen."""
        await self._async_write(False)

    async def _async_write(self, enabled: bool) -> None:
        """Toggle the screen, unless it already reads as where this asks."""
        if self.is_on is enabled:
            return
        await self.coordinator.client.async_toggle_screen()
        self._expected = enabled
        self._guess = enabled
        self.async_write_ha_state()
        # The tree is slow-tier, so confirm the write against the device's
        # own label instead of leaving the switch on a guess for up to 30 s.
        await self.coordinator.async_refresh_settings()


class EversoloScreensaverSuppressSwitch(EversoloEntity, SwitchEntity, RestoreEntity):
    """Keep the device awake while it plays, by request rather than by reading.

    This is the odd one out on the platform in the opposite direction from
    :class:`EversoloScreenSwitch`: not a device toggle at all, but an
    integration-side behaviour with no device state to confirm against — see
    ``EversoloDataUpdateCoordinator._async_maybe_keep_screensaver_awake`` for
    the mechanism. Its state is therefore just the request itself, restored
    across restarts the same way the screen switch's guess is, and reported
    without ``assumed_state``: unlike a device toggle this integration can
    never be wrong about it.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:monitor-eye"
    _attr_translation_key = "suppress_screensaver_during_playback"

    def __init__(self, coordinator: EversoloDataUpdateCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_suppress_screensaver"
        )

    async def async_added_to_hass(self) -> None:
        """Pick the last request back up; the coordinator starts off otherwise."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.coordinator.set_screensaver_suppression(last.state == STATE_ON)

    @property
    def is_on(self) -> bool:
        """Whether suppression is currently requested."""
        return self.coordinator.screensaver_suppression_enabled

    async def async_turn_on(self, **_: object) -> None:
        """Start suppressing the screensaver while playback is active."""
        self.coordinator.set_screensaver_suppression(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **_: object) -> None:
        """Stop suppressing; the device's own configured timeout applies again."""
        self.coordinator.set_screensaver_suppression(False)
        self.async_write_ha_state()
