# 0004 — Write verification checks HTTP status only, never the JSON `status` field

**Status:** Accepted

## Context

Several of the device's endpoints return a JSON body containing its own `status` field alongside
the HTTP status code, and the two disagree with each other in both directions: `setEffect`
returns HTTP `200` while doing nothing to device state, and `setDSPSource` returns HTTP `803`
(elsewhere used as an "unknown method" signal) while actually succeeding. Neither the HTTP layer
nor the JSON body reliably says whether a write landed, in either direction of error.

The vendor's own control app was confirmed to behave the same way: it doesn't gate its own UI
feedback on the JSON `status` field either.

## Decision

`EversoloApiClient._api_wrapper` (`api.py`) calls `response.raise_for_status()` and nothing else
to detect a failed request — it never inspects a JSON `status` field for success/failure. This is
deliberate, matching the vendor app's own behaviour, not an oversight to "fix" by adding JSON
status checking.

Because neither signal proves a write landed, **the only thing that actually proves a write
succeeded is a read-back** — the coordinator's `async_refresh_settings()` after any write exists
specifically for this, not just to pick up side effects.

## Consequences

- Any new write path must trigger a refresh afterward and treat the *refreshed state*, not the
  write response, as the source of truth for whether the write worked.
- A future contributor "fixing" the client to also check the JSON `status` field would introduce
  false negatives (rejecting genuinely successful `803` responses) and false positives (accepting
  `200` responses that did nothing) — this ADR exists specifically to head that off.
- Research work probing new endpoints must not trust either status source either; the project's
  standing rule ("status codes lie in both directions — only a read-back proves a write landed")
  applies to protocol-research and device-probing tickets just as much as to the client code.
