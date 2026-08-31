# Test fixtures — real DMP-A8 Gen 2 captures

Every JSON file here is a real payload captured from Tim's DMP-A8 Gen 2 Master Edition,
straight off the legacy port-9529 Zidoo-lineage API. These drive the mocked-HTTP test
seam (`aioclient_mock`); tests must never hit a live device. One non-JSON fixture,
`capture.h264`, is documented separately at the bottom — it drives the cast-mode
decoder instead, and never touches the HTTP seam at all.

Captured 2026-08-13 against `192.168.0.60:9529`, firmware `v1.1.50`, unless the table
says otherwise — two `getState` fixtures were captured 2026-08-17 against the device's
later address and firmware, `192.168.0.63`/Ethernet, `v1.1.80` (see the address/firmware
history in the project's `CLAUDE.md`).

| Fixture | Endpoint | Notes |
|---|---|---|
| `getmodel.json` | `/ControlCenter/getModel` | identity + power flags; `net_mac` `aa:bb:cc:00:00:01` |
| `getstate_spotify_disc_loaded.json` | `/ZidooMusicControl/v2/getState` | **real, unmodified** — a disc was loaded *and* Spotify Connect was streaming at once; the suite's default `GET_STATE` answer, see below |
| `getstate_cd.json` | `/ZidooMusicControl/v2/getState` | 2026-08-17, `v1.1.80` — **real, unmodified** — a disc genuinely playing, nothing else live; see below |
| `getstate_bluetooth.json` | `/ZidooMusicControl/v2/getState` | 2026-08-17, `v1.1.80` — **real, unmodified** — a phone paired and playing over Bluetooth; see below |
| `getstate_streaming.json` | `/ZidooMusicControl/v2/getState` | **derived** — see below |
| `getstate_earc.json` | `/ZidooMusicControl/v2/getState` | **derived** — the inert TV/eARC input, see below |
| `getstate_local_file.json` | `/ZidooMusicControl/v2/getState` | 2026-08-23, `v1.1.80` — **real, unmodified** — a local library FLAC playing on the internal player, no disc involved; see below |
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
| `getcdlist.json` | `/ZidooMusicControl/v2/getCDList` | **derived** — see below |
| `getcdlist_empty.json` | `/ZidooMusicControl/v2/getCDList` | `[]` — the empty-tray shape, no disc loaded |
| `getalbums.json` | `/ZidooMusicControl/v2/getAlbums?start=0&count=3` | 2026-08-31, `192.168.0.63`/`v1.1.80` — **real, unmodified** — 3 of 384 albums |
| `getartists.json` | `/ZidooMusicControl/v2/getArtists?start=0&count=3` | 2026-08-31, same capture session — **real, unmodified** — 3 of 168 artists on this library |
| `getalbummusics.json` | `/ZidooMusicControl/v2/getAlbumMusics?id=469&start=0&count=3` | 2026-08-31 — **real, unmodified** — 3 of 11 tracks off `getalbums.json`'s own "A Moon Shaped Pool" (`id:469`) |
| `getartistalbums.json` | `/ZidooMusicControl/v2/getArtistAlbums?id=10000820&start=0&count=5` | 2026-08-31 — **real, unmodified** — the one album by `getartists.json`'s own "A Tribe Called Quest" (`id:10000820`) |
| `getrecentlyplayedmusiclist.json` | `/ZidooMusicControl/v2/getRecentlyPlayedMusicList?start=0&count=3` | 2026-08-31 — **real, unmodified** — 3 of 11 recently played tracks |
| `getalbummusics_empty.json` | `/ZidooMusicControl/v2/getAlbumMusics?id=999999999&...` | 2026-08-31 — **real, unmodified** — a nonexistent album id, proving the "no tracks" shape is `total:0, array:[]` rather than an error |
| `getalbums_empty.json`, `getartists_empty.json`, `getrecentlyplayedmusiclist_empty.json` | same three endpoints | **derived** — no library on hand is actually empty, so these hand-build the `total:0, array:[]` shape `getalbummusics_empty.json` proved live, for `browse_media`'s empty-library tests |

## The `getState` fixtures

### `getstate_spotify_disc_loaded.json` — the defect Tim reported

Captured 2026-08-13 against `192.168.0.60`, firmware `v1.1.50`. A CD was loaded **and**
Spotify Connect was streaming over the XMOS input simultaneously. The device reports the
loaded disc in `playingMusic` (`extension:"cd"`, `trackIndex:-1`, `albumId:0`,
`hasQueue:false`, `playType:6`) while `everSoloPlayInfo.everSoloPlayAudioInfo` tracks the
live Spotify track — this is precisely the source-attribution bug the completion map's
02/03 exist to fix. **Real, unmodified.** It was named `getstate_cd.json` until issue
#01 renamed it to say what it actually holds; it stays the suite's default `GET_STATE`
answer until 02 decides what the default should be.

### `getstate_cd.json` — a disc genuinely playing, nothing else live

Captured 2026-08-17 against `192.168.0.63`/Ethernet, firmware `v1.1.80`, by issue #01,
while charting. `playType:5`, `playTypeSubtitle:"LOCAL"` — `playType` is the audible-source
tag, and `5` means a locally-playing disc (`4` is Bluetooth, see below; anything else is a
streaming source). The streaming block (`everSoloPlayAudioInfo`) is genuinely empty
(all-zero/blank fields), unlike the Spotify capture above where it carries a live track.
**Real, unmodified** aside from the MAC scrub below. This is the fixture whose absence
let the source-attribution bug ride unnoticed: nothing in the suite, until now, held a
CD state that wasn't also secretly a Spotify stream.

### `getstate_local_file.json` — a local library FLAC, no disc involved

Captured 2026-08-23 against `192.168.0.63`/Ethernet, firmware `v1.1.80`, while charting
issue #35. `playType:5`, `intputTag:"XMOS-XMOS"`, `playingMusic.extension:"flac"`, no
`formIcon` — the state that exposed the source-attribution defect where `source` reported
the synthetic `CD` source whenever anything at all played from the internal player,
because `playType == 5` alone was tested rather than `playType == 5 and extension == "cd"`.
**Real, unmodified** aside from the MAC scrub below.

### `getstate_bluetooth.json` — a phone paired and playing over Bluetooth

Captured 2026-08-17 against `192.168.0.63`/Ethernet, firmware `v1.1.80`, by issue #01,
while charting. `playType:4`, `intputTag:"BT-BT"` — the BT audible-source tag. Carries
`everSoloBtInInfo` (paired-phone metadata: title/artist/album, `localBtMac`), which no
other fixture in this suite populates. `playingMusic.extension=="cd"` here too — a disc
was still sitting in the tray while a phone streamed over Bluetooth, the same
double-source shape as the Spotify capture above, just a different second source.
**Real, unmodified** aside from the MAC scrub below.

### The MAC scrub

All four captures above share the same wired `net_mac`, scrubbed the same way as every
other fixture — real value swapped for the usual `aa:bb:cc:00:00:01` placeholder. The
real-to-placeholder mapping is kept local-only, outside this repo entirely (this repo is
deliberately cloned outside the project's OneDrive tree — see `CLAUDE.md` — while that
mapping lives in the project's own notes, so there is no relative path from here to it).
`getstate_bluetooth.json` additionally carries a real Bluetooth address at
`everSoloBtInInfo.localBtMac`, in an unseparated 12-hex-digit format none of the other
scrub rules cover; it was replaced with the placeholder `AABBCC000004`, recorded in the
same local-only mapping.

### The two derived `getState` fixtures

- **`getstate_streaming.json`** is **derived** from the Spotify-and-disc capture so downstream
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

## `getcdlist.json`

**Derived**, not a raw capture. Issue #36's research (device decompiled from the T10
firmware, live-verified 2026-08-23 against `192.168.0.63`/`v1.1.80`) recorded the shape of
a genuine `getCDList` response with a disc loaded — `info.url` is what `playCDMusic`'s
`uri` must match exactly — but elided the 12-entry `musics` array as "…12 entries…" rather
than transcribing it. This fixture reproduces the real `info` block verbatim and leaves
`musics: []`, since the code under test only ever reads `info.url`. Replace with a real,
untruncated capture if one is taken. `getcdlist_empty.json` (`[]`) is the empty-tray shape
and needs no such caveat — an empty list is an empty list.

## `capture.h264`

**Real, unmodified.** Not a JSON fixture and not part of the HTTP seam: a raw Annex-B H.264
byte stream, captured 2026-08-23 against `192.168.0.63`/`v1.1.80` by the `prototype/screen-mirror-spike`
branch's throwaway spike (`prototypes/screen-mirror/out/capture.h264`, commit `d504dae`) reading
the cast-mode socket for 8 seconds. This is the screensaver clock, at the stream's real 960x360 —
`test_camera.py` feeds it straight to `camera._FrameCollector` to prove the decode step produces a
real JPEG without a live device. It carries no packet framing of its own (the spike's
`collect_packets` already stripped the 4-byte length prefix and 10-byte type/flag/pts header
before writing it) — `test_cast_session.py`'s framing tests build synthetic packets by hand
instead, since this file has nothing left to parse.
