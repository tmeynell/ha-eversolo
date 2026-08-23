"""Image tests: per-option thumbnails for the VU/spectrum style pickers (#17).

Static content, dynamic timestamp: the picture behind an option never changes,
so the one thing worth pinning down is that the URL resolves against the
device host, and that ``image_last_updated`` moves only when the list's
current choice does — not on every settings poll.
"""

from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.eversolo.const import (
    SETTINGS_REFRESH_CYCLES,
    SETTING_TAG_SPECTRUM_MODE,
    SETTING_TAG_VU_MODE,
)

from .helpers import (
    BASE_URL,
    GET_MODEL,
    GET_SYSTEM_SETTINGS,
    advance_cycles,
    entity_id_for,
    entity_object,
    fixture_json,
    prime_device,
    settings_without,
    setup_integration,
)

IMAGE_DOMAIN = "image"


def _images_matching(hass: HomeAssistant, unique_id_key: str) -> list[str]:
    """Every image entity whose unique_id carries a key.

    Not the entity_id: HA slugifies that from the *option's* translated
    friendly name (``vu_meter_1_preview``), not from the description key this
    is actually keyed on.
    """
    registry = er.async_get(hass)
    return [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == IMAGE_DOMAIN and unique_id_key in entry.unique_id
    ]


async def test_a_vu_style_option_gets_a_preview_image(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """One image entity per option, resolved against the device host."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    entity_id = entity_id_for(hass, "_vu_style_preview_0")
    entity = entity_object(hass, entity_id)

    assert (
        entity.image_url
        == f"{BASE_URL}/SystemSettings/getItemSettingIcon?iconName=t10_setting_uv_default05.png"
    )
    assert entity.image_last_updated is not None
    # The friendly name is templated with *this* option's own title, not a
    # generic "VU style preview" shared by all twelve — pinned here because
    # ``test_translations``'s pattern-matching check can't tell one option's
    # title from another's.
    state = hass.states.get(entity_id)
    assert state.attributes["friendly_name"].endswith("VU meter 1 preview")


async def test_every_vu_style_option_is_covered(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The full list gets an entity each, not just the current choice."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    assert len(_images_matching(hass, "vu_style_preview")) == 12


async def test_every_spectrum_style_option_is_covered(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Same coverage on the spectrum list, which is shorter than the VU one."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    assert len(_images_matching(hass, "spectrum_style_preview")) == 9


async def test_the_picture_survives_a_settings_poll_that_changes_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A poll that reports the same current choice does not re-timestamp.

    The device's icon assets are static, so re-stamping every cycle would just
    force the frontend to refetch a picture that has not changed.
    """
    prime_device(aioclient_mock)
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_vu_style_preview_0"))
    first_stamp = entity.image_last_updated

    await advance_cycles(hass, freezer, 6)

    assert entity.image_last_updated == first_stamp


async def test_the_picture_is_restamped_when_the_current_choice_moves(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A settings re-read reporting a new ``currentIndex`` bumps the timestamp.

    Nothing else in this integration invalidates an ``image`` entity's
    client-side cache, so the list's own current-choice movement is used as
    the signal that a fresh fetch is worth forcing.
    """
    prime_device(aioclient_mock)
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_vu_style_preview_0"))
    first_stamp = entity.image_last_updated

    moved = fixture_json("getvumodelist.json")
    moved["currentIndex"] = 1
    aioclient_mock.clear_requests()
    prime_device(
        aioclient_mock,
        {"/SystemSettings/displaySettings/getVUModeList": {"json": moved}},
    )

    await advance_cycles(hass, freezer, 6)

    assert entity.image_last_updated is not None
    assert entity.image_last_updated > first_stamp


async def test_previews_still_appear_if_the_profile_read_first_fails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A slow-to-answer profile read must not permanently suppress previews.

    ``has_vu_style``/``has_spectrum_style`` are only known once the profile
    read (``getModel``) succeeds. A unit that fails that read on its first
    attempt gets its ``vu_mode_state`` settings fetch deferred behind it,
    which can land after the unrelated DSP/EQ gates settle — the point
    ``async_add_capability_gated`` normally stops watching for new entities.
    Without ``image.py``'s ``keep_watching`` override, the preview entities
    would never appear without a manual reload.
    """
    prime_device(
        aioclient_mock,
        {GET_MODEL: {"exc": aiohttp.ClientError("device still waking")}},
    )
    await setup_integration(hass)
    assert not _images_matching(hass, "vu_style_preview")

    aioclient_mock.clear_requests()
    prime_device(aioclient_mock)

    await advance_cycles(hass, freezer, SETTINGS_REFRESH_CYCLES * 2)

    assert len(_images_matching(hass, "vu_style_preview")) == 12
    assert len(_images_matching(hass, "spectrum_style_preview")) == 9


async def test_no_preview_images_when_the_unit_has_no_style_lists(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A unit without the VU/spectrum tags gets no preview images either."""
    prime_device(
        aioclient_mock,
        {
            GET_SYSTEM_SETTINGS: {
                "json": settings_without(SETTING_TAG_VU_MODE, SETTING_TAG_SPECTRUM_MODE)
            }
        },
    )
    await setup_integration(hass)

    assert not hass.states.async_entity_ids(IMAGE_DOMAIN)
