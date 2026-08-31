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
[Home Assistant](https://www.home-assistant.io/). It talks directly to your
streamer over your local network — no cloud account, no vendor app, and
nothing to log into.

It supports Eversolo's DMP-A line (DMP-A6, DMP-A8, DMP-A10 and other A-series
models). The entities you get depend on your specific model — the integration
only creates entities for features your device actually reports having.
**Only the DMP-A8 Gen 2 (firmware v1.1.50–v1.1.80) has been tested**; setup
currently only accepts devices that identify themselves as a DMP-A model, and
rejects anything else. Eversolo's PLAY and T series (T8/T10) streamers likely
work the same way under the hood, and the DAC-Z series is untested either
way, but none of them has been confirmed on real hardware yet, so setup
doesn't allow them by default. If you own one and want to try it anyway, see
[Trying this on a PLAY, T8 or T10](docs/trying-unsupported-models.md) for an
unofficial, unsupported way to bypass that check.

### Entities

| Platform      | Name                 | Description                                                                   |
|---------------|----------------------|-------------------------------------------------------------------------------|
| Media Player  | _(the device itself)_| Now playing, transport, volume/mute, source, and browsing/playing/searching the device's local music library |
| Binary Sensor | DSP active           | Diagnostic: whether DSP is engaged for the currently selected input           |
| Binary Sensor | EQ active            | Diagnostic: whether output EQ is engaged (only on units that have an EQ side) |
| Button        | Power off            | Turns off device (only on units that report they accept it)                  |
| Button        | Power on             | Wakes the device over Wake-on-LAN (only on units that report they accept it) |
| Button        | Reboot               | Reboots device (only on units that report they accept it)                     |
| Camera        | Panel view           | The front panel's screen, mirrored (960×360) without waking the display — a snapshot on request, or live at the device's own ~40 fps while a dashboard card is actually watching it |
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
| Switch        | Auto-switch source (Internal Player) | Auto-switches to the Internal Player on built-in playback/Connect, instead of leaving you to select it by hand (see note below) |
| Switch        | CD auto play         | Starts a disc as soon as it is inserted (only on units with a CD drive)       |
| Switch        | EOS engine           | Eversolo's original sampling-rate audio engine                                |
| Switch        | Gapless playback     | Plays consecutive tracks without a gap                                        |
| Switch        | Screen               | Blanks or wakes the front display (see below)                                |
| Switch        | Subwoofer output     | Enables the subwoofer channel (only on units with a subwoofer output)         |
| Switch        | Suppress screensaver during playback | Keeps the device's screensaver from firing while something plays (see below) |

The media player takes the device's own name and carries no entity name of its
own, which is how Home Assistant names the one entity that _is_ the device.

### Things worth knowing before you wire it into an automation

**Not every input supports every control.** On the TV/eARC input, for
example, the device doesn't respond to play/pause/skip, so those buttons show
as greyed out rather than doing nothing when pressed.

**CD is a source, not just a switch.** On units with a disc drive, selecting
`CD` as the source switches the device to its internal player and starts the
loaded disc playing — if the tray is empty, this fails with an error instead
of doing nothing. If you'd rather a disc start playing the moment you insert
it, use the separate CD auto play switch instead. The source reads back as
`CD` whenever a disc is loaded and playing.

