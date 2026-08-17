"""DataUpdateCoordinator for eversolo.

One coordinator, two speeds. The *live tier* is a single ``getState`` read on
every cycle — playback, volume, mute, input tag, display flags. The *settings
tier* is the handful of rarely-changing list/brightness endpoints, refreshed
every sixth cycle and immediately after any write, via
:meth:`EversoloDataUpdateCoordinator.async_refresh_settings`.

The two tiers also differ in how failure is treated. Losing ``getState`` means
the device is gone, so it raises ``UpdateFailed`` and every entity goes
unavailable. A settings endpoint that stops answering is a nuisance, not an
outage: its last known value is kept and the device stays available.

Underneath the two tiers sit two *latches*, deliberately separate. **Identity**
is read once and is final the moment it lands. **Capabilities** are read at the
same time, but two of their gates — DSP and EQ — are fields of ``getState``
rather than answers from an endpoint, so a payload can omit them without
failing anything. Latching those off a silence would leave the entity they gate
permanently and silently missing, so they are waited for, across cycles and
within a bound: :meth:`EversoloDataUpdateCoordinator._settle_capability_gates`.

Capabilities are still **published immediately**, provisional gates and all.
The wait is expressed by adding entities as their gate is answered, not by
withholding the set — see ``async_add_capability_gated``. Holding everything
back for two undecided gates would cost a slow-answering unit its whole entity
set on every restart, which is a worse bug than the one being avoided.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    EversoloApiClient,
    EversoloApiClientError,
)
from .const import (
    DOMAIN,
    LIVE_UPDATE_INTERVAL,
    LOGGER,
    PROCESSING_GATE_CYCLES,
    SETTINGS_REFRESH_CYCLES,
)
from .data import (
    EversoloCapabilities,
    EversoloData,
    EversoloDevice,
    EversoloProcessing,
)

type EversoloConfigEntry = ConfigEntry[EversoloDataUpdateCoordinator]

# The slow tier: each endpoint keyed by the name entities read it back under,
# paired with the gate that decides whether this unit has the hardware at all
# (the A8 has no knob, so it never asks for the knob endpoints). Fetched in
# sequence rather than concurrently: the device times out under parallel load.
SETTINGS_FETCHERS: dict[
    str,
    tuple[
        Callable[[EversoloCapabilities], bool],
        Callable[[EversoloApiClient], Awaitable[Any]],
    ],
] = {
    # Also the state of every ``?switch=`` toggle: the tree is the only place
    # the device reports them, so it is polled, not just read once for
    # capability detection.
    "system_settings": (
        lambda _: True,
        lambda client: client.async_get_system_settings(),
    ),
    "screen_brightness": (
        lambda capabilities: capabilities.has_screen_brightness,
        lambda client: client.async_get_screen_brightness(),
    ),
    "input_output_state": (
        lambda _: True,
        lambda client: client.async_get_input_output_state(),
    ),
    "vu_mode_state": (
        lambda capabilities: capabilities.has_vu_style,
        lambda client: client.async_get_vu_mode_state(),
    ),
    "spectrum_mode_state": (
        lambda capabilities: capabilities.has_spectrum_style,
        lambda client: client.async_get_spectrum_state(),
    ),
    # The analog panel is a single shared XLR+RCA page, so one gate covers both
    # of its lists.
    "dac_filter_state": (
        lambda capabilities: capabilities.has_analog_panel,
        lambda client: client.async_get_dac_filter_state(),
    ),
    "upsampling_state": (
        lambda capabilities: capabilities.has_analog_panel,
        lambda client: client.async_get_upsampling_state(),
    ),
    "master_clock_state": (
        lambda capabilities: capabilities.has_master_clock,
        lambda client: client.async_get_master_clock_state(),
    ),
    # Read for the subwoofer toggle's state, which the main tree only points at.
    "sub_output_option": (
        lambda capabilities: capabilities.has_subwoofer,
        lambda client: client.async_get_sub_output_option(),
    ),
    "knob_brightness": (
        lambda capabilities: capabilities.has_knob,
        lambda client: client.async_get_knob_brightness(),
    ),
    "knob_color_state": (
        lambda capabilities: capabilities.has_knob_color,
        lambda client: client.async_get_knob_color_state(),
    ),
}


class EversoloDataUpdateCoordinator(DataUpdateCoordinator[EversoloData]):
    """Poll the device at two speeds and hand entities one typed snapshot."""

    config_entry: EversoloConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EversoloConfigEntry,
        client: EversoloApiClient,
    ) -> None:
        """Initialize."""
        self.client = client
        # Identity and capabilities come from one profile read but latch
        # separately: identity is final as soon as it lands, while the DSP and
        # EQ gates wait for a getState that reports them.
        self._device: EversoloDevice | None = None
        self._capabilities: EversoloCapabilities | None = None
        self._gates_settled = False
        self._cycles_awaiting_processing = 0
        # The best answer the device has given about DSP and EQ so far, which
        # is not necessarily the last one: a cycle may report one flag and omit
        # the other, and dropping what it did say would defeat the wait.
        self._processing_seen = EversoloProcessing()
        self._settings: dict[str, Any] = {}
        # Starts at the threshold so the first cycle reads the settings tier.
        self._cycles_since_settings = SETTINGS_REFRESH_CYCLES
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(seconds=LIVE_UPDATE_INTERVAL),
        )
        # Seeded empty so a platform setting up before the device has ever
        # answered — the entry loads even while the unit is off — reads a total
        # snapshot rather than None.
        self.data = EversoloData()

    @property
    def device(self) -> EversoloDevice:
        """Best-known identity — empty until the first profile read lands."""
        if self._device is not None:
            return self._device
        return EversoloDevice()

    @property
    def capabilities_settled(self) -> bool:
        """Whether every gate has its final answer and none can still change.

        Read by ``async_add_capability_gated`` to know when it can stop
        watching for entities that are not justified *yet*.
        """
        return self._gates_settled

    async def _async_update_data(self) -> EversoloData:
        """Run one live cycle, plus the slow tiers when they are due."""
        try:
            data = await self.client.async_read_state()
        except EversoloApiClientError as exception:
            raise UpdateFailed(exception) from exception

        if self._device is None:
            # The DSP and EQ gates are in this cycle's getState and nowhere
            # else, so the profile read is handed the slice rather than asking
            # the device for the same payload again.
            await self._async_read_profile(data.processing)
        self._settle_capability_gates(data.processing)

        self._cycles_since_settings += 1
        if self._cycles_since_settings >= SETTINGS_REFRESH_CYCLES:
            await self._async_read_settings()

        return data.merge(
            settings=self._settings,
            device=self._device,
            # Published as soon as they exist, even with the two getState gates
            # still provisional. Withholding the whole set to wait on those two
            # would take every unrelated entity down with them for the length
            # of the wait, on every restart — the gates only ever gain answers,
            # and ``async_add_capability_gated`` adds what each one justifies
            # as it arrives.
            capabilities=self._capabilities,
        )

    async def async_refresh_settings(self) -> None:
        """Re-read the settings tier now and publish it.

        Entities call this after a write so the UI confirms against the device
        instead of waiting up to half a minute for the next settings cycle.
        """
        await self._async_read_settings()
        # Publishing marks the coordinator successful, so skip it while the live
        # tier is down — a settings write must not fake the device back online.
        if self.last_update_success:
            self.async_set_updated_data(
                self.data.merge(
                    settings=self._settings,
                    device=self._device,
                    capabilities=self._capabilities,
                )
            )

    async def _async_read_profile(self, processing: EversoloProcessing) -> None:
        """Read identity and capabilities once; retry next cycle if it fails."""
        try:
            profile = await self.client.async_read_profile(processing)
        except EversoloApiClientError as exception:
            LOGGER.debug("Could not read the device profile yet: %s", exception)
            return

        self._device = profile.device
        # Provisional in its DSP and EQ gates if this cycle's getState did not
        # report them. Good enough to drive the settings tier, which no
        # processing gate feeds; not published to entities until settled.
        self._capabilities = profile.capabilities
        self._async_update_device_registry()

    @callback
    def _settle_capability_gates(self, processing: EversoloProcessing) -> None:
        """Decide the DSP and EQ gates once the device has reported them.

        Every other capability is decided by an endpoint that either answers or
        raises, and a failed profile read is simply retried. These two are
        *fields* of ``getState``, so a payload that omits them is
        indistinguishable from the device saying no — and the profile is read
        once, which would make that mishearing permanent and silent: no error,
        no log line, no DSP sensor, and nothing short of deleting the config
        entry to recover it.

        So the wait is explicit. The gates settle once *both* have been
        answered — across cycles, not necessarily within one, which is what
        ``_processing_seen`` accumulates — or at
        :data:`PROCESSING_GATE_CYCLES`, whichever comes first; after that the
        silence is taken at face value and the answer is final. Bounded rather
        than indefinite because latching once is what keeps entities from
        appearing and vanishing under a running Home Assistant: a device that
        never reports the flags has to end up with a decided entity set, not
        with an undecided one.

        Two things are deliberately outside this wait, because both would turn
        a partial failure into a total one. **Identity** lands on the first
        successful profile read and is never revisited, so waiting never costs
        the device its model, firmware or registry entry. **Every other
        capability** is published as soon as it is known rather than held back
        for company, so a unit that is slow to answer this one question does
        not lose its whole entity set for half a minute on each restart.
        """
        if self._capabilities is None or self._gates_settled:
            return

        self._processing_seen = processing.retaining_gates_from(self._processing_seen)
        self._cycles_awaiting_processing += 1
        if (
            not self._processing_seen.reports_capabilities
            and self._cycles_awaiting_processing < PROCESSING_GATE_CYCLES
        ):
            return

        self._capabilities = self._capabilities.with_processing(self._processing_seen)
        self._gates_settled = True

    @callback
    def _async_update_device_registry(self) -> None:
        """Fill in model and firmware for a device that was off at setup.

        Entities capture their ``DeviceInfo`` when they are created, which can
        be before the unit has ever answered, so the registry needs the identity
        pushed to it once the profile finally lands.
        """
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(
            identifiers={(DOMAIN, self.config_entry.entry_id)}
        )
        if device is None:
            return
        registry.async_update_device(
            device.id,
            model=self.device.model,
            sw_version=self.device.firmware,
        )

    async def _async_read_settings(self) -> None:
        """Refresh the slow tier, keeping the last value for anything that fails."""
        # An unread profile gates every optional endpoint off; the next cycle
        # picks them up once the device has said what hardware it has. The
        # provisional copy is fine here even before the gates settle: no
        # settings endpoint is gated on DSP or EQ.
        capabilities = self._capabilities or EversoloCapabilities()

        for key, (is_supported, fetcher) in SETTINGS_FETCHERS.items():
            if not is_supported(capabilities):
                continue
            try:
                self._settings[key] = await fetcher(self.client)
            except EversoloApiClientError as exception:
                LOGGER.debug(
                    "Keeping the last known %s; the device did not answer: %s",
                    key,
                    exception,
                )

        self._cycles_since_settings = 0
