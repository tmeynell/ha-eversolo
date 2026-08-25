# Contributing

Thanks for looking at this. It's a hobby integration for one device family, so
this document is short on purpose.

## Working on it

The dev container (`.devcontainer.json`) gets you a working environment with
one open; otherwise:

```
scripts/setup    # install requirements.txt + requirements-test.txt
scripts/develop  # run a scratch Home Assistant with this integration loaded
scripts/lint     # ruff check --fix, then ruff format
```

**The test suite is the contract.** Every test runs against real captured
device payloads in `tests/fixtures/` (see `tests/fixtures/README.md` for
where they came from and what each one is) — nothing here talks to a live
device. If your change alters behaviour and no test failed before you made
it, the change isn't finished yet.

**Every change goes through a pull request.** `main` is protected with no
bypass, including for the maintainer — there's no "just this once, push
straight to main," not even to fix the fix.

## Versioning

**The git tag is the source of truth for the version.** `release.yml` writes
the tag into `custom_components/eversolo/manifest.json` inside the release
zip, verbatim.

**Tags are bare — `1.0.0`, never `v1.0.0`.** The write is verbatim: a
`v1.0.0` tag ships `"version": "v1.0.0"` to every HACS install, and HACS's
version comparison doesn't expect the prefix.

**`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).** Land
entries under `## [Unreleased]` as part of the PR that makes the change, grouped under `Added`,
`Changed`, `Fixed`, etc. — not saved up to be reconstructed from `git log` at release time.
`tests/test_packaging.py` fails a version-bump PR that doesn't also turn `[Unreleased]` into a
dated `## [X.Y.Z]` section, so the changelog can't drift from what actually shipped.

**Release procedure:**

1. Open a PR that bumps the version in `custom_components/eversolo/manifest.json` and, in
   `CHANGELOG.md`, renames `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` (today's date) and adds
   a fresh empty `## [Unreleased]` above it. Add the two compare-link references at the bottom
   (`[Unreleased]` against the new tag, `[X.Y.Z]` against the previous one).
2. Merge it through the normal gate.
3. Tag the merge commit with the bare version number.
4. Publish a GitHub release from that tag, pasting the new `CHANGELOG.md` section as the release
   body verbatim — this also fires `release.yml`, which writes the tag into the zipped manifest
   and attaches `eversolo.zip` to the release.

**What each part of the version bump means:**

- **major** — an entity or `unique_id` was removed or renamed (this breaks
  existing dashboards and automations, and orphans entity-registry entries),
  or a config-entry needs a migration.
- **minor** — new entities, new capabilities, or a raised `homeassistant`
  floor in `hacs.json`. The floor isn't a breaking change by itself — HACS
  simply withholds the update from anyone running an older core rather than
  shipping them something that won't work. `tests/test_packaging.py` checks
  the floor in `hacs.json` against the one stated in the README, so raising
  it means saying in the README which newer Home Assistant API forced it.
- **patch** — everything else: fixes that don't change any entity's
  identity.

## One CI thing not to do

**Never add a `paths:` filter to `lint.yml`, `test.yml` or `validate.yml`.**
Those checks are required on `main`'s ruleset. A required check whose
workflow never triggers leaves a PR waiting forever on a status that will
never report — there's no timeout, no bypass, nothing to click. If a filter
is ever genuinely wanted, the ruleset's required-check list has to be
updated in the same PR, not after.
