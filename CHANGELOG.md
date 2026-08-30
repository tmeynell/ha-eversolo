# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and version numbers follow the
SemVer contract in `CONTRIBUTING.md`.

## [Unreleased]

### Added

- The manual add form now checks for Eversolo devices already found on your network and offers
  any as a pick alongside the host field, so setting one up no longer requires knowing its IP
  address up front. A network with none found (or one not yet caught by Home Assistant's own
  discovery) falls back to today's bare host-entry form (#27).
- Camera platform: a "Panel view" snapshot entity, opening the device's screen-mirror socket for
  one frame per fetch instead of polling a still-image endpoint (#38).
- A "Suppress screensaver during playback" switch. The device's screensaver runs on a pure
  idle-since-last-write clock, blind to playback, with no app-level flag to turn it off during
  playback — so while the switch is on and something is playing, the integration periodically
  re-writes the device's own current screensaver timeout, which resets that clock without
  changing anything you configured. Off by default (#41).

### Removed

- **Breaking:** the `panel_screenshot` image entity, replaced by the new Camera "Panel view"
  entity above. Its `getScreenShot` transport woke the physical unit's display and popped up an
  on-screen dialog on every poll; the screen-mirror socket the replacement uses is passive and
  never does either. Anyone with `image.<name>_panel_screenshot` on a dashboard or in an
  automation needs to switch to the new `camera.<name>_panel_view` entity — the old `unique_id` is
  gone from the entity registry, not just renamed (#38).

### Changed

- SSDP discovery of a new device now shows a confirmation form instead of auto-creating the
  entry, with the same MusicBrainz cover-art toggle the manual add flow offers; rediscovery of an
  already-configured device still heals its host silently (#63).
- Both the discovery card and the entry title now use the device's own name (set in the Eversolo
  app), falling back to the model, instead of always showing the bare model name (#63, #73).
- Renaming the streamer in the Eversolo app now updates its HA device name on the next poll,
  instead of staying stuck on whatever name it had when the entry was added (#73).

### Fixed

- Selecting `CD` with a malformed (but non-empty) disc-list response now raises the same clean
  "No disc is loaded" error instead of an unhandled exception (#59).
- The current-selection VU/spectrum preview images now update their served picture when the
  selection changes, instead of staying stuck on whichever picture was fetched first (#58).

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
