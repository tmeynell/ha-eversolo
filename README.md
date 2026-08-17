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

It works with any Eversolo device the official Eversolo Control app supports.
Every entity is gated on what the device itself reports it has, so entity sets
vary by model; **only the DMP-A8 Gen 2 (firmware v1.1.50) has been tested.** A
device that does not identify itself as an Eversolo DMP model is refused
during setup.

### Entities

| Platform      | Name                 | Description                                                                   |
|---------------|----------------------|-------------------------------------------------------------------------------|
| Media Player  | _(the device itself)_| Now playing, transport, volume/mute and source                                |
| Binary Sensor | DSP active           | Diagnostic: whether DSP is engaged **for the input in use** (see below)       |
| Binary Sensor | EQ active            | Diagnostic: whether output EQ is engaged (only on units that have an EQ side) |
| Button        | Power off            | Turns off device (only on units that report they accept it)                   |
| Button        | Power on             | Wakes the device over Wake-on-LAN (only on units that report they accept it)  |
| Button        | Reboot               | Reboots device (only on units that report they accept it)                     |
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
| Switch        | CD auto play         | Starts a disc as soon as it is inserted (only on units with a CD drive)       |
| Switch        | EOS engine           | Eversolo's original sampling-rate audio engine                                |
| Switch        | Gapless playback     | Plays consecutive tracks without a gap                                        |
| Switch        | Screen               | Blanks or wakes the front display (write-only — see below)                    |
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

**DSP active is per-input, not a global "DSP is on".** The device keeps a
separate profile assignment and enable flag for each of its four inputs, and
reports the one belonging to whichever input is selected — so changing source
can flip this sensor with nothing else having changed. The sensor's `input`
attribute names the input its current reading is about. **The attribute is
absent, not a raw device code, for the first few polls after startup** while
that name is still resolving — a template can treat a missing `input` as "not
known yet" and never has to recognize a raw code standing in for the label.
**EQ active** is the
same reading for the parallel EQ feature, which applies to the digital
_outputs_ instead; units without an EQ side (including the DMP-A8 Gen 2) get no
EQ entity at all. Both are read-only.

**The Screen switch is write-only.** No field the device reports says whether
the front display is lit — not `getState`, not the settings tree — and the only
call available _toggles_ it. So the switch is marked as assuming its state: it
shows what it last asked for (remembered across restarts), and does not send a
second request for a state it already shows, because the device would read that
as "change" and do the opposite. Blank the screen at the unit itself and the
switch cannot notice; its next press will be one step out of phase. Screen
brightness and Visualization have no such caveat — the device reports both, so
they follow changes made on the unit.

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

Polling is fixed and there is no options flow: live state (playback, volume,
input) is read every 5 seconds, and the settings tier (brightness, styles,
routing, the toggles) every 30 seconds and again immediately after any write.

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
