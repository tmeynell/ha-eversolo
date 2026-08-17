# Test fixtures — real DMP-A8 Gen 2 captures

Every JSON file here is a real payload captured from Tim's DMP-A8 Gen 2 Master Edition
(`192.168.0.60:9529`, firmware `v1.1.50`), straight off the legacy port-9529
Zidoo-lineage API. These drive the mocked-HTTP test seam (`aioclient_mock`);
tests must never hit a live device.

Captured 2026-08-13 unless the table says otherwise.

| Fixture | Endpoint | Notes |
|---|---|---|
| `getmodel.json` | `/ControlCenter/getModel` | identity + power flags; `net_mac` `aa:bb:cc:00:00:01` |
| `getstate_cd.json` | `/ZidooMusicControl/v2/getState` | **real, unmodified** — a disc was loaded, so `playingMusic.extension=="cd"` |
| `getstate_streaming.json` | `/ZidooMusicControl/v2/getState` | **derived** — see below |
| `getstate_earc.json` | `/ZidooMusicControl/v2/getState` | **derived** — the inert TV/eARC input, see below |
| `getsystemsettings.json` | `/SystemSettings/getSystemSettings` | self-documenting settings tree; source of capability detection |
| `getinputandoutputlist.json` | `/ZidooMusicControl/v2/getInputAndOutputList` | inputs XMOS/BT/SPDIF/EARC, outputs XLR/RCA/XLRRCA/… |
| `getvumodelist.json` | `/SystemSettings/displaySettings/getVUModeList` | VU meter styles |
| `getspplaymodelist.json` | `/SystemSettings/displaySettings/getSpPlayModeList` | spectrum styles |
| `getscreenbrightness.json` | `/SystemSettings/displaySettings/getScreenBrightness` | `currentValue:30 max:255 min:0` — real max is **255**, not the 115 earlier docs assumed |
| `getmasterclocklist.json` | `/SystemSettings/audioSettings/getMasterClockList` | OCXO 10M / 50Ω 10M / 50Ω 25M |
| `getxlroutputoption.json` | `/SystemSettings/audioSettings/getXlrOutputOption` | shared XLR/RCA analog panel |
| `getxlroutputpcmfilterlist.json` | `…/xlrOutputOption/getXlrOutputPcmFilterList` | DAC filter list |
| `getxlroutputupsamplinglist.json` | `…/xlrOutputOption/getXlrOutputUpSamplingList` | upsampling list |
| `getsuboutputoption.json` | `/SystemSettings/audioSettings/getSubOutputOption` | subwoofer output |
| `getpoweroption.json` | `/ZidooMusicControl/v2/getPowerOption` | screen/power tags |
| `getknobsettingoption.json` | `/SystemSettings/displaySettings/getKnobSettingOption` | **`items: []`** — knob is absent on the A8 (capability gate off) |
| `getdspconfiglist.json` | `/ZidooMusicControl/v2/getDSPConfigList` | 2026-08-15 — all DSP/PEQ profiles + `dspRange` limits; see below |
| `getdspconfig.json` | `/ZidooMusicControl/v2/getDSPConfig?id=1` | 2026-08-15 — single profile, `id` is the only accepted param |
| `getdsppresetlist.json` | `/ZidooMusicControl/v2/getDSPPresetList` | 2026-08-15 — 23 named EQ presets, Flat / Pop / … / Treble Reducer |
| `getdspsourceinlist.json` | `/ZidooMusicControl/v2/getDSPSourceInList?isDSP=1` | 2026-08-15 — per-input profile assignment + enable; the only place this mapping appears |

## The three `getState` fixtures

At capture time a CD was loaded **and** Spotify Connect was streaming over the XMOS
input simultaneously. The device reports the loaded disc in `playingMusic`
(`extension:"cd"`, `trackIndex:-1`, `albumId:0`, `hasQueue:false`) while
`everSoloPlayInfo.everSoloPlayAudioInfo` tracks the live Spotify track.

- **`getstate_cd.json`** is that capture, byte-for-byte — the authoritative CD-present
  shape (source must read back as `CD` because `playingMusic.extension=="cd"`).
- **`getstate_streaming.json`** is **derived** from the same capture so downstream
  `media_player` tests have a no-CD shape without disturbing the device. Only
  `playingMusic` was reshaped from the capture's own real `everSoloPlayAudioInfo`
  (title/artist/bits/sampleRate) and `extension` cleared to `""`; every other block
  (`everSoloPlayInfo`, `volumeData`, `deviceInfo`, capability flags) is untouched real
  data. When a disc-free capture is available it should replace this file.
- **`getstate_earc.json`** is **derived** the same way, reproducing what issue #03
  observed live while the unit sat on the **TV/eARC** input: the disc is still reported
  in `playingMusic`, but the player is inert — `position`/`duration` zeroed, every
  `everSoloPlayInfo.isCan*` flag `false`, and `playStatus:0`. It drives the "transport
  is inert on eARC" tests, which depend only on those flags.

  **Unverified beyond that.** #03 recorded nothing about the rest of the payload on that
  input, so the remaining edits are plausible reconstruction, not observation:
  `everSoloPlayAudioInfo` emptied (no streaming session), `volumeData.intputTag` set to
  `EARC-EARC`, and `everSoloPlayOutputInfo` left exactly as the capture had it. Do not
  read a claim about eARC behaviour out of those fields, and replace the whole file with
  a real eARC capture when one is taken.

## The DSP/PEQ fixtures

All three are **real, unmodified** captures. `getdspconfiglist.json` holds three profiles:

| `id` | `name` | Origin |
|---|---|---|
| 1 | `wiim rc only` | Tim's own room correction — 20 PEQ bands per channel, non-flat |
| 3 | `wiim rc with eq` | Tim's own, PEQ disabled on both channels |
| 4 | `claude test` | **scratch** — a clone of `id:1` made on 2026-08-15 while mapping the write API |

`claude test` exists only to give writes a target that is not a real profile. It is a
faithful copy of `id:1` (identical `peqFirList` on both channels; differs only in `id`,
`name`, `createTime` and `type`). Delete it on the device and re-capture if it gets in
the way — but keep *some* throwaway profile before exercising `saveDSPConfig`.

`type` is **not** a selected/active marker: `id:1` is `0` while `id:3` and `id:4` are `1`,
and the value does not move when the device switches to an input using that profile.
Nothing in this payload records which input uses which profile — that mapping appears
only in `getDSPSourceInList`, captured as `getdspsourceinlist.json`.
