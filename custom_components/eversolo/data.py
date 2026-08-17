"""Typed data boundary for the Eversolo integration.

The port-9529 API returns loosely-typed JSON. This module parses the hot
``getState`` payload and ``getModel`` into frozen dataclasses so entities read
named, typed attributes instead of digging through raw nested dicts. Only the
hot path is fully typed; rarely-touched settings blobs stay loose in
``EversoloData.settings``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any
from collections.abc import Iterator, Mapping

from .const import (
    INPUT_INTERNAL_PLAYER,
    POWER_TAG_SCREEN,
    SETTING_TAG_ANALOG_PANEL,
    SETTING_TAG_CD_AUTO_PLAY,
    SETTING_TAG_EOS_ENGINE,
    SETTING_TAG_GAPLESS,
    SETTING_TAG_KNOB_COLOR,
    SETTING_TAG_MASTER_CLOCK,
    SETTING_TAG_SCREEN_BRIGHTNESS,
    SETTING_TAG_SPECTRUM_MODE,
    SETTING_TAG_SUBWOOFER,
    SETTING_TAG_VU_MODE,
)


def _as_int(value: Any) -> int | None:
    """Coerce a device value (int or numeric string) to int, else None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _as_measurement(value: Any) -> int | None:
    """Coerce a rate/depth field to int, treating zero as "not reported".

    The device zeroes the whole streaming block on inputs that carry no stream
    of their own (TV/eARC), and a 0 Hz sample rate is absence, not a reading.
    """
    number = _as_int(value)
    return number if number else None


def _as_flag(value: Any) -> bool | None:
    """Coerce a device flag to bool, keeping "not reported" as None.

    Distinct from ``bool(...)``, which turns a field the device never sent into
    a confident False.
    """
    return None if value is None else bool(value)


