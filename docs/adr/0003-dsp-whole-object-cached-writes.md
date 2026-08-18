# 0003 — DSP writes are whole-object-cached; everywhere else is per-field optimistic

**Status:** Accepted

## Context

Every write this integration makes today is a single scalar value appended to one URL, applied
optimistically (update local state immediately, confirm on the next poll). That pattern was
verified safe across the whole `/SystemSettings/` surface and the transport/display endpoints:
each is `GET <url>&value=<scalar>`, independent of every other setting.

DSP is different. Protocol research found exactly one endpoint in the entire API that takes a
serialized object rather than a scalar: `saveDSPConfig?dspConfig=%s&isRight=%s`. The vendor's
companion app builds up an in-memory representation of the whole DSP config and saves it as one
blob on that single endpoint — meaning a concurrent edit from the phone app (or a second
integration write) can overwrite the other's changes entirely, not just the one field either side
touched.

This was confirmed to be DSP-specific, not how every screen in the app works generally: no other
namespace has anything resembling a cached, save-triggered object write.

## Decision

The per-field optimistic write pattern used everywhere else in this integration does **not**
apply to DSP. No DSP writes exist in the integration as of this ADR (`binary_sensor.dsp_active`/
`eq_active` are read-only). Any future DSP write path must account for whole-object contention —
at minimum, read the current full config immediately before writing, and treat a concurrent
phone-app edit as an expected failure mode, not an edge case.

## Consequences

- A future DSP/EQ write feature (e.g. a simplified graphic-EQ-over-PEQ UI) cannot reuse the
  existing `async_write_setting(setter_url, value)` helper as-is — it needs its own read-modify-
  write path.
- Live DSP research calls (mining the remaining DSP/DRC surface) must back up the current config
  and use a scratch profile before writing, and must force-quit the phone app first, since an open
  app will overwrite device state on its own next save regardless of what the integration just
  wrote.
- The three DSP EQ blocks (`peqFirList`, `audioTunningEQList`, `eqGeqGainBeans`) are separate and
  summed, and the app's UI labels two of them "PEQ" — any DSP write work must say which block it
  targets explicitly; conflating them has already caused incorrect writes during research.
