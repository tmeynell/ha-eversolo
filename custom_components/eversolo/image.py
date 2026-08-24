"""Image platform for eversolo — per-option thumbnails for style pickers.

``vu_style``/``spectrum_style`` (select.py) offer titles the device lists, but
say nothing about what each one looks like. This adds one ``image`` entity per
option, sourced from that option's own ``icon`` field
(``getvumodelist.json``/``getspplaymodelist.json``), so a dashboard can show a
clickable picker rather than a bare dropdown. The selects themselves are
untouched — this is additive (#17).

Entities are keyed by the select's list *and* the option's device index, not
just position, since the device's own numbering is what a write and a
re-poll both key on (see ``EversoloOption.index``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from dataclasses import dataclass

from homeassistant.components.image import (
    ImageEntity,
    ImageEntityDescription,
    infer_image_type,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .api import EversoloApiClientError
from .const import SCREENSHOT_REFRESH_INTERVAL
from .coordinator import EversoloConfigEntry, EversoloDataUpdateCoordinator
from .data import (
    EversoloCapabilities,
    EversoloData,
    EversoloOption,
    EversoloOptionList,
    read_option_list,
)
from .entity import EversoloEntity, async_add_capability_gated


@dataclass(frozen=True, kw_only=True)
class EversoloImageDescription(ImageEntityDescription):
    """One option list whose entries get a preview-image entity each."""

    is_supported: Callable[[EversoloCapabilities], bool]
    # The settings-tier key this list lives under, not a bare ``read``
    # callable — kept as data rather than a closure so ``async_setup_entry``
    # can also ask "has this key ever been fetched at all" (see
    # ``_a_supported_list_is_still_unread``), which a callable can't answer.
    settings_key: str

    def read(self, data: EversoloData) -> EversoloOptionList:
        """Read this list as the device most recently reported it."""
        return read_option_list(self.settings_key)(data)


ENTITY_DESCRIPTIONS: tuple[EversoloImageDescription, ...] = (
    EversoloImageDescription(
        key="vu_style_preview",
        translation_key="vu_style_preview",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_supported=lambda capabilities: capabilities.has_vu_style,
        settings_key="vu_mode_state",
    ),
    EversoloImageDescription(
        key="spectrum_style_preview",
        translation_key="spectrum_style_preview",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_supported=lambda capabilities: capabilities.has_spectrum_style,
        settings_key="spectrum_mode_state",
    ),
)


def _build(
    coordinator: EversoloDataUpdateCoordinator, capabilities: EversoloCapabilities
) -> list[ImageEntity]:
    """Build one preview image per option this unit's lists actually carry.

    Only options with a ``preview_path`` are given an entity — a unit whose
    list entries never carried an ``icon`` (nobody has captured one, but
    nothing here assumes every firmware does) would otherwise show an image
    entity that can never resolve a picture.
    """
    return [
        EversoloOptionPreviewImage(coordinator, description, option)
        for description in ENTITY_DESCRIPTIONS
        if description.is_supported(capabilities)
        for option in description.read(coordinator.data).options
        if option.preview_path
    ]


def _a_supported_list_is_still_unread(
    coordinator: EversoloDataUpdateCoordinator,
) -> bool:
    """Whether a list this unit has justifies has never been fetched at all.

    ``async_add_capability_gated`` normally stops watching once the DSP/EQ
    gates settle, which is unrelated to whether ``vu_mode_state``/
    ``spectrum_mode_state`` have actually been fetched yet: if the setup-time
    profile read fails its very first attempt, capabilities become known only
    on a later cycle, which can push this list's *first* settings-tier fetch
    past the cycle the DSP/EQ gates happen to settle on — after which nothing
    is listening any more, and the entry would need a manual reload to ever
    get these entities. A settings key is present in ``data.settings`` only
    once its fetch has actually succeeded once (``coordinator.py`` never sets
    it on a failure), so its absence — not its content — is the "still
    waiting" signal here.
    """
    capabilities = coordinator.data.capabilities
    if capabilities is None:
        return False
    return any(
        description.is_supported(capabilities)
        and description.settings_key not in coordinator.data.settings
        for description in ENTITY_DESCRIPTIONS
    )


async def async_setup_entry(hass, entry: EversoloConfigEntry, async_add_devices):
    """Set up the Image platform."""
    coordinator = entry.runtime_data

    # Unconditional, unlike the option previews below: every unit with a
    # front panel answers ``getScreenShot`` one way or another (a PNG, or the
    # firmware's own error body), and the entity treats the latter as "no
    # picture yet" rather than needing a capability to gate on.
    async_add_devices([EversoloPanelScreenshotImage(coordinator)])

    async_add_capability_gated(
        coordinator,
        async_add_devices,
        lambda capabilities: _build(coordinator, capabilities),
        keep_watching=lambda: _a_supported_list_is_still_unread(coordinator),
    )


class EversoloOptionPreviewImage(EversoloEntity, ImageEntity):
    """The device's own thumbnail for one option in a style list.

    Static: the picture behind ``preview_path`` never changes for a given
    option, so nothing here needs to swap images as the select's *current*
    choice changes elsewhere. It only has to notice that its own option is
    still in the list at all — a re-poll dropping it is treated the same as
    the device never having reported it.
    """

    entity_description: EversoloImageDescription

    def __init__(
        self,
        coordinator: EversoloDataUpdateCoordinator,
        entity_description: EversoloImageDescription,
        option: EversoloOption,
    ) -> None:
        """Initialize the preview image for one option."""
        EversoloEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self.entity_description = entity_description
        self._option_index = option.index
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_"
            f"{entity_description.key}_{option.index}"
        )
        self._attr_translation_placeholders = {"option": option.title}
        # The reading this entity was built from already has a current
        # index — seed against it so the first coordinator update after
        # creation doesn't mistake "unchanged" for "just changed".
        self._known_current_index = entity_description.read(
            coordinator.data
        ).current_index
        self._set_image_url(option.preview_path)

    @property
    def _option(self) -> EversoloOption | None:
        """This entity's option, as the device most recently listed it."""
        options = self.entity_description.read(self.coordinator.data)
        return next((o for o in options.options if o.index == self._option_index), None)

    def _set_image_url(self, preview_path: str | None) -> None:
        """Resolve and store the picture URL, timestamping it as fresh."""
        self._attr_image_url = self.coordinator.client.create_image_url_or_none(
            preview_path
        )
        self._attr_image_last_updated = dt_util.utcnow()

    def _handle_coordinator_update(self) -> None:
        """Refresh the picture only when the list's current choice moves on.

        The device's own icon assets are static, so there is nothing to gain
        from re-timestamping this every settings poll. But nothing else in
        this integration invalidates an ``image`` entity's client-side cache,
        so a re-poll that *does* change which option is selected is used as
        the signal to bump ``image_last_updated`` and force a fresh fetch,
        rather than leaving Home Assistant's frontend serving whatever it
        cached at startup indefinitely.
        """
        options = self.entity_description.read(self.coordinator.data)
        if options.current_index != self._known_current_index:
            self._known_current_index = options.current_index
            option = self._option
            self._set_image_url(option.preview_path if option else None)
        super()._handle_coordinator_update()


