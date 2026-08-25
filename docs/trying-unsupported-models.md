# Trying this on a PLAY, T8 or T10

This integration only admits a device whose `getModel` response starts with `DMP-A` — see
`_is_supported()` in `custom_components/eversolo/config_flow.py`. Eversolo's PLAY and T-series
(T8/T10) streamers are believed to share the same on-device control API (they're the same
Zidoo-lineage hardware), but nobody has confirmed it: there's no PLAY/T unit available to test
against, so the entity set this integration builds — which is gated on what each device reports
it has — has never been checked against one.

If you own one of these and want to try it anyway, this is how, with the caveats stated plainly:
**this is unverified, unsupported, may produce broken or missing entities, and nothing here is a
commitment that it will ever become official.**

## 1. Install manually, not through HACS

HACS tracks tagged releases, all of which enforce the `DMP-A` check. To try an unsupported model
you need a local edit, so skip HACS for this:

1. Clone this repository (or download the source as a zip).
2. Apply the patch below.
3. Copy the resulting `custom_components/eversolo/` folder into your Home Assistant
   `custom_components/` folder yourself, replacing any existing copy.
4. Restart Home Assistant.

## 2. Patch the admission check

In `custom_components/eversolo/config_flow.py`, `_is_supported()` currently requires both a
`net_mac` and a `DMP-A`-prefixed model string:

```python
def _is_supported(device: EversoloDevice) -> bool:
    return bool(device.net_mac) and (device.model or "").strip().upper().startswith(
        SUPPORTED_MODEL_PREFIX
    )
```

Drop the model-prefix requirement so any device that answers on port 9529 with a usable MAC is
admitted:

```python
def _is_supported(device: EversoloDevice) -> bool:
    return bool(device.net_mac)
```

This is a deliberately blunt local bypass, not a real admission rule — it's only meant to get you
past setup on your own install, not something to send as a pull request.

## 3. Set it up and see what happens

Add the integration with `Add integration → Eversolo` and enter your device's IP, same as a
documented device. From there, every entity's `is_supported` gate reads the actual capability
flags the device reports (see the table in the [README](../README.md#entities)), so you'll only
get entities the device says it has — but nothing has verified those flags mean the same thing on
PLAY/T-series firmware as they do on the DMP-A8. Some entities may not appear, appear but not
work, or report nonsense state.

## 4. Report back

Whatever happens — it mostly works, it partially works, or it doesn't come up at all — please
[open an issue](https://github.com/tmeynell/ha-eversolo/issues/new) with your device model,
firmware version, and what you saw (a debug log of the config flow and first few coordinator
refreshes is the most useful thing you can attach). That's the only path to turning "likely
works" into an actual supported line: real evidence from real hardware is what would let
[#11](https://github.com/tmeynell/ha-eversolo/issues/11) reopen and widen the admission gate for
everyone, without the manual patch.
