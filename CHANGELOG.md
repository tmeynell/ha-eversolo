# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and version numbers follow the
SemVer contract in `CONTRIBUTING.md`.

## [Unreleased]

### Fixed

- Selecting `CD` with a malformed (but non-empty) disc-list response now raises the same clean
  "No disc is loaded" error instead of an unhandled exception (#59).

## [1.2.0] - 2026-08-25

### Added

- SSDP auto-discovery — Home Assistant can find the streamer on its own (#19, #23).
- Auto-switch source entity that switches to the Internal Player when built-in playback or
  Connect starts (#34, #44).
- Image entities: a live panel screenshot (#37, #50), a preview per VU/spectrum style option
  (#17, #21), and current-selection previews that track the active VU/spectrum style (#32, #51).
- MusicBrainz cover-art toggle for Bluetooth playback, now offered during initial device setup
  as well as via `Configure` (#18, #22, #45).
- `docs/trying-unsupported-models.md`, an unofficial path for PLAY/T8/T10 owners to try the
  integration and report back (#33).

### Changed

- Selecting the `CD` source now plays the loaded disc directly instead of only switching input
  (#36, #57).

### Fixed

- Source no longer misreports `CD` for internal-player playback that isn't actually a disc
  (#35, #52).
- Cover Art Archive lookups now request the `-500` thumbnail instead of the bare front redirect
  (#30).
- Added the config-flow label the MusicBrainz toggle was missing on the add-device form (#62).

## [1.1.0] - 2026-08-18

First public release.

### Added

- Media player: now playing, transport, volume/mute and source.
- Diagnostic sensors: DSP active, EQ active, audio format.
- Controls: power off/on/reboot, knob and screen brightness, DAC filter, output routing,
  upsampling, visualization, VU/spectrum style, knob color.
- Switches: CD auto play, EOS engine, gapless playback, screen, subwoofer output.

[Unreleased]: https://github.com/tmeynell/ha-eversolo/compare/1.2.0...HEAD
[1.2.0]: https://github.com/tmeynell/ha-eversolo/compare/1.1.0...1.2.0
[1.1.0]: https://github.com/tmeynell/ha-eversolo/releases/tag/1.1.0
