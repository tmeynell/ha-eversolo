# 0002 — `playType` decides the audible source, not `extension == "cd"`

**Status:** Accepted

## Context

The integration originally inferred what was audible from `music.extension == "cd"`: if a disc
was in the tray, the media player showed the disc. This looked right until Tim reported it live:
with Spotify Connect actually playing and a disc sitting in the tray, HA still showed the disc.
`extension == "cd"` only ever meant "a disc is loaded" — never "a disc is what's making sound" —
and nothing had distinguished those two questions until this defect surfaced.

The device's own `getState` payload carries `playType`, which mirrors the same source-selection
logic the vendor's own companion app uses: `4` is Bluetooth, `5` is the local player (including a
spinning disc), anything else is a network source such as Spotify Connect. It also rides inside
`everSoloPlayInfo`, agreeing with the top-level value in every capture taken.

## Decision

`EversoloMusicInfo.from_state` (`data.py`) and the media player's now-playing surface
(`media_player.py`) dispatch on `playType`, not on `extension`. The `extension == "cd"` check is
retained only as an *additional* guard for a narrower purpose — blanking stale disc metadata that
a live-input change hasn't yet overwritten — not as the primary source-of-truth signal.

## Consequences

- `source` and now-playing attribution are both derived from the same `playType` dispatch, so they
  can't disagree with each other the way `extension`-based logic could.
- Any future now-playing or source-attribution logic must key off `playType`, never re-introduce
  `extension` as a primary signal — that regression is exactly what this ADR exists to prevent.
- Bluetooth's `playType == 4` branch is defensive rather than corrective (BT was already handled
  correctly via a separate input-tag guard) — see `media_player.py`'s inline comment for why both
  exist rather than one being redundant with the other.
