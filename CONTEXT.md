# Domain glossary — `ha-eversolo`

Terms and internal concepts that aren't self-explanatory from a variable name alone. If you're
about to touch `coordinator.py`, `data.py`, `media_player.py` or `config_flow.py`, skim the
relevant entry first — several of these encode a decision that looked wrong before it was
understood. See `docs/adr/` for the full reasoning behind the starred (⭐) entries.

## `playType` ⭐

The device's own declaration of which block of `getState` is currently *audible*. Read at
`data.py`'s `EversoloMusicInfo.from_state` (also mirrored inside `everSoloPlayInfo`, confirmed
to agree in every capture taken so far). Values: `4` = Bluetooth, `5` = the local player
(including a spinning disc), anything else = a network source (Spotify Connect and similar).

This is the thing that decides what `media_player` and `sensor.audio_format` show — **not**
`music.extension == "cd"`, which only ever meant "a disc is loaded in the tray," regardless of
whether it's what's actually playing. See ADR-0002.

## Two-speed coordinator ⭐

`EversoloDataUpdateCoordinator` polls at two cadences: the **live tier** (`getState`, every
`LIVE_UPDATE_INTERVAL` = 5s — playback, volume, mute, input tag, display flags) and the
**settings tier** (the rarely-changing list/brightness endpoints, every `SETTINGS_REFRESH_CYCLES`
= 6 live cycles, or immediately after any write). Losing the live tier means the device is gone
(`UpdateFailed`, every entity unavailable); losing the settings tier just keeps the last known
value. See ADR-0001.

## Device latch / capabilities latch ⭐

Two separate pieces of state the coordinator settles independently, both from one profile read.
**Identity** latches the moment it lands and is never re-evaluated. **Capabilities** has two
gates — DSP and EQ — that are fields of `getState` rather than answers from a dedicated endpoint,
so a payload can omit them without the read itself failing. Those two gates are waited for across
cycles, bounded at `PROCESSING_GATE_CYCLES` = 6 cycles (~30s at the live-tier cadence), rather
than latched off on first silence — see `async_add_capability_gated` for how entities are added
as their gate resolves instead of the whole set being withheld. See ADR-0001.

## Whole-object cached save (DSP) ⭐

Every write in this integration except DSP is a single scalar appended to one URL, applied
optimistically. DSP is the one namespace where the vendor app instead builds up an entire
in-memory config object and saves it as one serialized blob (`saveDSPConfig?dspConfig=%s`) — which
means a concurrent edit from the phone app can silently overwrite an in-flight integration write,
and vice versa. No DSP writes exist in the integration yet; this is why any future one needs a
different pattern from the rest of the codebase. See ADR-0003.

## `hasEQSetting` vs `hasEqSetting` ⭐

A real, confirmed inconsistency in the device's own API: `getModel` reports `hasEqSetting: true`
while `getState` reports `hasEQSetting: false` for the same unit — different casing, opposite
values. The integration reads `getState`'s field (`data.py`, `EversoloCapabilities.from_state`),
which is why the EQ binary sensor is correctly gated off for hardware that `getModel` claims has
EQ. This is not a bug to fix; the `getState` field is the trusted one because it's the one that
gates entity creation directly, and reading `getModel` instead would create an EQ entity for
hardware that (per `getState`) reports none. See ADR-0001.

## The device's ports (Zidoo lineage)

The Eversolo control app talks to several ports on the unit; only one is currently in scope.

- **9529** — the main API this integration uses. Plain, unauthenticated HTTP JSON. The screen-mirror
  cast-mode session (`cast_session.py`, #38) also lives here: a `setcastmode` handshake on this
  port hands back a *separate*, per-session TCP port for the actual video socket — there is no
  fixed video port to enumerate. An earlier version of this note placed screen-mirror on 9599;
  that was based on the app's port-9599 WebSocket path, which turned out unnecessary once the
  9529 mechanism was found and proven (RESEARCH.md, "Screen-mirror decode pipeline").
- **9599** — a WebSocket path the vendor app can also use for screen-mirror/touch-injection, not
  the one this integration speaks. Not used; a live probe risks sending real touch input to the
  device, so it hasn't been enumerated beyond identifying what it's for.
- **9587**, **18888** — file-manager and app-installer surfaces. Out of scope; not investigated.

## Setup admission (`SUPPORTED_MODEL_PREFIX`) ⭐

`config_flow.py` only admits devices whose `getModel` starts with `DMP-A`. This is deliberate,
not an oversight — see ADR-0005 for why, and for the open question of whether other Eversolo
model lines should be admitted too (tracked as a research effort outside this repo; see
`docs/agents/issue-tracker.md`).

---

## ADRs

Read `docs/adr/` for anything that touches an area you're about to work in:

- [0001 — Two-speed coordinator with a separate device/capabilities latch](docs/adr/0001-two-speed-coordinator-and-capability-latches.md)
- [0002 — `playType` decides the audible source, not `extension == "cd"`](docs/adr/0002-playtype-decides-the-audible-source.md)
- [0003 — DSP writes are whole-object-cached; everywhere else is per-field optimistic](docs/adr/0003-dsp-whole-object-cached-writes.md)
- [0004 — Write verification checks HTTP status only, never the JSON `status` field](docs/adr/0004-http-status-only-write-verification.md)
- [0005 — Setup admission is restricted to the `DMP-A` model prefix](docs/adr/0005-dmp-a-only-setup-admission.md)
