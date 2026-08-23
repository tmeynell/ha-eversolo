"""Constants for eversolo."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

NAME = "Eversolo"
DOMAIN = "eversolo"
ATTRIBUTION = ""

DEFAULT_PORT = 9529

# The coordinator polls at two speeds. The live tier (``getState``) runs every
# cycle; the settings tier runs every sixth cycle (~30 s) and after any write.
# Five seconds is fine for the seek bar because the frontend extrapolates from
# ``media_position_updated_at``.
LIVE_UPDATE_INTERVAL = 5
SETTINGS_REFRESH_CYCLES = 6

# How many live cycles the DSP and EQ gates are waited for before their absence
# is read as "this unit has neither". Those two alone are carried by
# ``getState``, so unlike every other capability they can be *omitted* rather
# than answered, and the coordinator cannot tell that apart from a no.
#
# Bounded on purpose. The gates are latched once, so entities do not appear and
# vanish under a running Home Assistant; waiting indefinitely would therefore
# leave a unit that never reports them permanently undecided, which is a worse
# failure than the one being fixed.
#
# Counted in cycles *including* the one the profile landed on, so six is the
# profile's own cycle plus five more polls — 25 s at a 5 s interval, not 30.
# Worth knowing before anyone retunes this against a wall-clock target.
PROCESSING_GATE_CYCLES = 6

# The CD is not a hardware input: a disc plays through the internal player, so
# the "CD" source is synthetic and selecting it just switches the input there.
CD_SOURCE = "CD"
INPUT_INTERNAL_PLAYER = "XMOS"

# Top-level ``playType`` for Bluetooth — see ``EversoloPlayback.from_state``.
# The only source ``from_state`` never gives a cover for, hence the one the
# optional MusicBrainz lookup (#18) targets.
PLAY_TYPE_BLUETOOTH = 4

# The power menu's tag for the screen. ``setPowerOption?tag=screen`` toggles the
# front display, and the tag appearing in ``getPowerOption`` is the only thing
# that says the unit has a screen to switch — nothing anywhere reports whether
# it is currently on.
POWER_TAG_SCREEN = "screen"

# Wake-on-LAN ports the unit answers a magic packet on. Port 9 is what #10's
# F5 proved wakes this firmware in ~15 s; 9517 is what the vendor's own app
# broadcasts to (``WakeThread.wakeOnLan``, the app's only wake mechanism —
# there is no HTTP boot endpoint anywhere in it). Both cost one extra
# datagram, so both are sent; see RESEARCH.md "Power-on is Wake-on-LAN, and
# nothing else".
WAKE_ON_LAN_PORTS = (9, 9517)

# Gap before the retry send. Live-tested 2026-08-18 (RESEARCH.md, "A single WoL
# magic packet can silently fail; the integration doesn't retry"): sends timed
# at a ~15-16 s gap since power-off went unanswered every time, sends at 30 s+
# woke the unit every time — the unit's NIC isn't listening for a magic packet
# immediately after the soft-off command, not random packet loss. 20 s clears
# every observed failure with margin.
WAKE_ON_LAN_RETRY_DELAY = 20

# Tags in the getSystemSettings tree, which is where the device reports both
# that a feature exists — its tag is absent on units without the hardware — and,
# for a ``?switch=`` toggle, whether it is currently on.
SETTING_TAG_CD_AUTO_PLAY = "SettingsItemTagCDAutoPlay"
SETTING_TAG_SUBWOOFER = "SettingsItemTagSubOutput"
SETTING_TAG_MASTER_CLOCK = "SettingsItemTagMasterClock"
SETTING_TAG_ANALOG_PANEL = "SettingsItemTagXLROutput"
SETTING_TAG_KNOB_COLOR = "SettingsItemTagKnobLightColorList"
SETTING_TAG_GAPLESS = "SettingsItemTagGallessnewPlay"
SETTING_TAG_EOS_ENGINE = "SettingsItemTagEOSEngine"
SETTING_TAG_SCREEN_BRIGHTNESS = "SettingsItemTagScreenBrightness"
SETTING_TAG_VU_MODE = "SettingsItemTagVUMode"
SETTING_TAG_SPECTRUM_MODE = "SettingsItemTagSpPlayMode"
# The subwoofer's own on/off lives one level down, inside the sub-page
# ``SETTING_TAG_SUBWOOFER`` points at, not in the main tree.
SETTING_TAG_SUBWOOFER_SWITCH = "SettingsItemTagSubSwitchEnable"

# Options-flow key for the MusicBrainz/Cover Art Archive Bluetooth cover-art
# lookup (#18). Off by default: unlike every other read in this integration,
# enabling it sends the current track's artist/title/album off-LAN.
CONF_ENABLE_MUSICBRAINZ_LOOKUP = "enable_musicbrainz_lookup"

# MusicBrainz's own search API and Cover Art Archive's per-release front-cover
# redirect, per https://musicbrainz.org/doc/MusicBrainz_API and
# https://musicbrainz.org/doc/Cover_Art_Archive/API. Only the search step is
# rate-limited by MusicBrainz's policy; Cover Art Archive has none of its own.
MUSICBRAINZ_SEARCH_URL = "https://musicbrainz.org/ws/2/release"
COVER_ART_ARCHIVE_URL = "https://coverartarchive.org/release"
MUSICBRAINZ_CONTACT_URL = "https://github.com/tmeynell/ha-eversolo"
# MusicBrainz declines outright above ~1 request/second average per IP.
MUSICBRAINZ_MIN_REQUEST_INTERVAL = 1.0
