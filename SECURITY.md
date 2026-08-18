# Security Policy

## Supported versions

Only the latest release is supported. There is no backport story for older
versions.

## Reporting a vulnerability

Please report security issues privately using
[GitHub's private vulnerability reporting](https://github.com/tmeynell/ha-eversolo/security/advisories/new)
rather than a public issue. You should get a response within a few days.

## What is (and isn't) a vulnerability here

**The Eversolo device's own control API (port 9529) is unauthenticated by
design.** Anyone already on the same LAN as the device can control it
directly, with or without this integration — plain HTTP, no login. That is a
property of the device, not something this integration can fix, and reports
along those lines will be closed as expected behavior rather than treated as
a finding.

What **would** be a real finding:

- Something that lets a party **not** already on the device's LAN reach or
  control it through this integration.
- Anything that leaks Home Assistant credentials or config-entry data.

**The integration itself never handles a credential.** Its config flow asks
for a host address and nothing else — there is no secret stored in the
config entry to leak.

This is a local-polling integration speaking plain HTTP to a device on a
home network. That's the honest scope of what it can and can't protect
against.
