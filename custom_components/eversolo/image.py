"""Image platform for eversolo — per-option thumbnails for style pickers.

``vu_style``/``spectrum_style`` (select.py) offer titles the device lists, but
say nothing about what each one looks like. This adds one ``image`` entity per
option, sourced from that option's own ``icon`` field
(``getvumodelist.json``/``getspplaymodelist.json``), so a dashboard can show a
clickable picker rather than a bare dropdown. The selects themselves are
untouched — this is additive (#17).

Alongside that per-option gallery, one further entity per list tracks
whichever option is *currently* selected, so a dashboard can show what the
device is doing right now without the user picking through the gallery
themselves (#32).

Entities are keyed by the select's list *and* the option's device index, not
just position, since the device's own numbering is what a write and a
re-poll both key on (see ``EversoloOption.index``).
"""

from __future__ import annotations

from collections.abc import Callable

from dataclasses import dataclass

from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util

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
    # Key/translation for the one current-selection entity this list also
    # gets (#32), distinct from ``key``/``translation_key`` above, which
    # belong to the per-*option* gallery entities.
    current_key: str
    current_translation_key: str

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
        current_key="vu_style_current_preview",
        current_translation_key="vu_style_current_preview",
    ),
    EversoloImageDescription(
        key="spectrum_style_preview",
        translation_key="spectrum_style_preview",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_supported=lambda capabilities: capabilities.has_spectrum_style,
        settings_key="spectrum_mode_state",
        current_key="spectrum_style_current_preview",
        current_translation_key="spectrum_style_current_preview",
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

    Alongside those, one *current-selection* entity per supported list (#32):
    unlike the per-option gallery above, that one entity exists regardless of
    whether the option currently selected happens to carry a preview — the
    selection can move to one that does.
    """
    return [
        EversoloOptionPreviewImage(coordinator, description, option)
        for description in ENTITY_DESCRIPTIONS
        if description.is_supported(capabilities)
        for option in description.read(coordinator.data).options
        if option.preview_path
    ] + [
        EversoloCurrentSelectionPreviewImage(coordinator, description)
        for description in ENTITY_DESCRIPTIONS
        if description.is_supported(capabilities)
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

    async_add_capability_gated(
        coordinator,
        async_add_devices,
        lambda capabilities: _build(coordinator, capabilities),
        keep_watching=lambda: _a_supported_list_is_still_unread(coordinator),
    )


class _EversoloOptionListPreviewImage(EversoloEntity, ImageEntity):
    """Shared tracking for the two style-list preview entities (#17, #32).

    Both watch the same list for its current choice to move on, and re-
    timestamp exactly then — nothing else in this integration invalidates an
    ``image`` entity's client-side cache, so that movement is the signal used
    to force a fresh frontend fetch. They differ only in *which* option's
    picture that is: one option fixed at construction, or whichever the list
    currently selects — ``_resolve_option`` is that one seam.
    """

    entity_description: EversoloImageDescription

    def _start_tracking(
        self,
        coordinator: EversoloDataUpdateCoordinator,
        entity_description: EversoloImageDescription,
    ) -> None:
        """Seed tracking state and the initial picture.

        Called at the end of each subclass's own ``__init__``, once whatever
        ``_resolve_option`` needs (e.g. ``_option_index``) is already set —
        the reading this entity was built from already has a current index,
        so seeding against it here means the first coordinator update after
        creation can't mistake "unchanged" for "just changed".
        """
        self.entity_description = entity_description
        self._known_current_index = entity_description.read(
            coordinator.data
        ).current_index
        self._set_image_url()

    def _resolve_option(self) -> EversoloOption | None:
        """Return this entity's option, as the device most recently listed it."""
        raise NotImplementedError

    def _set_image_url(self) -> None:
        """Resolve and store the picture URL, timestamping it as fresh.

        Also drops the base ``ImageEntity``'s ``_cached_image`` bytes: it
        caches whatever ``async_image`` first fetched and never re-fetches on
        its own, so a resolved-URL change here would otherwise keep serving
        the previous picture forever (HA's ``fyta`` integration hits the same
        staleness and clears its cache the same way).
        """
        option = self._resolve_option()
        self._attr_image_url = self.coordinator.client.create_image_url_or_none(
            option.preview_path if option else None
        )
        self._cached_image = None
        self._attr_image_last_updated = dt_util.utcnow()

    def _handle_coordinator_update(self) -> None:
        """Refresh the picture only when the list's current choice moves on."""
        options = self.entity_description.read(self.coordinator.data)
        if options.current_index != self._known_current_index:
            self._known_current_index = options.current_index
            self._set_image_url()
        super()._handle_coordinator_update()


class EversoloOptionPreviewImage(_EversoloOptionListPreviewImage):
    """The device's own thumbnail for one option in a style list.

    Static: the picture behind ``preview_path`` never changes for a given
    option, so nothing here needs to swap images as the select's *current*
    choice changes elsewhere. It only has to notice that its own option is
    still in the list at all — a re-poll dropping it is treated the same as
    the device never having reported it.
    """

    def __init__(
        self,
        coordinator: EversoloDataUpdateCoordinator,
        entity_description: EversoloImageDescription,
        option: EversoloOption,
    ) -> None:
        """Initialize the preview image for one option."""
        EversoloEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self._option_index = option.index
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_"
            f"{entity_description.key}_{option.index}"
        )
        self._attr_translation_placeholders = {"option": option.title}
        self._start_tracking(coordinator, entity_description)

    def _resolve_option(self) -> EversoloOption | None:
        options = self.entity_description.read(self.coordinator.data)
        return next((o for o in options.options if o.index == self._option_index), None)


class EversoloCurrentSelectionPreviewImage(_EversoloOptionListPreviewImage):
    """Whichever option a style list currently has selected (#32).

    One entity per list, unlike ``EversoloOptionPreviewImage``'s one-per-
    option gallery — this tracks *which* picture is live rather than
    offering every picture the device could show.
    """

    def __init__(
        self,
        coordinator: EversoloDataUpdateCoordinator,
        entity_description: EversoloImageDescription,
    ) -> None:
        """Initialize the current-selection preview image."""
        EversoloEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.current_key}"
        )
        self._attr_translation_key = entity_description.current_translation_key
        self._start_tracking(coordinator, entity_description)

    def _resolve_option(self) -> EversoloOption | None:
        return self.entity_description.read(self.coordinator.data).current
