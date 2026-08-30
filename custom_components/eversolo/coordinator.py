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
from dataclasses import replace
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from . import wake_on_lan
from .api import (
    EversoloApiClient,
    EversoloApiClientError,
)
from .const import (
    CONF_ENABLE_MUSICBRAINZ_LOOKUP,
    DOMAIN,
    LIVE_UPDATE_INTERVAL,
    LOGGER,
    PLAY_TYPE_BLUETOOTH,
    PROCESSING_GATE_CYCLES,
    SCREENSAVER_KEEPALIVE_CYCLES,
    SETTINGS_REFRESH_CYCLES,
)
from .data import (
    EversoloCapabilities,
    EversoloData,
    EversoloDevice,
    EversoloPlayback,
    EversoloProcessing,
)
from .musicbrainz import EversoloMusicBrainzClient

type BluetoothTrack = tuple[str, str, str | None]

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
        musicbrainz_client: EversoloMusicBrainzClient,
    ) -> None:
        """Initialize."""
        self.client = client
        self._musicbrainz = musicbrainz_client
        # The Bluetooth (artist, title, album) a cover lookup was last fired
        # for, and what it found — None/None until a lookup has ever run.
        # Both reset together so a resolving lookup can never be shown against
        # the track that has since replaced it.
        self._bluetooth_track: BluetoothTrack | None = None
        self._bluetooth_cover_url: str | None = None
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
        # User preference, not a device reading — see the screensaver switch.
        self._suppress_screensaver = False
        self._cycles_since_screensaver_keepalive = 0
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
    def bluetooth_cover_url(self) -> str | None:
        """The cover MusicBrainz found for the live Bluetooth track, if any.

        ``None`` both before a lookup has run and once it has run and found
        nothing — the media player (#18) cannot tell those apart, and does
        not need to: either way there is no image to show.
        """
        return self._bluetooth_cover_url

    @property
    def capabilities_settled(self) -> bool:
        """Whether every gate has its final answer and none can still change.

        Read by ``async_add_capability_gated`` to know when it can stop
        watching for entities that are not justified *yet*.
        """
        return self._gates_settled

    @property
    def screensaver_suppression_enabled(self) -> bool:
        """Whether the screensaver-suppression switch is on."""
        return self._suppress_screensaver

    @callback
    def set_screensaver_suppression(self, enabled: bool) -> None:
        """Record the switch's request; the next live cycle acts on it."""
        self._suppress_screensaver = enabled
        self._cycles_since_screensaver_keepalive = 0

    async def async_wake(self) -> None:
        """Send the magic packet that is this unit's only power-on mechanism.

        Targets ``self.device.net_mac`` rather than the config entry's own
        ``unique_id`` — the two usually agree, since the entry is anchored to
        exactly this field at setup (config_flow.py:65), but a legacy entry
        that migrated while the device was offline can carry a ``None``
        unique_id (``_async_read_unique_id`` in ``__init__.py`` is
        best-effort). ``device.net_mac`` cannot be ``None`` here: it lands in
        the same profile read that decides ``has_power_on``, capabilities are
        never unpublished once set (``EversoloData.merge``), and every caller
        of this method — the Power On button, the media player's ``turn_on``
        — exists only because that read already succeeded.
        """
        await wake_on_lan.async_wake(
            self.hass, self.config_entry.data[CONF_HOST], self.device.net_mac
        )

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
        else:
            self._track_device_name(data.device.name)
        self._settle_capability_gates(data.processing)
        self._maybe_lookup_bluetooth_cover(data.playback)
        await self._async_maybe_keep_screensaver_awake(data.playback)

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
    def _maybe_lookup_bluetooth_cover(self, playback: EversoloPlayback) -> None:
        """Fire an off-device cover lookup for a Bluetooth track HA hasn't seen.

        Off unless the entry's options opt in (#18) — this is the one thing
        this integration ever sends off the local network. Only Bluetooth
        (``play_type == 4``) has no cover of its own to fall back to; every
        other source either has real art or a device-local id to fetch it by.

        Firing is gated on the ``(artist, title, album)`` tuple changing, not
        on a poll cycle passing — the client caches the query itself, but
        without this gate every 5 s cycle would still pay for the cache
        lookup and, worse, would re-arm the throttle wait on an unretired
        track already answered.
        """
        if not self.config_entry.options.get(CONF_ENABLE_MUSICBRAINZ_LOOKUP, False):
            return
        if (
            playback.play_type != PLAY_TYPE_BLUETOOTH
            or not playback.artist
            or not playback.title
        ):
            return

        track: BluetoothTrack = (playback.artist, playback.title, playback.album)
        if track == self._bluetooth_track:
            return

        self._bluetooth_track = track
        # Cleared rather than left showing the previous track's art while the
        # new lookup is in flight — a stale cover is worse than none.
        self._bluetooth_cover_url = None
        # Tied to the config entry, not the bare event loop: a reload or
        # unload while a lookup is in flight cancels it along with everything
        # else the entry owns, rather than letting it complete against a
        # coordinator nothing is listening to anymore.
        self.config_entry.async_create_background_task(
            self.hass,
            self._async_resolve_bluetooth_cover(track),
            name=f"{DOMAIN} musicbrainz cover lookup",
        )

    async def _async_maybe_keep_screensaver_awake(
        self, playback: EversoloPlayback
    ) -> None:
        """Reset the device's screensaver idle clock while suppression is on.

        The device's screensaver runs on a pure idle-since-last-*write* clock,
        blind to playback (RESEARCH.md, "Ticket 09"), and there is no
        app-level "don't screensave during playback" flag to flip — the whole
        family is confirmed device-tree passthrough, not app code. But
        re-issuing ``setScreensaverTime`` with its own *current* index resets
        that clock without changing the configured timeout, so this leans on
        that as a keep-alive: while the switch is on and something is
        playing, re-touch it every :data:`SCREENSAVER_KEEPALIVE_CYCLES` live
        cycles — comfortably inside the shortest selectable timeout (5 min).

        The counter resets whenever suppression is off or nothing is playing,
        so the first keep-alive after playback (re)starts always lands a full
        interval later, never immediately.
        """
        if not self._suppress_screensaver or not playback.is_playing:
            self._cycles_since_screensaver_keepalive = 0
            return

        self._cycles_since_screensaver_keepalive += 1
        if self._cycles_since_screensaver_keepalive < SCREENSAVER_KEEPALIVE_CYCLES:
            return
        self._cycles_since_screensaver_keepalive = 0

        try:
            current = await self.client.async_get_screensaver_time_list()
            index = current.get("currentIndex")
            if index is None:
                return
            await self.client.async_set_screensaver_time(index)
        except EversoloApiClientError as exception:
            LOGGER.debug(
                "Could not keep the screensaver at bay this cycle: %s", exception
            )

    async def _async_resolve_bluetooth_cover(self, track: BluetoothTrack) -> None:
        """Look up one Bluetooth track's cover and publish it, if still current."""
        artist, title, album = track
        cover = await self._musicbrainz.async_lookup_cover(artist, title, album)

        if self._bluetooth_track != track:
            # A newer track has already taken over; this answer arrived too
            # late to mean anything and must not overwrite its cover.
            return
        self._bluetooth_cover_url = cover
        self.async_update_listeners()

    @callback
    def _track_device_name(self, live_name: str | None) -> None:
        """Follow the name the user sets on the device itself, unlike model/mac.

        Everything else in ``EversoloDevice`` is a hardware fact, read once and
        final (module docstring). The name is not: it is the one field a user
        changes from the Eversolo app whenever they like, so — alone among the
        identity fields — it is re-read every live cycle and pushed to the
        registry when it moves (#73). Pushing the bare ``name`` rather than
        touching the config entry's title leaves ``entity_id`` untouched, and
        a device the user has manually renamed inside HA is left alone too:
        ``name`` only backs the registry's *suggested* name, which HA's own
        ``name_by_user`` already overrides in the frontend without this
        integration having to know that happened.
        """
        # Falsy, not just ``None``: ``EversoloDevice.from_state`` (unlike
        # ``from_model``) applies no model fallback for a blank
        # ``deviceInfo.deviceName``, so an empty string is this tier's way of
        # saying nothing was reported — not the device asking to be renamed
        # to nothing.
        if not live_name or live_name == self._device.name:
            return
        self._device = replace(self._device, name=live_name)
        self._async_update_device_registry()

    @callback
    def _async_update_device_registry(self) -> None:
        """Fill in identity fields the registry entry didn't have at setup.

        Entities capture their ``DeviceInfo`` when they are created, which can
        be before the unit has ever answered, so the registry needs the
        identity pushed to it once the profile finally lands — and, for
        ``name`` alone, again whenever ``_track_device_name`` sees it move.
        """
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(
            identifiers={(DOMAIN, self.config_entry.entry_id)}
        )
        if device is None:
            return
        registry.async_update_device(
            device.id,
            # ``display_title`` is the same "{NAME} {name}" shape
            # config_flow.py gives the entry title at setup — pushing the
            # bare device name here would strip that prefix off the very
            # first call, before anything has moved. UNDEFINED (not None)
            # when there is nothing yet: passing None would clear the
            # registry's name outright rather than leave it.
            name=self.device.display_title if self.device.name else dr.UNDEFINED,
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