def _first(*values: Any) -> Any:
    """Return the first value that is neither None nor an empty string."""
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _iter_setting_items(node: Any) -> Iterator[Mapping[str, Any]]:
    """Yield every tagged node anywhere in a getSystemSettings tree."""
    if isinstance(node, dict):
        if isinstance(node.get("tag"), str):
            yield node
        for value in node.values():
            yield from _iter_setting_items(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_setting_items(item)


def _iter_setting_tags(system_settings: Mapping[str, Any] | None) -> set[str]:
    """Collect every ``tag`` string anywhere in a getSystemSettings tree.

    The tree is self-documenting: a feature's ``SettingsItemTag*`` only appears
    on units that actually expose it, so tag presence is the capability signal.
    """
    return {item["tag"] for item in _iter_setting_items(system_settings)}


@dataclass(frozen=True, slots=True)
class EversoloToggles:
    """The state of every ``?switch=`` toggle the settings tree reports.

    Keyed by settings tag rather than given a field each, because the switch
    platform is description-driven: an entity names the tag it belongs to, and
    a unit only lists the toggles its hardware has.
    """

    states: Mapping[str, bool] = field(default_factory=dict)

    def is_on(self, tag: str) -> bool | None:
        """Whether one toggle is on, or None if this unit never reported it."""
        return self.states.get(tag)

    @classmethod
    def from_settings(cls, *trees: Mapping[str, Any] | None) -> EversoloToggles:
        """Parse the toggles out of one or more settings trees.

        The tree is the only place the device reports these — there is no
        per-toggle getter — so it is polled on the settings tier. Entries that
        are not toggles carry no ``switchStatus`` and are left out entirely.

        More than one tree because ``getSystemSettings`` is not the whole of
        it: an ``option: 3`` entry is a *sub-page* fetched separately, and the
        subwoofer's own on/off is in one of those rather than in the main tree.
        Tags are unique across the pages, so the maps merge cleanly.
        """
        return cls(
            states={
                item["tag"]: item["switchStatus"]
                for tree in trees
                for item in _iter_setting_items(tree)
                if isinstance(item.get("switchStatus"), bool)
            }
        )


@dataclass(frozen=True, slots=True)
class EversoloPlayback:
    """Now-playing slice parsed from ``getState``."""

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    extension: str | None = None
    art_url: str | None = None
    song_id: int | None = None
    # ``playingMusic.type`` — the device's own tag for the loaded item, echoed
    # back verbatim as the cover fetch's ``musicType`` (see api.py).
    music_type: int | None = None
    play_status: int | None = None
    position: int | None = None  # milliseconds
    duration: int | None = None  # milliseconds
    track_index: int | None = None
    can_seek: bool = False
    can_next: bool = False
    can_previous: bool = False
    can_change_play_status: bool = False
    codec: str | None = None
    sample_rate: int | None = None  # Hz
    bit_depth: int | None = None
    # Usually the disc's preformatted string ("1.41 Mbps"); falls back to the
    # streaming block's raw ``audioBitrate`` int when there is no disc to ask.
    bitrate: str | int | None = None
    channels: int | None = None

    @property
    def is_playing(self) -> bool:
        """True when the device reports active playback."""
        return self.play_status == 1

    @property
    def is_cd(self) -> bool:
        """True when the active media is a CD (``extension == "cd"``)."""
        return (self.extension or "").lower() == "cd"

    @property
    def has_media(self) -> bool:
        """True when a track is loaded, playing or not.

        Separates "paused on something" from "nothing to play". ``playStatus``
        is ``0`` (stopped), ``1`` (playing) or ``2`` (paused) — both ``0`` and
        ``2`` correctly fall through to "paused" here while a title is loaded,
        since the device draws no line between the two.
        """
        return bool(self.title or self.duration)

    @property
    def format_label(self) -> str | None:
        """The stream quality as one line, e.g. ``PCM 44.1kHz/16bit``.

        Built from whichever parts the device reports; None when it reports
        none of them, so the sensor reads unknown instead of an empty stub.
        """
        quality = "/".join(
            part
            for part in (
                f"{self.sample_rate / 1000:g}kHz" if self.sample_rate else None,
                f"{self.bit_depth}bit" if self.bit_depth else None,
            )
            if part
        )
        return " ".join(part for part in (self.codec, quality) if part) or None

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> EversoloPlayback:
        """Parse the playback slice from a raw ``getState`` payload."""
        music = state.get("playingMusic") or {}
        info = state.get("everSoloPlayInfo") or {}
        audio = info.get("everSoloPlayAudioInfo") or {}
        output = info.get("everSoloPlayOutputInfo") or {}

        # The device keeps ``playingMusic`` describing a loaded disc even
        # while a different input is live (#03) — it never clears the block
        # on an input switch, so a disc left in the tray is misreported as
        # what is currently playing on TV/eARC/BT/SPDIF. ``volumeData``
        # rides the same payload and its ``intputTag`` moves in step with the
        # input (unlike the settings-tier input list, which trails a write by
        # up to a poll), so comparing the two here is self-consistent. One
        # guard, applied by blanking the whole block, rather than each field
        # below re-deciding whether to trust it.
        #
        # A payload that omits ``volumeData`` (or its ``intputTag``) has not
        # said the disc is wrong, only that it has not said anything — that
        # is "unknown", not "no", so it leaves the block trusted rather than
        # blanking a real, live disc for want of a field to check it against.
        raw_input_tag = (state.get("volumeData") or {}).get("intputTag")
        live_input = raw_input_tag.split("-")[0] if raw_input_tag else None
        if (
            (music.get("extension") or "").lower() == "cd"
            and live_input is not None
            and live_input != INPUT_INTERNAL_PLAYER
        ):
            music = {}

        return cls(
            title=_first(music.get("title"), audio.get("songName")),
            artist=_first(music.get("artist"), audio.get("artistName")),
            album=_first(music.get("album"), audio.get("albumName")),
            extension=music.get("extension"),
            art_url=_first(
                info.get("icon"), music.get("albumArt"), audio.get("albumUrl")
            ),
            song_id=_as_int(music.get("id")),
            music_type=_as_int(music.get("type")),
            play_status=_as_int(info.get("playStatus")),
            position=_first(
                _as_int(info.get("currentPosition")), _as_int(state.get("position"))
            ),
            duration=_first(
                _as_int(info.get("duration")), _as_int(state.get("duration"))
            ),
            track_index=_as_int(state.get("trackIndex")),
            can_seek=bool(info.get("isCanSeek", False)),
            can_next=bool(info.get("isCanNextPlay", False)),
            can_previous=bool(info.get("isCanLastPlay", False)),
            can_change_play_status=bool(info.get("isCanChangePlayStatus", False)),
            # The streaming block describes the incoming stream and is empty on
            # inputs that carry none (TV/eARC), where the output block — what
            # the DAC is actually converting — is the only report left.
            codec=_first(audio.get("audioDecodec"), output.get("outPutDecodec")),
            sample_rate=_first(
                _as_measurement(audio.get("audioSampleRate")),
                _as_measurement(music.get("sampleRateNumber")),
                _as_measurement(output.get("outPutSampleRate")),
            ),
            bit_depth=_first(
                _as_measurement(audio.get("audioBitsPerSample")),
                _as_measurement(music.get("bits")),
                _as_measurement(output.get("outPutBits")),
            ),
            # No output-block equivalent exists for bitrate (it never reports
            # one), so this chain has one fewer link than its neighbours.
            bitrate=_first(
                _as_measurement(audio.get("audioBitrate")), music.get("bitrate")
            ),
            channels=_first(
                _as_measurement(music.get("channels")),
                _as_measurement(audio.get("audioChannels")),
                _as_measurement(output.get("outPutChannels")),
            ),
        )


@dataclass(frozen=True, slots=True)
class EversoloProcessing:
    """The DSP/EQ block of ``getState``: what the unit has, and what is engaged.

    The two sides are parallel, independently configured feature sets: DSP is
    applied to the four *inputs* (``XMOS``, ``BT``, ``SPDIF``, ``EARC``), EQ to
    the three digital *outputs*. A unit may have one, both or neither.

    ``dsp_active`` is **per-input, not global** — it reports the state of
    whichever input is selected right now, so changing source can flip it with
    nothing else having changed. Verified four-for-four against a deliberately
    mixed configuration.

    The two ``has_*`` flags are lifted into :class:`EversoloCapabilities`,
    which is the one place entities read a gate from; they are parsed here
    because ``getState`` is parsed here and this is the payload that carries
    them. They appear nowhere in the settings tree — DSP lives under
    ``/ZidooMusicControl/v2/``, which is why the tree has no entry for it.

    **All four fields are tri-state**, including the two ``has_*`` ones. Every
    other capability is decided by an endpoint that either answers or raises,
    so "did not say" arrives as an exception and is retried. These two are
    *fields*, and a payload that omits one is indistinguishable from a no
    unless the absence is carried as its own value. It is the coordinator that
    decides how long to wait for the answer and what to assume if it never
    comes; nothing here coerces the silence.
    """

    # None until the device has answered: no reading is neither "it is off" nor
    # "this unit does not have one".
    has_dsp: bool | None = None
    has_eq: bool | None = None
    dsp_active: bool | None = None
    eq_active: bool | None = None

    @property
    def reports_capabilities(self) -> bool:
        """Whether this payload answered the DSP *and* EQ questions at all.

        Both, not either: the pair shares a block, so a cycle carrying only
        ``hasEQSetting`` has not said anything about DSP, and settling the DSP
        gate off it would be the same mistake one field along.
        """
        return self.has_dsp is not None and self.has_eq is not None

    def retaining_gates_from(self, earlier: EversoloProcessing) -> EversoloProcessing:
        """Return this reading with gates an earlier one answered filled in.

        Only the two ``has_*`` gates are carried forward, and only into fields
        this reading left unanswered: they describe hardware, so a payload that
        omits one has not unsaid an earlier payload that carried it. The two
        ``*_active`` readings are deliberately *not* accumulated — those
        describe the moment, and a remembered one would be a lie rather than a
        memory.

        Without this, a device that answered the pair one field at a time,
        never both in the same cycle, would answer the question over and over
        and still have every answer thrown away when the wait ran out.
        """
        return replace(
            self,
            has_dsp=_first(self.has_dsp, earlier.has_dsp),
            has_eq=_first(self.has_eq, earlier.has_eq),
        )

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> EversoloProcessing:
        """Parse the DSP/EQ flags from a raw ``getState`` payload."""
        return cls(
            has_dsp=_as_flag(state.get("hasDspSetting")),
            has_eq=_as_flag(state.get("hasEQSetting")),
            dsp_active=_as_flag(state.get("dspActive")),
            eq_active=_as_flag(state.get("eqActive")),
        )


class EversoloVisualizationMode(StrEnum):
    """What the front screen can be showing instead of the now-playing view.

    These are the values the select reports and accepts, so they are slugs
    rather than labels: the wording lives in ``strings.json`` under the
    select's ``state`` block, which is what makes this the one option list here
    that can be translated. Every other select offers labels the device itself
    supplies, which nothing in this repo can translate.
    """

    OFF = "off"
    VU_METER = "vu_meter"
    SPECTRUM = "spectrum"


@dataclass(frozen=True, slots=True)
class EversoloVisualization:
    """The front screen's two display flags, as ``getState`` reports them.

    ``changVUDisplay`` answers with the same two fields, so its reply parses
    here too — which is how a write learns what it actually did on a device
    that reports the pair nowhere else.

    **``0`` and ``-1`` are both read as "neither showing".** Live testing saw
    the pair sit at ``0/0`` from the regular now-playing view and drop to
    ``-1/-1`` after switching the last visualization back off; whether those
    are one state or two was never pinned down, and the APK cannot answer it
    (the only code path that names this endpoint is uncalled). Treating both as
    Off is the reading that cannot claim a visualization the screen is not
    showing. ``RESEARCH.md`` has the sequences.
    """

    vu_mode: int | None = None
    spectrum_mode: int | None = None

    @property
    def mode(self) -> EversoloVisualizationMode | None:
        """Which visualization is up, or None while the device has not said."""
        if self.vu_mode is None and self.spectrum_mode is None:
            return None
        if (self.spectrum_mode or 0) >= 1:
            return EversoloVisualizationMode.SPECTRUM
        if (self.vu_mode or 0) >= 1:
            return EversoloVisualizationMode.VU_METER
        return EversoloVisualizationMode.OFF

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> EversoloVisualization:
        """Parse the flag pair out of a ``getState`` or ``changVUDisplay`` reply."""
        payload = payload or {}
        return cls(
            vu_mode=_as_int(payload.get("vuDisplayMode")),
            spectrum_mode=_as_int(payload.get("spDisplayMode")),
        )


@dataclass(frozen=True, slots=True)
class EversoloLevel:
    """A slider setting: where it sits, how far it goes, and where to write it.

    ``percent`` is ``current / maximum``, ignoring ``minimum`` — that is the
    arithmetic the vendor's app uses, and the device agrees with it, rendering
    ``currentValue: 30, maxValue: 255`` as "11%". Following the device here
    matters more than the more defensible span-relative reading would: a
    percentage that disagreed with the one on the unit's own screen would look
    like a bug on every glance.

    ``setter_url`` comes out of the response for the same reason it does in
    :class:`EversoloOptionList` — the device names its own setter, so nothing
    here has to guess one.
    """

    current: int | None = None
    minimum: int = 0
    maximum: int | None = None
    setter_url: str | None = None

    @property
    def percent(self) -> int | None:
        """The level as a whole 0..100, or None until the device reports a range.

        Whole numbers because the slider these feed steps in ones: an
        unrounded 11.764705882352942 would never rest on a step it declares,
        and would defeat a template comparing the state to a number.
        """
        if self.current is None or not self.maximum:
            return None
        return round(max(0.0, min(100.0, self.current / self.maximum * 100)))

    def index_for(self, percent: float) -> int | None:
        """Return the device index a percentage means, or None if unknown.

        Clamped to the reported range at both ends. ``minimum`` matters on the
        write side even though ``percent`` ignores it: 0 % on a slider whose
        floor is not zero would otherwise write below what the device declared.
        """
        if not self.maximum:
            return None
        index = round(max(0.0, min(100.0, percent)) * self.maximum / 100)
        return max(self.minimum, index)

    def assuming_maximum(self, maximum: int) -> EversoloLevel:
        """Return this level with a range assumed, if the device reported none.

        For a slider on hardware nobody here has captured, where failing
        closed — no reading, and every write refused — is worse than working
        from an assumed range and letting the write through.
        """
        if self.maximum:
            return self
        return replace(self, maximum=maximum)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> EversoloLevel:
        """Parse the ``{currentValue, minValue, maxValue, url}`` slider shape.

        The keys are ``minValue``/``maxValue``, not ``min``/``max``.
        """
        payload = payload or {}
        return cls(
            current=_as_int(payload.get("currentValue")),
            minimum=_as_int(payload.get("minValue")) or 0,
            maximum=_as_int(payload.get("maxValue")),
            setter_url=payload.get("url") or None,
        )


@dataclass(frozen=True, slots=True)
class EversoloVolume:
    """Volume slice parsed from ``getState.volumeData``."""

    current: int | None = None
    minimum: int = 0
    maximum: int | None = None
    is_muted: bool = False
    display: str | None = None
    input_tag: str | None = None
    is_enabled: bool = True

    @property
    def selected_input_tag(self) -> str | None:
        """The bare input tag, out of the device's compound ``XMOS-XMOS`` form."""
        if not self.input_tag:
            return None
        return self.input_tag.split("-")[0]

    @property
    def level(self) -> float | None:
        """Volume as a 0..1 fraction for Home Assistant, or None if unknown."""
        if self.current is None or self.maximum is None:
            return None
        span = self.maximum - self.minimum
        if span <= 0:
            return None
        return max(0.0, min(1.0, (self.current - self.minimum) / span))

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> EversoloVolume:
        """Parse the volume slice from a raw ``getState`` payload."""
        data = state.get("volumeData") or {}
        return cls(
            # Device spells it "currenttVolume" (sic).
            current=_as_int(data.get("currenttVolume")),
            minimum=_as_int(data.get("minVolume")) or 0,
            maximum=_as_int(data.get("maxVolume")),
            is_muted=bool(data.get("isMute", False)),
            display=data.get("display"),
            input_tag=data.get("intputTag"),
            is_enabled=bool(data.get("isVolumeEnable", True)),
        )


@dataclass(frozen=True, slots=True)
class EversoloInput:
    """One selectable hardware input.

    ``index`` is what the device wants back in ``setInputList``; it is not
    necessarily the position in the list, so it is carried rather than derived.
    """

    tag: str
    name: str
    index: int


@dataclass(frozen=True, slots=True)
class EversoloInputs:
    """The unit's hardware inputs, and which one is live."""

    available: tuple[EversoloInput, ...] = ()
    current_index: int | None = None

    @property
    def current(self) -> EversoloInput | None:
        """The input the device is listening to, if it has said.

        ``inputIndex`` from the settings tier is the only reading used, even
        though ``getState.volumeData.intputTag`` names the live input every 5 s.
        Preferring the faster one would undo each write for a moment: selecting
        a source re-reads the settings tier immediately, and the live tag would
        still be naming the old input for up to a poll afterwards.
        """
        if self.current_index is None:
            return None
        return next(
            (entry for entry in self.available if entry.index == self.current_index),
            None,
        )

    def by_name(self, name: str) -> EversoloInput | None:
        """Find an input by the label the device shows for it."""
        return next((entry for entry in self.available if entry.name == name), None)

    def by_tag(self, tag: str) -> EversoloInput | None:
        """Find an input by its stable tag (``XMOS``, ``BT``, ``SPDIF``, ``EARC``)."""
        return next((entry for entry in self.available if entry.tag == tag), None)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> EversoloInputs:
        """Parse ``getInputAndOutputList``'s input half."""
        payload = payload or {}
        inputs = []

        for position, item in enumerate(payload.get("inputData") or []):
            tag = item.get("tag")
            if not isinstance(tag, str) or not tag:
                continue
            index = _as_int(item.get("index"))
            inputs.append(
                EversoloInput(
                    tag=tag,
                    # A renamed input ("Record Player" for SPDIF) is what the
                    # user picked, so the label wins over the tag.
                    name=item.get("name") or tag,
                    index=position if index is None else index,
                )
            )

        return cls(
            available=tuple(inputs),
            current_index=_as_int(payload.get("inputIndex")),
        )


@dataclass(frozen=True, slots=True)
class EversoloOption:
    """One choice in a device list, with whatever a write has to quote back.

    ``index`` is the device's own number for the entry, never the position of
    the entry in the list we happen to show: the output list hides the sockets
    this unit has disabled, and re-numbering what is left would route audio to
    the wrong one.
    """

    title: str
    index: int
    tag: str | None = None


@dataclass(frozen=True, slots=True)
class EversoloOptionList:
    """A list endpoint's choices, which one is live, and where to write it.

    ``setter_url`` is read out of the response's own ``url`` field rather than
    built from the getter's name. The names do not correspond: the list at
    ``getXlrOutputPcmFilterList`` is written by ``setPcmFilter``, and
    ``getSpPlayModeList`` by ``setSpPlayModeList`` — a setter that keeps the
    ``List``. Guessing either way is a silent wrong write, so the device is
    asked. This is what the vendor's own app does; it never builds these URLs.
    """

    options: tuple[EversoloOption, ...] = ()
    current_index: int | None = None
    setter_url: str | None = None

    @property
    def titles(self) -> list[str]:
        """The labels to offer, in the order the device listed them."""
        return [option.title for option in self.options]

    @property
    def current(self) -> EversoloOption | None:
        """The option the device says is selected, if it has said."""
        if self.current_index is None:
            return None
        return next(
            (option for option in self.options if option.index == self.current_index),
            None,
        )

    def by_title(self, title: str) -> EversoloOption | None:
        """Find the option behind a label the user picked."""
        return next((option for option in self.options if option.title == title), None)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> EversoloOptionList:
        """Parse the ``{currentIndex, url, data[]}`` shape every list endpoint uses."""
        payload = payload or {}
        options = []

        for position, item in enumerate(payload.get("data") or []):
            title = item.get("title")
            if not title:
                continue
            index = _as_int(item.get("index"))
            options.append(
                EversoloOption(title=title, index=position if index is None else index)
            )

        return cls(
            options=tuple(options),
            current_index=_as_int(payload.get("currentIndex")),
            setter_url=payload.get("url") or None,
        )

    @classmethod
    def from_outputs(cls, payload: Mapping[str, Any] | None) -> EversoloOptionList:
        """Parse the output half of ``getInputAndOutputList`` into the same shape.

        Routing is the one select that is not a ``/SystemSettings/`` list: it
        carries no ``url``, and is written by ``setOutInputList?tag=&index=``
        instead. It is still one-of-these, so it is modelled the same way.

        Sockets the unit reports as disabled — a USB DAC with nothing plugged
        in — are not offered, but they still *count*: ``index`` is the position
        in the raw list, because that is how the device numbers them and how it
        reports ``outputIndex`` back.

        Hiding them is a deliberate departure from the vendor's app, which
        lists every socket and refuses the tap on a disabled one. Home
        Assistant has no equivalent of refusing a tap, so the same outcome —
        you cannot route to a socket the unit says is dead — is reached by not
        offering it. The consequence to know about: a unit whose
        ``outputIndex`` points at a socket it also calls disabled reports no
        current option rather than a name that cannot be chosen back.
        """
        payload = payload or {}
        options = [
            EversoloOption(
                title=item.get("name") or item["tag"],
                index=position,
                # Carried from the inherited client, which stripped this before
                # putting the tag on a query string. No captured tag contains a
                # slash — only the display names do — so this is defensive
                # against firmware nobody here has seen.
                tag=item["tag"].replace("/", ""),
            )
            for position, item in enumerate(payload.get("outputData") or [])
            if isinstance(item.get("tag"), str) and item["tag"] and item.get("enable")
        ]

        return cls(
            options=tuple(options),
            current_index=_as_int(payload.get("outputIndex")),
        )


@dataclass(frozen=True, slots=True)
class EversoloDevice:
    """Device identity. Rich form from ``getModel``, light form from ``getState``."""

    name: str | None = None
    model: str | None = None
    net_mac: str | None = None
    wif_mac: str | None = None
    firmware: str | None = None
    android_version: str | None = None

    @classmethod
    def from_model(cls, model: Mapping[str, Any]) -> EversoloDevice:
        """Parse the full identity from a raw ``getModel`` payload.

        Identity only: what the unit *can do* — including ``getModel``'s own
        ``ableRemote*`` power flags — is :class:`EversoloCapabilities`, so the
        gates entities read have one representation rather than two.
        """
        return cls(
            name=_first(model.get("deviceName"), model.get("model")),
            model=model.get("model"),
            net_mac=model.get("net_mac"),
            wif_mac=model.get("wif_mac"),
            firmware=model.get("firmware"),
            android_version=model.get("androidversion"),
        )

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> EversoloDevice:
        """Parse the light identity carried inside ``getState.deviceInfo``.

        Thinner than ``getModel``'s: no Android version, no WiFi MAC, which is
        why a profile read supersedes it.
        """
        info = state.get("deviceInfo") or {}
        return cls(
            name=info.get("deviceName"),
            model=info.get("model"),
            net_mac=info.get("net_mac"),
            firmware=info.get("version"),
        )


@dataclass(frozen=True, slots=True)
class EversoloCapabilities:
    """Which optional features this unit exposes.

    Detected once from the self-documenting ``getSystemSettings`` tree plus the
    ``getModel`` power flags, so entity creation can be gated without brittle
    model-string matching.

    Every field is a plain bool, deliberately: this is the settled answer that
    entities are gated on, and "we do not know yet" is expressed by there being
    no ``EversoloCapabilities`` at all — :class:`EversoloData` leaves it None
    until the coordinator has one it is prepared to stand behind. A tri-state
    here would push that distinction into every platform.

    This is also the only place a gate is read from. :class:`EversoloProcessing`
    carries the raw ``hasDspSetting`` / ``hasEQSetting`` readings it is fed
    from, and those keep reporting whatever the current cycle said; if the two
    ever disagree, this one is the gate and that one is the reading.
    """

    has_cd: bool = False
    has_subwoofer: bool = False
    has_master_clock: bool = False
    has_analog_panel: bool = False
    has_gapless: bool = False
    has_eos_engine: bool = False
    has_output_routing: bool = False
    has_reboot: bool = False
    has_power_off: bool = False
    has_knob: bool = False
    has_knob_color: bool = False
    has_dsp: bool = False
    has_eq: bool = False
    has_screen_power: bool = False
    has_screen_brightness: bool = False
    has_vu_style: bool = False
    has_spectrum_style: bool = False

    @property
    def has_visualization(self) -> bool:
        """Whether the screen has anything to show instead of now-playing.

        Either style list being in the tree is enough: the mode itself is
        chosen with one endpoint that covers both.
        """
        return self.has_vu_style or self.has_spectrum_style

    def with_processing(self, processing: EversoloProcessing) -> EversoloCapabilities:
        """Return these capabilities with the two ``getState`` gates decided.

        Separate from :meth:`detect` because these two arrive on a different
        schedule from every other gate: they are fields of a payload polled
        every cycle rather than answers from an endpoint read once. A flag the
        device has not reported settles False here, which is only sound once
        the caller has decided to stop waiting for it — the coordinator owns
        that decision, and bounds it.
        """
        return replace(
            self,
            has_dsp=bool(processing.has_dsp),
            has_eq=bool(processing.has_eq),
        )

    @classmethod
    def detect(
        cls,
        *,
        system_settings: Mapping[str, Any] | None = None,
        model: Mapping[str, Any] | None = None,
        knob_option: Mapping[str, Any] | None = None,
        input_output: Mapping[str, Any] | None = None,
        power_option: Mapping[str, Any] | None = None,
        processing: EversoloProcessing | None = None,
    ) -> EversoloCapabilities:
        """Derive capabilities from the settings tree, model, knob probe and state.

        ``processing`` is handed over already parsed, unlike the raw payloads
        beside it: those are read for capability detection alone, whereas
        ``getState`` is parsed every cycle for the hot path anyway, so this
        takes the slice rather than parsing the same payload twice.

        The result is **provisional in its two ``getState`` gates** if that
        slice did not report them — see :meth:`with_processing`. The
        coordinator is what decides when the answer is final, and does not
        publish capabilities to entities before then.
        """
        tags = _iter_setting_tags(system_settings)
        model = model or {}
        knob_items = (knob_option or {}).get("items") or []
        processing = processing or EversoloProcessing()

        return cls(
            has_cd=SETTING_TAG_CD_AUTO_PLAY in tags,
            # The tree tag rather than ``getState.hasSubSetting``, which names
            # the same hardware: the tag is what carries the sub-page URL the
            # subwoofer switch reads its state out of, so gating on it keeps
            # the entity and its only data source deciding together.
            has_subwoofer=SETTING_TAG_SUBWOOFER in tags,
            has_master_clock=SETTING_TAG_MASTER_CLOCK in tags,
            has_analog_panel=SETTING_TAG_ANALOG_PANEL in tags,
            has_gapless=SETTING_TAG_GAPLESS in tags,
            has_eos_engine=SETTING_TAG_EOS_ENGINE in tags,
            has_screen_brightness=SETTING_TAG_SCREEN_BRIGHTNESS in tags,
            has_vu_style=SETTING_TAG_VU_MODE in tags,
            has_spectrum_style=SETTING_TAG_SPECTRUM_MODE in tags,
            # Routing is not in the settings tree at all — the analog panel the
            # tree does carry holds DAC filter, upsampling and volume limits,
            # and no routing control — so the socket list is its own gate.
            has_output_routing=bool((input_output or {}).get("outputData")),
            has_reboot=bool(model.get("ableRemoteReboot", False)),
            has_power_off=bool(model.get("ableRemoteShutdown", False)),
            # "Knob present at all" — a superset of knob *colour* support. The A8
            # has no knob, so this list is empty; only the A6 populates it.
            has_knob=bool(knob_items),
            has_knob_color=any(
                item.get("tag") == SETTING_TAG_KNOB_COLOR for item in knob_items
            ),
            # The screen is not in the settings tree either: the only thing
            # that says this unit has one to switch off is the power menu
            # offering the tag that switches it.
            has_screen_power=any(
                item.get("tag") == POWER_TAG_SCREEN
                for item in (power_option or {}).get("data") or []
            ),
            # DSP and EQ are set by ``with_processing`` rather than listed here,
            # because they are the two gates that can still be undecided at this
            # point: a ``getState`` may simply not carry them.
        ).with_processing(processing)


@dataclass(frozen=True, slots=True)
class EversoloProfile:
    """What one setup-time read establishes: who the device is and what it can do.

    Read once (and retried until it succeeds) rather than every poll — neither
    half changes while the unit is running.

    The two halves are latched independently once they get here, though. This
    is the shape of one read, not of one decision: ``device`` is final as soon
    as it lands, while ``capabilities`` may still be waiting on the DSP/EQ
    flags that only ``getState`` carries.
    """

    device: EversoloDevice
    capabilities: EversoloCapabilities


@dataclass(frozen=True, slots=True)
class EversoloData:
    """Typed container the coordinator hands to entities.

    ``playback`` / ``volume`` / ``device`` / ``processing`` / ``visualization``
    come from ``getState``; ``inputs``
    and ``toggles`` are parsed out of the settings tier; ``settings`` still
    holds the loose, rarely-touched blobs keyed by name; ``capabilities`` gates
    entity creation.

    Every slice but ``capabilities`` is always present, empty before the device
    has answered, so entities can read them straight instead of each carrying
    its own None guard. ``capabilities`` stays optional because there "unknown"
    and "has nothing" gate entity creation differently.
    """

    playback: EversoloPlayback = field(default_factory=EversoloPlayback)
    volume: EversoloVolume = field(default_factory=EversoloVolume)
    device: EversoloDevice = field(default_factory=EversoloDevice)
    processing: EversoloProcessing = field(default_factory=EversoloProcessing)
    visualization: EversoloVisualization = field(default_factory=EversoloVisualization)
    inputs: EversoloInputs = field(default_factory=EversoloInputs)
    toggles: EversoloToggles = field(default_factory=EversoloToggles)
    settings: Mapping[str, Any] = field(default_factory=dict)
    capabilities: EversoloCapabilities | None = None

    @property
    def live_input_name(self) -> str | None:
        """The label of the input ``getState`` says is selected right now.

        Not ``inputs.current``: that one is read off the settings tier and
        deliberately trails a write by up to a settings cycle, which is right
        for the source select but wrong for anything describing a live
        reading — it would still name the old input while the reading already
        describes the new one. The tag ``getState`` carries in the same payload
        is the one that moves in step.

        Falls back to the tag itself while the input list is unread, so it
        names *something* rather than going blank.
        """
        tag = self.volume.selected_input_tag
        if tag is None:
            return None
        entry = self.inputs.by_tag(tag)
        return entry.name if entry else tag

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> EversoloData:
        """Build the live slice from one ``getState``.

        The screen's visualization flags ride here rather than on the settings
        tier because ``getState`` is the only payload that carries them.
        """
        return cls(
            playback=EversoloPlayback.from_state(state),
            volume=EversoloVolume.from_state(state),
            device=EversoloDevice.from_state(state),
            processing=EversoloProcessing.from_state(state),
            visualization=EversoloVisualization.from_payload(state),
        )

    def merge(
        self,
        *,
        settings: Mapping[str, Any] | None = None,
        device: EversoloDevice | None = None,
        capabilities: EversoloCapabilities | None = None,
    ) -> EversoloData:
        """Return a copy with the slow tiers layered over this live read.

        The settings blobs are parsed here rather than in the entities that
        read them: ``inputs`` and the ``switches`` map are the typed views of
        two of them. A ``device`` supersedes the light identity ``getState``
        carries, because ``getModel`` is the only source of the ``ableRemote*``
        power flags.

        Identity and capabilities arrive together from one profile read but are
        taken **separately** here, because they do not become final together:
        identity is stable from the first successful read, while two of the
        gates are fields of ``getState`` that a payload may omit. Passing the
        profile whole would mean withholding the device to keep waiting on a
        gate, and a missing device is worse than a missing entity.
        """
        merged = dict(settings) if settings is not None else dict(self.settings)

        return replace(
            self,
            settings=merged,
            inputs=EversoloInputs.from_payload(merged.get("input_output_state")),
            toggles=EversoloToggles.from_settings(
                merged.get("system_settings"), merged.get("sub_output_option")
            ),
            capabilities=capabilities
            if capabilities is not None
            else self.capabilities,
            device=device if device is not None else self.device,
        )
