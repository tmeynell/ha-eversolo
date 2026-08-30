"""Eversolo API Client (port-9529 Zidoo-lineage HTTP JSON API).

The device is unauthenticated, so there is no auth path here. The client is
split into three concerns:

* **Typed reads** — parse the hot ``getState``/``getModel`` payloads and the
  capability tree into the frozen models in :mod:`.data`.
* **Raw reads** — GET an endpoint and return its decoded JSON dict. Used for the
  loose, rarely-touched settings/list blobs.
* **Commands** — fire-and-forget writes (transport, volume, power, selects).

Every URL here is live-verified against a DMP-A8 Gen 2 (fw v1.1.50).
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Awaitable
from urllib.parse import quote

import aiohttp

from .const import LOGGER, POWER_TAG_SCREEN
from .data import (
    EversoloCapabilities,
    EversoloData,
    EversoloDevice,
    EversoloProcessing,
    EversoloProfile,
    EversoloVisualization,
)


class EversoloApiClientError(Exception):
    """Exception to indicate a general API error."""


class EversoloApiClientCommunicationError(EversoloApiClientError):
    """Exception to indicate a communication error."""


class EversoloApiClient:
    """Eversolo API Client."""

    def __init__(
        self,
        host: str,
        port: int,
        session: aiohttp.ClientSession,
    ) -> None:
        """Eversolo API Client."""
        self._host = host
        self._port = port
        self._session = session

    @property
    def host(self) -> str:
        """The device's configured host.

        Exposed for the cast-mode session (:mod:`.cast_session`): the handshake's own ``ip``
        field is preferred for the raw TCP connect, but a payload that omits it falls back to
        this — the same host every HTTP call here already uses.
        """
        return self._host

    def _url(self, path: str) -> str:
        """Build a full URL for a port-9529 API path."""
        return f"http://{self._host}:{self._port}{path}"

    async def _read(self, path: str) -> dict | list[dict]:
        """GET an endpoint and return its decoded JSON body (dict or array)."""
        return await self._api_wrapper(method="get", url=self._url(path))

    async def _command(self, path: str) -> None:
        """GET an endpoint whose body is fire-and-forget."""
        await self._api_wrapper(method="get", url=self._url(path), parse_json=False)

    # ------------------------------------------------------------------
    # Typed reads — the boundary where JSON becomes typed models.
    # ------------------------------------------------------------------

    async def async_read_state(self) -> EversoloData:
        """Read ``getState`` and parse the live slice into ``EversoloData``.

        Populates ``playback`` / ``volume`` / ``device``; the settings tier and
        capabilities are merged in separately by the coordinator.
        """
        payload = await self._read("/ZidooMusicControl/v2/getState")
        return EversoloData.from_state(payload)

    async def async_read_device(self) -> EversoloDevice:
        """Read ``getModel`` and parse it into a typed ``EversoloDevice``."""
        return EversoloDevice.from_model(await self.async_get_device_model())

    async def async_read_profile(
        self, processing: EversoloProcessing
    ) -> EversoloProfile:
        """Read the identity and capabilities that hold for the whole session.

        The reads are independent, so they run concurrently to keep setup
        latency down on a device that is prone to timing out under load.

        ``processing`` is the DSP/EQ slice of a ``getState`` the caller has
        already made — the DSP and EQ gates live in that payload alone, and the
        coordinator reads it every cycle regardless, so it is required here
        rather than fetched a second time. The socket list cannot be borrowed
        the same way: the settings tier is read *after* the profile, so its
        copy does not exist yet when capabilities are decided.

        The power menu is read here rather than on the settings tier because
        nothing about it changes while the unit runs: it says which power
        actions this model accepts, and none of them reports a state. It is
        also the one read here that is allowed to fail. The other four decide
        identity and most of the entity set, so losing one is worth retrying
        the whole profile for; the power menu gates a single switch, and a
        firmware that does not serve it must not cost the device every other
        capability-gated entity it has.
        """
        (
            system_settings,
            model,
            knob_option,
            input_output,
        ) = await asyncio.gather(
            self.async_get_system_settings(),
            self.async_get_device_model(),
            self.async_get_knob_setting_option(),
            self.async_get_input_output_state(),
        )
        power_option = await self._async_read_optional(self.async_get_power_option())
        return EversoloProfile(
            device=EversoloDevice.from_model(model),
            capabilities=EversoloCapabilities.detect(
                system_settings=system_settings,
                model=model,
                knob_option=knob_option,
                input_output=input_output,
                power_option=power_option,
                processing=processing,
            ),
        )

    @staticmethod
    async def _async_read_optional(read: Awaitable[dict]) -> dict | None:
        """Await a read whose absence is a smaller loss than a failed profile."""
        try:
            return await read
        except EversoloApiClientError as exception:
            LOGGER.debug("The device did not answer an optional read: %s", exception)
            return None

    # ------------------------------------------------------------------
    # Raw reads — decoded JSON dicts for loose settings/list blobs.
    # ------------------------------------------------------------------

    async def async_get_device_model(self) -> dict:
        """Fetch device model info including MAC addresses."""
        return await self._read("/ControlCenter/getModel")

    async def async_get_system_settings(self) -> dict:
        """Return the self-documenting settings tree used for capability detection."""
        return await self._read("/SystemSettings/getSystemSettings")

    async def async_get_input_output_state(self) -> dict:
        """Return the raw input/output payload; both halves are typed in ``data``."""
        return await self._read("/ZidooMusicControl/v2/getInputAndOutputList")

    async def async_get_vu_mode_state(self) -> dict:
        """Return VU mode state."""
        return await self._read("/SystemSettings/displaySettings/getVUModeList")

    async def async_get_spectrum_state(self) -> dict:
        """Return spectrum state."""
        return await self._read("/SystemSettings/displaySettings/getSpPlayModeList")

    async def async_get_power_option(self) -> dict:
        """Return the power menu — which power actions this unit accepts.

        Read for its tags alone: a ``screen`` tag says the unit has a screen
        that can be blanked. The entries carry no state field. They do carry a
        *label* ("Screen off"), and an earlier design read that as a state by
        matching it against a hard-coded list of seven localised strings —
        but nothing has ever verified that the label flips with the screen,
        and it would break on the eighth language regardless.
        """
        return await self._read("/ZidooMusicControl/v2/getPowerOption")

    async def async_get_screen_brightness(self) -> dict:
        """Return the screen brightness slider, raw.

        :class:`~.data.EversoloLevel` does the scaling, because the device
        reports its own range — ``currentValue``/``minValue``/``maxValue``,
        0..255 here — alongside the URL that writes it.
        """
        return await self._read("/SystemSettings/displaySettings/getScreenBrightness")

    async def async_get_knob_brightness(self) -> dict:
        """Return the knob brightness slider, raw — the same shape, A6 only."""
        return await self._read("/SystemSettings/displaySettings/getKnobBrightness")

    async def async_get_knob_setting_option(self) -> dict:
        """Return the raw knob setting option (``items: []`` on knob-less units)."""
        return await self._read("/SystemSettings/displaySettings/getKnobSettingOption")

    async def async_get_knob_color_state(self) -> dict:
        """Return the knob color list and current selection."""
        return await self._read("/SystemSettings/displaySettings/getKnobLightColorList")

    async def async_get_dac_filter_state(self) -> dict:
        """Return the DAC reconstruction-filter list and current selection.

        On the shared XLR+RCA analog panel — there is no separate RCA page, so
        this is device-scoped despite the ``Xlr`` in the device's own path.
        """
        return await self._read(
            "/SystemSettings/audioSettings/xlrOutputOption/getXlrOutputPcmFilterList"
        )

    async def async_get_upsampling_state(self) -> dict:
        """Return the upsampling list and current selection (same analog panel)."""
        return await self._read(
            "/SystemSettings/audioSettings/xlrOutputOption/getXlrOutputUpSamplingList"
        )

    async def async_get_master_clock_state(self) -> dict:
        """Return the master-clock list and current selection."""
        return await self._read("/SystemSettings/audioSettings/getMasterClockList")

    async def async_get_sub_output_option(self) -> dict:
        """Return the subwoofer sub-page, which carries its on/off state.

        A sub-page rather than an entry in ``getSystemSettings``: the main tree
        only points at it, so the subwoofer toggle is invisible without this.
        """
        return await self._read("/SystemSettings/audioSettings/getSubOutputOption")

    # ------------------------------------------------------------------
    # Commands — fire-and-forget writes.
    # ------------------------------------------------------------------

    async def async_set_knob_brightness(self, index: int) -> None:
        """Set the knob brightness, in the device's own 0..255 index.

        The one brightness write that is not read off its getter's ``url``, for
        the reason ``async_set_knob_color`` gives: no knob-bearing unit has been
        captured, so this stays a fixed endpoint rather than an unverifiable one.
        """
        await self._command(
            f"/SystemSettings/displaySettings/setKnobBrightness?index={index}"
        )

    async def async_trigger_reboot(self) -> None:
        """Reboots the device."""
        await self._command("/ZidooMusicControl/v2/setPowerOption?tag=reboot")

    async def async_trigger_power_off(self) -> None:
        """Powers off the device."""
        await self._command("/ZidooMusicControl/v2/setPowerOption?tag=poweroff")

    async def async_toggle_screen(self) -> None:
        """Blank the front screen, or wake it — the same call does both.

        A power-menu action tagged ``screen``, not a key press: the vendor's
        app sends ``setPowerOption`` with a tag it read out of
        ``getPowerOption``, and never the ``Key.Screen.*`` keys an earlier
        design used. It is momentary and reports nothing back, and no
        payload anywhere says whether the screen is lit, so a caller cannot
        confirm this landed.
        """
        await self._command(
            f"/ZidooMusicControl/v2/setPowerOption?tag={POWER_TAG_SCREEN}"
        )

    async def async_change_visualization(self, open_type: int) -> EversoloVisualization:
        """Toggle one of the screen's two visualizations, and report the result.

        ``openType=0`` toggles the VU meter, ``openType=1`` the spectrum; the
        two are mutually exclusive, so switching one on drops the other. The
        reply carries *both* display flags whichever was asked for, which is
        the only prompt reading of the front screen there is — ``getState``
        repeats the pair, but not until the next poll.
        """
        payload = await self._read(
            f"/ZidooMusicControl/v2/changVUDisplay?openType={open_type}"
        )
        return EversoloVisualization.from_payload(payload)

    async def async_write_setting(self, setter_url: str, value: int) -> None:
        """Write a scalar to the setter the device named for that setting.

        Every ``/SystemSettings/`` list and slider answers with the URL that
        writes it, up to and including the trailing ``?index=``; the write is
        that string with the value appended. The vendor's app does exactly
        this, and the setter names diverge from the getter names badly enough
        (``getXlrOutputPcmFilterList`` → ``setPcmFilter``) that building one
        here would eventually write to the wrong endpoint.
        """
        await self._command(f"{setter_url}{value}")

    async def async_set_knob_color(self, index: int) -> None:
        """Select the knob light colour.

        The one select that does not read its setter off the list response.
        Only the A6 has a knob, so no capture of ``getKnobLightColorList``
        exists to show whether it carries a ``url`` — so this path stays a
        fixed endpoint rather than being swapped for a mechanism nobody can
        test here. Move it onto ``async_write_setting`` once an A6 capture
        proves the ``url``.
        """
        await self._command(
            f"/SystemSettings/displaySettings/setKnobLightColor?index={index}"
        )

    async def async_set_subwoofer_output(self, enabled: bool) -> None:
        """Enable or disable the subwoofer output channel."""
        await self._command(
            "/SystemSettings/audioSettings/subOutputOption"
            f"/setSubSwitchEnable?switch={int(enabled)}"
        )

    async def async_set_gapless(self, enabled: bool) -> None:
        """Turn gapless playback on or off (device spelling: ``Gallessnew``)."""
        await self._command(
            f"/SystemSettings/playSettings/setGallessnewPlay?switch={int(enabled)}"
        )

    async def async_set_eos_engine(self, enabled: bool) -> None:
        """Turn the Eversolo Original Sampling-rate engine on or off."""
        await self._command(
            f"/SystemSettings/playSettings/setEOSEngine?switch={int(enabled)}"
        )

    async def async_set_cd_auto_play(self, enabled: bool) -> None:
        """Start playing a disc as soon as it is inserted, or stop doing that.

        The flag is an integer: ``switch=true`` is rejected with status 805.
        """
        await self._command(
            f"/SystemSettings/playSettings/setCDAutoPlay?switch={int(enabled)}"
        )

    async def async_set_auto_change_source(self, enabled: bool) -> None:
        """Auto-switch to Internal Player/Connect when they start playing.

        Does not cover Bluetooth In — the device's own description of this
        toggle limits it to the Internal Player (built-in music service and
        Connect).
        """
        await self._command(
            f"/SystemSettings/playSettings/setAutoChangeSource?switch={int(enabled)}"
        )

    async def async_mute(self) -> None:
        """Mutes the output."""
        await self._command("/ZidooMusicControl/v2/setMuteVolume?isMute=1")

    async def async_unmute(self) -> None:
        """Unmutes the output."""
        await self._command("/ZidooMusicControl/v2/setMuteVolume?isMute=0")

    async def async_volume_down(self) -> None:
        """Decreases the volume by one step."""
        await self._command(
            "/ZidooControlCenter/RemoteControl/sendkey?key=Key.VolumeDown"
        )

    async def async_volume_up(self) -> None:
        """Increases the volume by one step."""
        await self._command(
            "/ZidooControlCenter/RemoteControl/sendkey?key=Key.VolumeUp"
        )

    async def async_toggle_play_pause(self) -> None:
        """Toggles between play and pause."""
        await self._command("/ZidooMusicControl/v2/playOrPause")

    async def async_previous_title(self) -> None:
        """Plays the previous title."""
        await self._command("/ZidooMusicControl/v2/playLast")

    async def async_next_title(self) -> None:
        """Plays the next title."""
        await self._command("/ZidooMusicControl/v2/playNext")

    async def async_seek_time(self, time: int) -> None:
        """Seeks to a time given in milliseconds."""
        await self._command(f"/ZidooMusicControl/v2/seekTo?time={time}")

    async def async_set_volume(self, volume: int) -> None:
        """Set the volume."""
        await self._command(f"/ZidooMusicControl/v2/setDevicesVolume?volume={volume}")

    async def async_set_input(self, index: int, tag: str) -> None:
        """Set the input/source."""
        await self._command(
            f"/ZidooMusicControl/v2/setInputList?tag={tag}&index={index}"
        )

    async def async_set_output(self, index: int, tag: str) -> None:
        """Set the output."""
        await self._command(
            f"/ZidooMusicControl/v2/setOutInputList?tag={tag}&index={index}"
        )

    async def async_get_cd_list(self) -> list[dict]:
        """Return the loaded disc(s), or an empty list when the tray is empty.

        Each entry carries ``info.url`` — the value ``async_play_cd_music``'s
        ``uri`` must match exactly, no normalisation applied server-side — and
        ``info.name``. An empty list is the "no disc loaded" signal: the
        drive-capability flag only says the unit *has* a drive, not that one
        is *in* it.
        """
        return await self._read("/ZidooMusicControl/v2/getCDList")

    async def async_play_cd_music(self, uri: str, index: int) -> None:
        """Make the disc at ``uri`` audible, starting at its ``index``-th track.

        Switches the active input to the internal player by itself — no prior
        ``async_set_input`` call is needed (#36). ``uri`` must be a value read
        from ``async_get_cd_list``'s own ``info.url``, matched by the device
        server-side with exact string equality; a wrong ``uri`` or an
        out-of-range ``index`` both answer ``{"status": 200}`` and do nothing,
        consistent with this API's habit of lying in both directions — only a
        read-back proves this landed.
        """
        await self._command(
            f"/ZidooMusicControl/v2/playCDMusic?uri={quote(uri, safe='')}&index={index}"
        )

    # ------------------------------------------------------------------
    # Image URL helpers.
    # ------------------------------------------------------------------

    def create_image_url_by_song_id(self, song_id, music_type: int | None) -> str:
        """Create url to fetch album covers when using the internal player.

        ``musicType`` and ``type`` are both required — the endpoint 806s
        ("The resource does not exist") with only ``id`` and ``target``,
        which is what silently broke disc covers (#22). Recovered from the
        control app's own cover-loading code (``MusicImageLoader.music()``),
        which builds this exact URL from a ``playingMusic``-shaped object:
        ``musicType`` echoes the item's own ``type`` field, while ``type=4``
        is the app's constant for this call (``TYPE_AUTO``) and is not
        derived from anything. Live-verified against the A8: the old URL
        answers ``806``, the new one returns the disc's actual cover.
        """
        music_type = 0 if music_type is None else music_type
        return self._url(
            f"/ZidooMusicControl/v2/getImage?id={song_id}&musicType={music_type}"
            "&type=4&target=16"
        )

    def create_image_url_by_path(self, path) -> str:
        """Create url to fetch album covers when using AirPlay by concatting the path."""
        return self._url(path)

    def create_image_url_or_none(self, path: str | None) -> str | None:
        """Resolve a nullable device icon path to a URL, or None if there is none.

        The one check every ``entity_picture`` backed by a device icon field
        needs before ``create_image_url_by_path`` — shared rather than
        repeated at each call site (#16).
        """
        return self.create_image_url_by_path(path) if path else None

    async def async_start_cast_session(self) -> dict:
        """Open a cast-mode session and return its handshake (#38).

        ``mode=1`` is the passive screen-mirror the phone/web apps use for live viewing — read-only
        by construction, unlike ``getScreenShot`` (the transport this replaces): opening a session
        and reading frames off the socket it hands back never wakes the panel or shows anything on
        it (RESEARCH.md's 2026-08-30 entry). The reply carries the TCP port to connect to next
        (reallocated per session, never fixed) plus the stream's own ``videoWidth``/``videoHeight``
        — see :class:`.cast_session.CastHandshake`, which parses this payload.
        """
        return await self._read("/ZidooControlCenter/setcastmode?mode=1&version=1")

    async def async_stop_cast_session(self, port: int) -> None:
        """Tear down a cast-mode session opened with :meth:`async_start_cast_session`.

        Best-effort from the caller's point of view: the device frees the port on socket close
        regardless (verified live, RESEARCH.md), so a caller that cannot reach this — the device
        already went away — has not leaked anything on the device side either.
        """
        await self._command(f"/ZidooControlCenter/setcastmode?mode=0&port={port}")

    # ------------------------------------------------------------------
    # Transport.
    # ------------------------------------------------------------------

    async def _api_wrapper(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
        parse_json: bool = True,
    ) -> dict | bytes:
        """Get information from the API.

        Only communication faults are translated into a typed comms error; any
        other exception is left to propagate so real bugs are not masked.
        """
        try:
            async with asyncio.timeout(5):
                response = await self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data,
                )
                response.raise_for_status()
                if parse_json:
                    return await response.json(content_type=None)
                return await response.read()

        except TimeoutError as exception:
            raise EversoloApiClientCommunicationError(
                f"Timeout error fetching information from {url}",
            ) from exception
        except (aiohttp.ClientError, socket.gaierror) as exception:
            raise EversoloApiClientCommunicationError(
                f"Error fetching information from {url}",
            ) from exception
        except json.JSONDecodeError as exception:
            # A truncated body or an HTML error page is a comms fault, not a bug;
            # classify it so the coordinator degrades gracefully instead of
            # logging an unexpected-error traceback on every poll.
            raise EversoloApiClientCommunicationError(
                f"Invalid JSON response from {url}",
            ) from exception
