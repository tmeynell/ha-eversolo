# 0001 — Two-speed coordinator with a separate device/capabilities latch

**Status:** Accepted

## Context

`getState` is a single endpoint that carries both fast-changing playback state and much slower
things like identity and capability flags. Polling everything on one cadence means either
hammering the device for state that almost never changes, or throttling playback updates to match
the slow stuff — neither is right.

Separately, device identity and capability flags both come from one profile read, but they don't
behave the same way under a slow or incomplete first response. Identity (model, MAC) is either
present or the device isn't there. Two capability flags — DSP and EQ active — are ordinary fields
of `getState` rather than answers from a dedicated endpoint, so a slow-to-answer unit's *first*
read can omit them without the read itself failing. Latching a capability off on that first
silence would leave the entity it gates permanently and silently missing on every restart of a
slow unit — a defect with no error message and no obvious cause.

## Decision

`EversoloDataUpdateCoordinator` runs two tiers: a **live tier** on `getState` every
`LIVE_UPDATE_INTERVAL` (5s) for playback/volume/mute/input/display state, and a **settings tier**
every `SETTINGS_REFRESH_CYCLES` (6 live cycles) for the rarely-changing list/brightness endpoints,
also refreshed immediately after any write. Failure is treated differently per tier: a failed live
read raises `UpdateFailed` (the device is gone, every entity goes unavailable); a failed settings
read just keeps the last known value (a nuisance, not an outage).

Underneath both tiers, identity and capabilities are two separate latches. Identity latches once,
permanently, on first successful read. Capabilities are **published immediately** with whatever
gate state is currently known, and the two uncertain gates (DSP, EQ) are re-checked across cycles,
bounded at `PROCESSING_GATE_CYCLES` (6 cycles, ~30s at the live cadence) — entities are added as
each gate resolves (`async_add_capability_gated`), not held back as a set until both resolve.

## Consequences

- A slow-answering unit's DSP/EQ entities appear a few seconds late instead of never appearing —
  the failure mode changed from silent-and-permanent to visible-and-bounded.
- The 30-second ceiling is a real wait during coordinator setup for a genuinely slow unit; this is
  intentional (bounded, not indefinite) rather than a performance bug.
- Anyone adding a new capability gate that's a `getState` field (not a dedicated endpoint) needs to
  route it through the same latch mechanism, not assume `getState` will always carry it on the
  first read.