class EversoloPanelScreenshotImage(EversoloEntity, ImageEntity):
    """The device's front panel, captured live as a PNG (#37).

    Unlike ``EversoloOptionPreviewImage``, the picture behind this entity
    genuinely changes over time and the device offers no push for it — the
    only way to get a fresh frame is to poll ``getScreenShot`` again. This
    keeps its own timer for that, independent of the coordinator: a fetch is
    ~500 KB and ~0.65 s live-measured, both too heavy for the 5 s live tier
    (``SCREENSHOT_REFRESH_INTERVAL``, ``const.py``).

    It also bypasses the base ``ImageEntity``'s own ``image_url``/httpx fetch
    path entirely, fetching through the coordinator's aiohttp session instead
    (``async_image`` below). Two reasons: that keeps it on the same mocked-HTTP
    seam every other call in this integration is tested against, and the
    device answers a missing endpoint with HTTP 200 and a JSON error body
    rather than an error status — telling that apart from a real screenshot
    means reading the actual bytes, not just trusting the response arrived.
    """

    _attr_translation_key = "panel_screenshot"
    _attr_icon = "mdi:monitor-screenshot"
    _attr_content_type = "image/png"

    def __init__(self, coordinator: EversoloDataUpdateCoordinator) -> None:
        """Initialize the panel-screenshot image."""
        EversoloEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_panel_screenshot"
        self._attr_image_last_updated = dt_util.utcnow()

    async def async_added_to_hass(self) -> None:
        """Start the entity's own slow refresh timer."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_refresh,
                timedelta(seconds=SCREENSHOT_REFRESH_INTERVAL),
            )
        )

    async def _async_refresh(self, _now: object = None) -> None:
        """Bump the timestamp so the frontend refetches on its next look.

        Nothing here fetches the picture itself — ``async_image`` does that,
        lazily, whenever something actually asks for it (a dashboard card, the
        ``/api/image_proxy`` view). This only forces that ask to happen again,
        on a cadence independent of whether anything is currently watching.
        """
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """Fetch a fresh screenshot, or None if the device didn't hand one back.

        Covers the device being off or unreachable (the client raises, caught
        here) and firmware without ``getScreenShot`` (a 200 whose body isn't a
        PNG) the same way: no picture this round, no exception, no entity
        state flipped to unavailable — the frontend just keeps showing
        whatever it last had, same as any other camera-like entity between
        frames.
        """
        try:
            content = await self.coordinator.client.async_get_screenshot()
        except EversoloApiClientError:
            return None
        if infer_image_type(content) != "image/png":
            return None
        return content
