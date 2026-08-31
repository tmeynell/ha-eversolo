"""Media player platform for eversolo — the playback hub.

One entity per device: what is playing, the transport that drives it, and the
volume. It reads the live tier only, so it goes unavailable the moment
``getState`` stops answering — unless the last known profile said the unit
accepts Wake-on-LAN, in which case it reports ``off`` instead (see
``available``/``state``): an unavailable entity cannot be sent ``turn_on``,
which would make the whole point of that service decorative. ``turn_on`` and
``turn_off`` mirror the Power On/Off buttons — same gates, same commands —
rather than this entity owning either action itself.

Two rules shape the code below:

* **The device says what it can do.** ``everSoloPlayInfo``'s ``isCan*`` flags go
  false on inputs the unit cannot drive (TV/eARC passes audio through untouched),
  so the transport features are advertised per-poll rather than fixed, and each
  command re-checks before firing — an inert input is a no-op, never an error.
* **Writes are optimistic.** A command that succeeded has a known outcome, so the
  entity shows it immediately and lets the next 5 s poll confirm, instead of
  spending an extra read on it. Every poll wipes the guesses: the device's own
  report always wins.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from homeassistant.components.media_player import (
    ATTR_MEDIA_ENQUEUE,
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEnqueue,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .const import (
    CD_SOURCE,
    DEFAULT_CD_IMAGE_URL_PATH,
    LOGGER,
    LOOP_MODEL_OFF,
    LOOP_MODEL_REPEAT_ALL,
    LOOP_MODEL_REPEAT_ONE,
    LOOP_MODEL_SHUFFLE,
    PLAY_TYPE_BLUETOOTH,
    QUEUE_ACTION_ADD,
    QUEUE_ACTION_NEXT,
    QUEUE_ACTION_PLAY,
    QUEUE_CONTENT_ALBUM,
    QUEUE_CONTENT_ARTIST,
    QUEUE_CONTENT_TRACK,
)
from .coordinator import EversoloConfigEntry, EversoloDataUpdateCoordinator
from .data import EversoloPlayback, EversoloVolume
from .entity import EversoloEntity
from .media_library import async_browse_library, async_search_library

VOLUME_FEATURES = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.VOLUME_STEP
)

PLAY_MODE_FEATURES = (
    MediaPlayerEntityFeature.SHUFFLE_SET | MediaPlayerEntityFeature.REPEAT_SET
)

_REPEAT_BY_LOOP_MODEL = {
    LOOP_MODEL_REPEAT_ALL: RepeatMode.ALL,
    LOOP_MODEL_REPEAT_ONE: RepeatMode.ONE,
}
_LOOP_MODEL_BY_REPEAT = {mode: model for model, mode in _REPEAT_BY_LOOP_MODEL.items()}

# What the local browse tree (#47) can hand ``play_media``, mapped onto
# ``addLocalSongsToPlayQueue``'s ``type`` — see :mod:`.media_library`.
_QUEUE_CONTENT_BY_MEDIA_TYPE = {
    MediaType.TRACK: QUEUE_CONTENT_TRACK,
    MediaType.ALBUM: QUEUE_CONTENT_ALBUM,
    MediaType.ARTIST: QUEUE_CONTENT_ARTIST,
}

# HA's four ``enqueue`` values, mapped onto the same endpoint's ``playType``
# (#48) — live-verified (RESEARCH.md, 2026-08-31). ``play``/``replace`` are
# not the same action (``QUEUE_ACTION_PLAY`` alone only adds and plays, it
# does not clear what's already queued), so both are handled in
# ``async_play_media`` rather than folded into this table.
_QUEUE_ACTION_BY_ENQUEUE = {
    MediaPlayerEnqueue.NEXT: QUEUE_ACTION_NEXT,
    MediaPlayerEnqueue.ADD: QUEUE_ACTION_ADD,
}


def _ignored(action: str) -> None:
    """Note a command the current input will not act on."""
    LOGGER.debug("Ignoring %s: the current input does not support it", action)


@dataclass(frozen=True, slots=True)
class Expected:
    """What a just-accepted command should look like, pending confirmation.

    One field per property that can be guessed; ``None`` means "no guess
    outstanding, use what the device last reported".
    """

    state: MediaPlayerState | None = None
    volume_level: float | None = None
    is_volume_muted: bool | None = None
    position: int | None = None
    source: str | None = None
    loop_model: int | None = None


async def async_setup_entry(hass, entry: EversoloConfigEntry, async_add_devices):
    """Set up the Media Player platform."""
    async_add_devices([EversoloMediaPlayer(entry.runtime_data)])


class EversoloMediaPlayer(EversoloEntity, MediaPlayerEntity):
    """Eversolo Media Player."""

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_name = None

    def __init__(self, coordinator: EversoloDataUpdateCoordinator) -> None:
        """Initialize the Media Player."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_media_player"
        # Outcomes the device has not confirmed yet, wiped by the next poll.
        self._expected = Expected()
        self._position: int | None = None
        self._position_updated_at: datetime | None = None

    # ------------------------------------------------------------------
    # Snapshot plumbing.
    # ------------------------------------------------------------------

    @property
    def _playback(self) -> EversoloPlayback:
        """The last known playback slice."""
        return self.coordinator.data.playback

    @property
    def _volume(self) -> EversoloVolume:
        """The last known volume slice."""
        return self.coordinator.data.volume

    def _guess(self, guessed: Any, reported: Any) -> Any:
        """Prefer an unconfirmed command outcome over the last polled value."""
        return reported if guessed is None else guessed

    def _expect(self, **outcomes: Any) -> None:
        """Show the outcome of a command that has just been accepted."""
        if (position := outcomes.get("position")) is not None:
            # The seek bar is extrapolated from the stamp, so a guessed
            # position has to carry a matching one or it reads as stale.
            self._track_position(position)
        # ``replace`` rejects a field name that is not a guessable property.
        self._expected = replace(self._expected, **outcomes)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Stamp the position that was already polled before we were added."""
        await super().async_added_to_hass()
        self._track_position(self._playback.position)

    def _handle_coordinator_update(self) -> None:
        """Take the device's word for everything, dropping any guesses."""
        self._expected = Expected()
        self._track_position(self._playback.position)
        super()._handle_coordinator_update()

    def _track_position(self, position: int | None) -> None:
        """Record when the playback position last actually moved.

        The frontend extrapolates the seek bar from this stamp, so it has to
        mark a genuinely fresh reading — refreshing it on every poll would keep
        a stalled player's bar creeping forward.
        """
        if position != self._position:
            self._position = position
            self._position_updated_at = dt_util.utcnow()

    # ------------------------------------------------------------------
    # What the device is doing.
    # ------------------------------------------------------------------

    @property
    def _can_wake(self) -> bool:
        """Whether the last known profile said this unit accepts Wake-on-LAN.

        Read off whatever capabilities were last published, which survives a
        live-tier outage: capabilities latch once from the settings tier and
        are not part of ``getState``. A unit that never claimed
        ``ableRemoteBoot`` gets none of the behaviour below, and keeps honest
        unavailability instead of a control that would do nothing.
        """
        capabilities = self.coordinator.data.capabilities
        return capabilities is not None and capabilities.has_power_on

    @property
    def available(self) -> bool:
        """True while the device answers, or it is off but can be woken.

        HA will not dispatch a service call to an unavailable entity, so
        without this a boot-capable unit's ``TURN_ON`` would be decorative —
        it could never be off *and* available to receive it. **Accepted
        cost**: a genuine network fault and a powered-down unit now both read
        available (and, per ``state`` below, ``off``); this integration has no
        way to tell the two apart short of a probe it does not attempt. That
        is a deliberate reversal, for a unit that can actually be woken, of
        the plain live-tier-outage-is-unavailable rule the original power
        design chose.
        """
        return self.coordinator.last_update_success or self._can_wake

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Advertise only the controls this input actually responds to."""
        playback = self._playback
        capabilities = self.coordinator.data.capabilities
        features = MediaPlayerEntityFeature(0)

        if self.source_list:
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        if self._volume.is_enabled:
            features |= VOLUME_FEATURES
        if playback.can_change_play_status:
            features |= MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.PAUSE
        if playback.can_next:
            features |= MediaPlayerEntityFeature.NEXT_TRACK
        if playback.can_previous:
            features |= MediaPlayerEntityFeature.PREVIOUS_TRACK
        if playback.can_seek:
            features |= MediaPlayerEntityFeature.SEEK
        if playback.has_play_mode:
            features |= PLAY_MODE_FEATURES
        if self._can_wake:
            features |= MediaPlayerEntityFeature.TURN_ON
        if capabilities is not None and capabilities.has_power_off:
            features |= MediaPlayerEntityFeature.TURN_OFF
        if playback.has_play_queue:
            features |= (
                MediaPlayerEntityFeature.BROWSE_MEDIA
                | MediaPlayerEntityFeature.PLAY_MEDIA
                | MediaPlayerEntityFeature.MEDIA_ENQUEUE
                | MediaPlayerEntityFeature.CLEAR_PLAYLIST
                | MediaPlayerEntityFeature.SEARCH_MEDIA
            )

        return features

    @property
    def state(self) -> MediaPlayerState:
        """Return Media Player state.

        ``playStatus`` is the honest signal (1 playing, 0 not); the top-level
        ``state`` field goes stale on an inert input. The device draws no line
        between paused and stopped, so a stopped track the unit can still
        resume reads as paused. A track it cannot drive at all — the disc still
        reported while the unit sits on the TV input — is not "paused": nothing
        would resume it, so that reads as idle.

        This is only ever reached while ``available`` — HA substitutes
        "unavailable" itself otherwise — so a live-tier outage that gets here
        at all is exactly the boot-capable case ``available`` carves out, and
        reads as off rather than falling through to a guess built from data
        that stopped being current the moment the device went quiet.
        """
        if not self.coordinator.last_update_success:
            return MediaPlayerState.OFF
        playback = self._playback
        if playback.is_playing:
            reported = MediaPlayerState.PLAYING
        elif playback.has_media and playback.can_change_play_status:
            reported = MediaPlayerState.PAUSED
        else:
            reported = MediaPlayerState.IDLE
        return self._guess(self._expected.state, reported)

    @property
    def volume_level(self) -> float | None:
        """Volume level of the Media Player in range 0..1."""
        return self._guess(self._expected.volume_level, self._volume.level)

    @property
    def is_volume_muted(self) -> bool:
        """Return muted state."""
        return self._guess(self._expected.is_volume_muted, self._volume.is_muted)

    @property
    def media_title(self) -> str | None:
        """Title of current playing media."""
        return self._playback.title

    @property
    def media_artist(self) -> str | None:
        """Artist of current playing media."""
        return self._playback.artist

    @property
    def media_content_type(self) -> MediaType | None:
        """Content type of current playing media.

        Frontend media cards (``hui-media-control-card``) only render the
        artist subtitle for a handful of ``media_content_type`` values —
        without this, the artist is present in state/more-info but never
        drawn on the card itself.
        """
        return MediaType.MUSIC if self._playback.title else None

    @property
    def media_album_name(self) -> str | None:
        """Album of current playing media."""
        return self._playback.album

    @property
    def media_image_url(self) -> str | None:
        """Image url of current playing media.

        ``EversoloPlayback.from_state`` already picked the block that
        describes what is audible, by ``playType`` — a network source's
        ``art_url`` is an absolute URL, the local player's is a device-local
        path or nothing, and Bluetooth's is always nothing. The local branch
        (nothing in hand) falls through to a song-id lookup; a streaming or
        Bluetooth track has no disc song id to fetch. Last resort is
        ``form_icon`` (#16) — a small source badge, not real art, and never
        set for Bluetooth (see ``EversoloPlayback.from_state``), but better
        than nothing on a local disc with no embedded cover or a streaming
        track its service sent no art for.

        Bluetooth is the one exception to that chain (#18): the device gives
        it no art at all, ever, so the fallback is not another device field
        but whatever the coordinator's opt-in MusicBrainz lookup last found
        for this exact track — ``None`` while that lookup is off, still
        running, or came back empty, same as the device's own "nothing" would
        read.

        A disc with no discovered metadata at all (#65) is the other
        exception: the song-id lookup has nothing to key off and 806s, same
        as it would for a nonexistent id, so that branch is skipped in favour
        of a bundled default-CD image before it is ever attempted. Gated on
        ``is_local_source`` as well as ``is_cd``, same as ``source`` (#03) —
        ``extension`` can otherwise still read "cd" from a stale disc sitting
        in the tray while a different source is what's actually audible.
        """
        playback = self._playback
        client = self.coordinator.client

        if playback.play_type == PLAY_TYPE_BLUETOOTH:
            return self.coordinator.bluetooth_cover_url
        if playback.art_url:
            if playback.art_url.startswith("http"):
                return playback.art_url
            return client.create_image_url_by_path(playback.art_url)
        if (
            playback.is_local_source
            and playback.is_cd
            and not (playback.title or playback.artist or playback.album)
        ):
            return DEFAULT_CD_IMAGE_URL_PATH
        if playback.song_id is not None:
            return client.create_image_url_by_song_id(
                playback.song_id, playback.music_type
            )
        return client.create_image_url_or_none(playback.form_icon)

    @property
    def media_image_remotely_accessible(self) -> bool:
        """True only for the Bluetooth cover MusicBrainz found (#18).

        Every other image this entity ever returns is served by the device
        itself over the LAN, which needs HA's own proxy/auth in front of it —
        the default this property falls back to otherwise. A Cover Art
        Archive URL is a real internet address with nothing to proxy.
        """
        return (
            self._playback.play_type == PLAY_TYPE_BLUETOOTH
            and self.coordinator.bluetooth_cover_url is not None
        )

    @property
    def media_duration(self) -> float | None:
        """Duration of current playing media in seconds."""
        duration = self._playback.duration
        return duration / 1000 if duration else None

    @property
    def media_position(self) -> float | None:
        """Position of current playing media in seconds."""
        position = self._guess(self._expected.position, self._playback.position)
        return position / 1000 if position is not None else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        """When the reported position was last a fresh reading."""
        return self._position_updated_at

    # ------------------------------------------------------------------
    # Source selection, including the synthetic CD.
    # ------------------------------------------------------------------

    @property
    def _has_cd(self) -> bool:
        """Whether this unit has a disc drive at all."""
        capabilities = self.coordinator.data.capabilities
        return capabilities is not None and capabilities.has_cd

    @property
    def source(self) -> str | None:
        """Return the current input source.

        The synthetic CD source reads back only while the local player is
        what's audible (``playback.is_local_source``, ``playType == 5``,
        #03) **and** the loaded item is actually a disc (``playback.is_cd``,
        ``extension == "cd"``, #35) — ``playType == 5`` alone is also true
        for a local library/SMB/USB track playing on the internal player,
        which is not a disc even though the unit keeps reporting a loaded
        one in ``playingMusic``.
        """
        current = self.coordinator.data.inputs.current
        if self._has_cd and self._playback.is_local_source and self._playback.is_cd:
            reported = CD_SOURCE
        else:
            reported = current.name if current else None
        return self._guess(self._expected.source, reported)

    @property
    def _loop_model(self) -> int | None:
        """The device's one shuffle/repeat state, guess-then-confirm."""
        return self._guess(self._expected.loop_model, self._playback.loop_model)

    @property
    def shuffle(self) -> bool | None:
        """True only for ``loopModel`` 2 — the device's one shuffle state."""
        return self._loop_model == LOOP_MODEL_SHUFFLE

    @property
    def repeat(self) -> RepeatMode:
        """Map ``loopModel`` onto HA's repeat modes; anything else reads off."""
        return _REPEAT_BY_LOOP_MODEL.get(self._loop_model, RepeatMode.OFF)

    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Turn shuffle on/off.

        Off lands on ``loopModel`` 3 (off), not whatever repeat mode preceded
        it: the device has no state to restore from, so caching a prior
        repeat value here would invent state the device doesn't have and
        desync the moment the front panel is touched. Turning shuffle on
        silently drops any repeat mode that was set — an intentional,
        documented lossy mapping (#46), visible within one poll cycle.
        """
        loop_model = LOOP_MODEL_SHUFFLE if shuffle else LOOP_MODEL_OFF
        await self.coordinator.client.async_set_loop_mode(loop_model)
        self._expect(loop_model=loop_model)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Set the repeat mode, per the ``loopModel`` mapping above."""
        loop_model = _LOOP_MODEL_BY_REPEAT.get(repeat, LOOP_MODEL_OFF)
        await self.coordinator.client.async_set_loop_mode(loop_model)
        self._expect(loop_model=loop_model)

    @property
    def source_list(self) -> list[str] | None:
        """List of available input sources, plus the CD where there is one.

        Inputs are renameable on the device, so one can already be called "CD";
        the synthetic source stands aside rather than listing the name twice.
        """
        names = [entry.name for entry in self.coordinator.data.inputs.available]
        if names and self._has_cd and CD_SOURCE not in names:
            names.append(CD_SOURCE)
        return names or None

    async def async_select_source(self, source: str) -> None:
        """Set the input source.

        A real input answers first: a user who renamed one "CD" means that
        one, and it would otherwise become unselectable. Only when no real
        input claims the name does the synthetic CD source (#36) get a look.
        """
        target = self.coordinator.data.inputs.by_name(source)
        if target is None:
            if source == CD_SOURCE and self._has_cd:
                await self._async_play_cd()
                return
            raise ServiceValidationError(f"{source} is not an input on this device")

        await self.coordinator.client.async_set_input(target.index, target.tag)
        self._expect(source=source)
        # The input list is slow-tier, so confirm the write instead of waiting
        # up to 30 s for the next settings cycle.
        await self.coordinator.async_refresh_settings()

    async def _async_play_cd(self) -> None:
        """Make an already-loaded disc audible (#36).

        ``playCDMusic`` switches the active input to the internal player by
        itself, so no prior ``async_set_input`` call is needed. The disc's
        ``uri`` is read fresh from ``getCDList`` rather than assumed — the
        device matches it by exact string equality, and a wrong or absent one
        answers ``{"status": 200}`` and silently does nothing. An empty
        ``getCDList`` is the "no disc loaded" signal: the ``CD`` source stays
        listed either way, but selecting it with nothing in the tray raises
        rather than firing a call that would just as silently no-op. This
        API is known to answer unreliably (#59), so a non-empty response
        missing ``info``/``url`` raises the same clean error rather than an
        unhandled ``KeyError``/``TypeError``.
        """
        discs = await self.coordinator.client.async_get_cd_list()
        if not discs:
            raise ServiceValidationError("No disc is loaded")
        try:
            url = discs[0]["info"]["url"]
        except (KeyError, TypeError):
            raise ServiceValidationError("No disc is loaded") from None
        await self.coordinator.client.async_play_cd_music(url, index=0)
        self._expect(source=CD_SOURCE)

    # ------------------------------------------------------------------
    # Transport.
    # ------------------------------------------------------------------

    async def async_media_play(self) -> None:
        """Send play command."""
        if self.state is not MediaPlayerState.PLAYING:
            await self._async_toggle_play_pause(MediaPlayerState.PLAYING)

    async def async_media_pause(self) -> None:
        """Send pause command."""
        if self.state is MediaPlayerState.PLAYING:
            await self._async_toggle_play_pause(MediaPlayerState.PAUSED)

    async def async_media_play_pause(self) -> None:
        """Toggle between play and pause."""
        playing = self.state is MediaPlayerState.PLAYING
        await self._async_toggle_play_pause(
            MediaPlayerState.PAUSED if playing else MediaPlayerState.PLAYING
        )

    async def _async_toggle_play_pause(self, expected: MediaPlayerState) -> None:
        """Fire the device's one play/pause toggle, if it is listening."""
        if not self._playback.can_change_play_status:
            _ignored("play/pause")
            return
        await self.coordinator.client.async_toggle_play_pause()
        self._expect(state=expected)

    async def async_media_next_track(self) -> None:
        """Send next track command."""
        if not self._playback.can_next:
            _ignored("skip to the next track")
            return
        await self.coordinator.client.async_next_title()
        # Whatever plays next, it plays from the top.
        self._expect(position=0)

    async def async_media_previous_track(self) -> None:
        """Send the previous track command."""
        if not self._playback.can_previous:
            _ignored("skip to the previous track")
            return
        await self.coordinator.client.async_previous_title()
        self._expect(position=0)

    async def async_media_seek(self, position: float) -> None:
        """Seek the media to a specific location."""
        if not self._playback.can_seek:
            _ignored("seek")
            return
        milliseconds = round(position * 1000)
        await self.coordinator.client.async_seek_time(milliseconds)
        self._expect(position=milliseconds)

    # ------------------------------------------------------------------
    # Volume.
    # ------------------------------------------------------------------

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        scale = self._volume
        if scale.maximum is None:
            LOGGER.debug("Ignoring volume: the device has not reported its range")
            return

        span = scale.maximum - scale.minimum
        await self.coordinator.client.async_set_volume(
            round(scale.minimum + volume * span)
        )
        self._expect(volume_level=volume)

    async def async_volume_up(self) -> None:
        """Volume up the Media Player."""
        # The device owns the step size, so there is nothing to guess here; the
        # next poll brings back where it landed.
        await self.coordinator.client.async_volume_up()

    async def async_volume_down(self) -> None:
        """Volume down Media Player."""
        await self.coordinator.client.async_volume_down()

    async def async_mute_volume(self, mute: bool) -> None:
        """Send mute command."""
        if mute:
            await self.coordinator.client.async_mute()
        else:
            await self.coordinator.client.async_unmute()
        self._expect(is_volume_muted=mute)

    # ------------------------------------------------------------------
    # Power. Mirrors the Power On/Off buttons: same gates, same commands.
    # ------------------------------------------------------------------

    async def async_turn_on(self) -> None:
        """Wake the device — the same Wake-on-LAN the Power On button sends.

        No guessed outcome follows: unlike an HTTP command, there is nothing
        here to have failed loudly, and the state is already ``off`` — the
        only way this is reachable — so there is no closer guess to show
        while the unit boots.
        """
        await self.coordinator.async_wake()

    async def async_turn_off(self) -> None:
        """Power the device off — the same command the Power Off button sends."""
        await self.coordinator.client.async_trigger_power_off()
        self._expect(state=MediaPlayerState.OFF)

    # ------------------------------------------------------------------
    # Browsing the local library (#47).
    # ------------------------------------------------------------------

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Browse Albums, Artists and Recently Played (see :mod:`.media_library`)."""
        return await async_browse_library(
            self.coordinator.client, media_content_type, media_content_id
        )

    # ------------------------------------------------------------------
    # Searching the local library (#49).
    # ------------------------------------------------------------------

    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        """Search the local library, returning tracks as ``browse_media`` nodes.

        ``searchMusicV2`` takes no service/platform parameter and matches
        filenames as readily as metadata, so ``media_content_type`` /
        ``media_filter_classes`` narrowing isn't honoured — see
        :func:`.media_library.async_search_library`.
        """
        results = await async_search_library(
            self.coordinator.client, query.search_query
        )
        return SearchMedia(result=results)

    # ------------------------------------------------------------------
    # Playing from the local library (#48).
    # ------------------------------------------------------------------

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a track, album or artist id from :mod:`.media_library`.

        ``replace``/``play`` (the default when ``enqueue`` is omitted) is not
        one device call: the endpoint that adds-and-plays never clears what it
        finds already queued, so a genuine replace clears the queue first —
        live-verified, RESEARCH.md 2026-08-31. ``next`` and ``add`` fire the
        one call unchanged (``_QUEUE_ACTION_BY_ENQUEUE``); neither touches the
        queue's existing contents.
        """
        queue_type = _QUEUE_CONTENT_BY_MEDIA_TYPE.get(media_type)
        if queue_type is None:
            raise ServiceValidationError(
                f"Cannot play {media_type}: only track, album and artist ids "
                "from this device's local library are supported"
            )
        try:
            item_id = int(media_id)
        except ValueError:
            raise ServiceValidationError(
                f"{media_id} is not a local library id"
            ) from None

        enqueue = kwargs.get(ATTR_MEDIA_ENQUEUE)
        if enqueue in (None, MediaPlayerEnqueue.PLAY, MediaPlayerEnqueue.REPLACE):
            await self.coordinator.client.async_clear_play_queue()
            action = QUEUE_ACTION_PLAY
        else:
            action = _QUEUE_ACTION_BY_ENQUEUE[enqueue]

        await self.coordinator.client.async_add_local_content_to_queue(
            item_id, queue_type, action
        )
        if action == QUEUE_ACTION_PLAY:
            # No guess for ``source``: the device switches to the internal
            # player by itself (docstring above), but its display name is
            # whatever the unit was configured with — the next poll reports
            # it, same as every other command here that can't know it.
            self._expect(state=MediaPlayerState.PLAYING)

    async def async_clear_playlist(self) -> None:
        """Clear the play queue."""
        await self.coordinator.client.async_clear_play_queue()
