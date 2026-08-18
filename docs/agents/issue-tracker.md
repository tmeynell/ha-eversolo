# Issue tracker: GitHub, plus private research tracking

**Code-change work for this repo lives as GitHub issues.** Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## Two kinds of ticket

Not every unit of work behind this integration is a GitHub issue on this repo:

- **Code-change tickets** — new behaviour, a bug fix, anything landing in `custom_components/` or
  its tests — are always GitHub issues here.
- **Research tickets** — device-protocol investigation, a written recommendation that a later
  code-change ticket acts on — are tracked in the maintainer's own private notes, **outside this
  repository**. This is deliberate: that material is notes about the device and the vendor's
  companion app, not documentation of the integration itself, and isn't meant for public
  distribution.
- When a code-change issue depends on research that hasn't been published here, its
  **"Blocked by"** section says so in prose rather than linking a private location — see `#11`
  for a live example.

**If you're an external contributor**, this split is purely an internal maintainer workflow.
File a normal GitHub issue for anything you want to propose, ask, or report — you never need to
interact with the private side of it.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature
requests; `/triage` reads this flag.)_

## When a skill says "publish to the issue tracker"

Create a GitHub issue on this repo. Research findings don't belong here — they go to the
maintainer's private notes instead.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `/wayfinder` for planning this integration's roadmap. **This repo does not host
wayfinder maps** — that planning happens in the maintainer's private notes, for the same reason
research stays private (see above). This repo's only participation is as the **destination for
individual code-change tickets a private map graduates**: those get filed here as ordinary
GitHub issues, cross-referenced back to the private planning ticket in prose, the same way `#11`
references its blocking research.