**Auto-switch source fixes a real annoyance.** If you start playback from the
Eversolo app, Spotify Connect, or another built-in source, the device doesn't
always switch to show/use that input — it can keep playing whatever was
selected before, with no visible change. Turn this switch on and the device
will automatically switch to the Internal Player whenever built-in playback
or Spotify Connect starts (this doesn't apply to Bluetooth).

**The Visualization dropdown and its automation value aren't the same text.**
The dropdown shows Off, VU meter and Spectrum, but automations must use
`off`, `vu_meter` or `spectrum` — writing `state: "VU meter"` in an automation
won't match. Every other dropdown in this integration uses the same text in
both places; this is the one exception.

**The Screen switch reads the device's real state**, not just the last
command Home Assistant sent — so turning the screen on or off at the unit
itself shows up here within one poll. On the rare device UI language the
integration doesn't yet recognize the label for, it falls back to
remembering the last command instead (and shows as an assumed-state toggle
while it does).

**Suppress screensaver during playback re-touches the device's own timeout,
rather than disabling it.** The device's screensaver runs on a pure
idle-since-last-write clock that ignores playback, and there is no setting
that turns it off during playback — so while this switch is on and something
is playing, the integration periodically re-writes the device's own current
screensaver timeout, which resets that clock without changing anything you
configured. Off by default; nothing is touched unless you turn it on.

That re-write is also, incidentally, the only way this integration ever
touches the screen itself: if the device is already sitting on its
screensaver (or the screen is off) when a keep-alive cycle fires, the write
dismisses it back to the now-playing screen — confirmed live against a real
unit. It's a side effect of resetting the idle clock, not a deliberate "wake
the screen" feature, and it only happens while this switch is on and
something is playing.

**A couple of images are loaded straight from the device, unencrypted.** The
Input sensor's icon, and occasionally the media player's now-playing picture
(a small source badge, not real album art — never shown for Bluetooth), come
directly from the device over plain `http://` rather than through Home
Assistant. If you access your Home Assistant over `https://`, your browser
may block these as insecure ("mixed content").

**The style preview images show what each option looks like, not which one
is selected.** There's one image per VU meter/spectrum style so you can
browse them, but nothing highlights the current selection automatically —
building that into a dashboard is up to you. To see what's playing right
now, use the separate Selected VU preview / Selected spectrum preview
entities instead — their picture always matches the current selection.

**Power buttons and the media player's power controls do the same thing.**
On models that support remote power-on, turning the media player on sends a
Wake-on-LAN signal (the only way to wake the device remotely), and turning it
off sends the same command as the Power Off button — both buttons still work
independently too. Because there's no way to tell a genuinely unreachable
device apart from one that's simply powered down, the media player shows as
"off" rather than "unavailable" on these models, so you can still turn it
back on. Models that don't support remote power-on show as properly
unavailable when unreachable, and don't get a Power On button.

**The media player can browse, play and search the device's local music
library** (units with a play queue only). Browsing offers Albums, Artists and
Recently Played; playing a track, album or artist from there — or from HA's
`search_media` — replaces the current queue by default, or adds to it via the
usual `enqueue` options. Search matches filenames as readily as tags, so on a
library with untidy filenames the results can look scruffy (a scene-release
string, a bare filename) rather than clean metadata — that's the device's own
matching, not a bug in this integration. Folder browsing isn't offered: the
device's folder listing leaks your SMB share password in plain text.

## Requirements

- **Home Assistant 2026.4.0 or newer.** (Needed for the front-panel camera image.)
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

Home Assistant will usually find your streamer on the network automatically
and offer it under `Settings → Devices & services → Discovered` — just
confirm it to finish setup. Otherwise, click `Add integration`, search for
`Eversolo`, and enter your streamer's IP address (or hostname). That's the
only thing you're asked for: no username or password needed.

The same unit can't be added twice, since it's identified by its hardware
address. If its IP address changes later, use `Reconfigure` on the existing
entry to point it at the new address without losing your entities or
automations.

Home Assistant checks playback status (what's playing, volume, input) every
5 seconds, and other settings (brightness, styles, routing, toggles) every
30 seconds, plus immediately after you change one.

The one thing that is configurable is an off-by-default option to look up
cover art on MusicBrainz and the Cover Art Archive for Bluetooth playback — the
only source the device itself never supplies art for. It is the only network
traffic this integration ever sends off your local network, so it stays off
unless you turn it on. The manual `Add integration` form offers it up front;
either way, it can be flipped later via `Configure` on the integration entry.

## Known limitations

Deliberate omissions, not bugs. Several of these are things the device _can_
do — they are simply not in this release.

**DSP, EQ and room correction.** The integration reports whether DSP is active
for the input in use, and whether output EQ is active, and nothing more.
Choosing or editing a DSP profile, editing PEQ bands, and running DRC room
correction are not in this release.

**CD support is playback only.** Transport, disc metadata, selecting the CD
source, and CD auto play work. Tray/eject, ripping and disc details need
touch injection over the device's screen-mirroring channel, which is not
supported (see below). The metadata the API gives for a disc is title,
artist, duration and position — no album, and no track list.

**Touch injection, the file manager and the APK installer** are not
supported. (Screen mirroring itself is — see the Panel view camera above —
but it's view-only: nothing here can tap or type on the device's screen.)

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
