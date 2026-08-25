# Eversolo

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

[![hacs][hacsbadge]][hacs]
![Project Maintenance][maintenance-shield]

[![Community Forum][forum-shield]][forum]

_Home Assistant integration for [Eversolo](https://www.eversolo.com/) streamers._

## Description

This custom component integrates Eversolo streamers into
[Home Assistant](https://www.home-assistant.io/). It talks to the device's own
control API on port 9529 over your LAN — no cloud account, no vendor app, and
nothing to authenticate.

It supports Eversolo's DMP-A line (DMP-A6, DMP-A8, DMP-A10 and other A-series models).
Every entity is gated on what the device itself reports it has, so entity sets
vary by model; **only the DMP-A8 Gen 2 (firmware v1.1.50–v1.1.80) has been tested.**
Eversolo's PLAY and T series (T8/T10) streamers likely speak the same on-device
API and would probably work too, but setup currently only admits a device that
identifies itself as a DMP-A model — a device that doesn't is refused. The
DAC-Z series is unverified either way. Widening admission to PLAY/T series is
tracked but not yet done, since there's no hardware available to confirm they
share the DMP-A entity shape.

### Entities

| Platform      | Name                 | Description                                                                   |
|---------------|----------------------|-------------------------------------------------------------------------------|
| Media Player  | _(the device itself)_| Now playing, transport, volume/mute and source                                |
| Binary Sensor | DSP active           | Diagnostic: whether DSP is engaged for the currently selected input           |
| Binary Sensor | EQ active            | Diagnostic: whether output EQ is engaged (only on units that have an EQ side) |
| Button        | Power off            | Turns off device (only on units that report they accept it)                  |
| Button        | Power on             | Wakes the device over Wake-on-LAN (only on units that report they accept it) |
| Button        | Reboot               | Reboots device (only on units that report they accept it)                     |
| Image         | Panel screenshot     | Live capture of the front panel (native 1600x600), refreshed every 60 s      |
| Image         | {option} preview     | One per VU/spectrum style option, e.g. "VU meter 3 preview" (only on units with that style list) |
| Image         | Selected spectrum preview | The currently selected spectrum style's picture, updating as the selection changes (only on units with that style list) |
| Image         | Selected VU preview  | The currently selected VU style's picture, updating as the selection changes (only on units with that style list) |
| Number        | Knob brightness      | Knob brightness, 0–100% (only on units with a knob)                           |
| Number        | Screen brightness    | Front display brightness, 0–100%                                              |
| Select        | DAC filter           | Reconstruction filter for the analog outputs (only on units with that panel)  |
| Select        | Knob color           | Selects knob light color (only on supported devices)                          |
| Select        | Master clock         | Internal or external clock (only on units with a clock input)                 |
| Select        | Output routing       | Routes audio to a socket the unit reports as connected                        |
| Select        | Spectrum style       | Selects between the spectrum styles the device lists                          |
| Select        | Upsampling           | Upsampling rate for the analog outputs (only on units with that panel)        |
| Select        | Visualization        | What the front display shows (see the note below on its values)               |
| Select        | VU style             | Selects between the VU meter styles the device lists                          |
| Sensor        | Audio format         | Diagnostic: current stream quality, e.g. `PCM 44.1kHz/16bit`                  |
| Sensor        | Input                | The live input's name, with the device's own icon as its picture              |
| Switch        | Auto-switch source (Internal Player) | Switches input to the Internal Player when built-in playback or Connect starts (not Bluetooth In) |
| Switch        | CD auto play         | Starts a disc as soon as it is inserted (only on units with a CD drive)       |
| Switch        | EOS engine           | Eversolo's original sampling-rate audio engine                                |
| Switch        | Gapless playback     | Plays consecutive tracks without a gap                                        |
| Switch        | Screen               | Blanks or wakes the front display (see below)                                |
| Switch        | Subwoofer output     | Enables the subwoofer channel (only on units with a subwoofer output)         |

The media player takes the device's own name and carries no entity name of its
own, which is how Home Assistant names the one entity that _is_ the device.

### Things worth knowing before you wire it into an automation

**The media player advertises only the controls the current input responds
to** — on the TV/eARC input the device drives nothing, so transport is greyed
out rather than failing when pressed.

**Its source list carries the hardware inputs plus a synthetic `CD` source** on
units with a disc drive. Picking `CD` switches the device to its internal
player, which is the only input where transport actually controls the disc — it
does not start playback; that is what the CD auto play switch is for. The
source reads back as `CD` while a disc is loaded and the internal player is
live.

**The Visualization select's state is a slug, not the label you see.** Its
three values are `off`, `vu_meter` and `spectrum`, displayed as Off, VU meter
and Spectrum. An automation has to use the values — `state: "VU meter"` will
never match. This is the only select where the two differ: every other one
offers option labels the device itself supplies, and those are used as-is.

**The Screen switch assumes its state rather than reading it back.** No field
the device reports says whether the front display is lit, so the switch just
remembers what it last asked for, across restarts. Blank the screen at the unit
itself and the switch won't notice — its next press will be one step out of
phase. Screen brightness and Visualization aren't affected; the device reports
both directly.

The device does report the screen's state, but only as the *label* on its power
menu — "Screen off" while lit, "Screen on" while blanked — rendered in the
device's own UI locale. Reading it would mean matching translated text, so the
switch doesn't, yet.

**Two entities carry a device-supplied icon as `entity_picture`, fetched
straight from the device over plain `http://`** — the Input sensor (always,
once the device has reported one) and, only as a last resort when there is no
real cover art to show, the media player's now-playing picture (a small
source badge, not album art; never used on Bluetooth, which has no badge of
its own). Home Assistant's browser handling of mixed content applies if your
own UI is served over `https://`.

**The `{option} preview` Image entities show what each VU/spectrum option
looks like, not which one is selected.** Home Assistant has no widget that
binds several thumbnails to one select, so pairing the gallery with the
select on a dashboard (e.g. a `picture-elements` card tapping
`select.select_option`) is left to you. The separate `Selected VU preview`/
`Selected spectrum preview` entities cover the other half — their picture always
tracks whichever option is currently selected, so a plain `picture` card
showing one of them is enough to see what the device is doing right now.

**Power is symmetric on units that report `ableRemoteBoot`.** The media
player's `turn_on`/`turn_off` and the Power On/Off buttons drive the same two
actions: `turn_on` broadcasts a Wake-on-LAN magic packet (the device's own,
and only, wake mechanism — there is no power-on command over the API), and
`turn_off` sends the same command the Power Off button does. Both buttons stay
even though the media player now covers the same ground, so nothing loses its
`unique_id`. On a boot-capable unit, the media player reads `off` rather than
unavailable while the device is unreachable — an unavailable entity cannot be
sent `turn_on`, which would make it useless for exactly the state it exists
for — so a genuine network fault and a powered-down unit are indistinguishable
from the entity's state alone. A unit that does not report `ableRemoteBoot`
keeps honest unavailability and gets no Power On button.

## Requirements

- **Home Assistant 2024.11.0 or newer.**
- The streamer reachable on your LAN at a stable address. Give it a DHCP
  reservation or a static IP; if it does move, `Reconfigure` follows it without
  losing your entities.

## Installation

### Install with HACS

Add this repository to [HACS](https://hacs.xyz/) as a custom repository and
install it from there, then restart Home Assistant. HACS tracks releases, so
updates show up on their own.

### Install manually

Copy the `eversolo` folder from `custom_components/` in this repository into
your Home Assistant `custom_components/` folder, then restart Home Assistant.

## Configuration

Use the `Add integration` dialog, search for `Eversolo`, and enter the host IP
(or hostname) of your streamer. That is all you are asked for: the device is
contacted on its fixed port 9529 and needs no username or password.

The integration identifies your device by its hardware MAC address, so the same
unit cannot be added twice. If its IP changes, use `Reconfigure` on the existing
entry to point it at the new address and keep your entities and automations;
reconfigure refuses an address that turns out to be a different device.

Polling is fixed: live state (playback, volume, input) is read every 5
seconds, and the settings tier (brightness, styles, routing, the toggles)
every 30 seconds and again immediately after any write.

The one thing that is configurable, via `Configure` on the integration entry,
is an off-by-default option to look up cover art on MusicBrainz and the Cover
Art Archive for Bluetooth playback — the only source the device itself never
supplies art for. It is the only network traffic this integration ever sends
off your local network, so it stays off unless you turn it on.

## Known limitations

Deliberate omissions, not bugs. Several of these are things the device _can_
do — they are simply not in this release.

**DSP, EQ and room correction.** The integration reports whether DSP is active
for the input in use, and whether output EQ is active, and nothing more.
Choosing or editing a DSP profile, editing PEQ bands, and running DRC room
correction are not in this release.

**No auto-discovery.** Setup is by IP address only.

**CD support is playback only.** Transport, disc metadata and CD auto play work.
Tray/eject, ripping and disc details need the device's screen-mirroring channel
and are not supported. The metadata the API gives for a disc is title, artist,
duration and position — no album, and no track list.

**Screen mirroring, touch injection, the file manager and the APK installer**
are not supported.

**Settings not exposed.** Full subwoofer bass management (level, crossover,
delay, bypass); the extra analog-output parameters (volume step, polarity,
passthrough, fade, limits); the secondary display selects (screensaver, theme
mode, spectrum background, touch button); the extra playback toggles
(ReplayGain, cached streaming, previous-track reset, auto source switching,
folder/playlist skip, auto-open screens); and the other output modes
(IIS/SPDIF/USB-DAC/ARC).

## Licence

MIT — see [LICENSE](LICENSE).

[commits-shield]: https://img.shields.io/github/commit-activity/y/tmeynell/ha-eversolo.svg?style=for-the-badge
[commits]: https://github.com/tmeynell/ha-eversolo/commits/main
[hacs]: https://github.com/hacs/integration
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license-shield]: https://img.shields.io/github/license/tmeynell/ha-eversolo.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-Tim%20%40tmeynell-blue.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/tmeynell/ha-eversolo.svg?style=for-the-badge
[releases]: https://github.com/tmeynell/ha-eversolo/releases
