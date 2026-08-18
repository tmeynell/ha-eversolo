# 0005 — Setup admission is restricted to the `DMP-A` model prefix

**Status:** Accepted, under active reconsideration

## Context

This integration was built and verified against the DMP-A6 and DMP-A8 (the Eversolo "A-series").
The Eversolo control app recognizes other model lines too, and the vendor's whole product family
shares a common Zidoo-lineage API surface at a protocol level. Without hardware from another line
to verify against, admitting an unverified model risks producing a config entry that looks set up
but silently exposes broken or missing entities — `EversoloCapabilities`'s gating logic was
written and tested against A-series behaviour specifically.

## Decision

`config_flow.py` sets `SUPPORTED_MODEL_PREFIX = "DMP-A"` and refuses setup (`unsupported_model`)
for anything else, deliberately — not because other lines are known to be incompatible, but
because compatibility has never been checked and a broken entry is worse than a refused one.

## Consequences

- Owners of non-A-series Eversolo hardware cannot use this integration at all, even where the
  underlying API might well support it.
- **This decision is the explicit subject of open research** — a ticket ("enumerate Eversolo
  model lines and assess admission safety") is investigating which other model lines could be
  safely admitted, which need a real device to verify first, and which are a fundamentally
  different capability shape.
  `tmeynell/ha-eversolo#11` is the widened-admission implementation ticket blocked on that
  research's findings — see its "Blocked by" section, and this repo's
  `docs/agents/issue-tracker.md` for why the research itself isn't tracked here.
- If that research concludes nothing beyond `DMP-A` is safely admittable without hardware, this
  ADR's status should be updated to reflect that as a confirmed (not just default) decision,
  rather than left looking unexamined.
