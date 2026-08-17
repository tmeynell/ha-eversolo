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
