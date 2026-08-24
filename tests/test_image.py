"""Image tests: style-picker thumbnails (#17) and the panel screenshot (#37).

The option previews are static content, dynamic timestamp: the picture behind
an option never changes, so the one thing worth pinning down there is that the
URL resolves against the device host, and that ``image_last_updated`` moves
only when the list's current choice does — not on every settings poll.

The panel screenshot is the opposite: the picture genuinely changes, so its
own tests cover the actual fetch — the success path, the firmware-lacks-it
error-body path, and a device that is simply unreachable — plus its own,
independent refresh timer.
"""

from __future__ import annotations

from datetime import timedelta

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.eversolo.const import (
    SCREENSHOT_REFRESH_INTERVAL,
    SETTINGS_REFRESH_CYCLES,
    SETTING_TAG_SPECTRUM_MODE,
    SETTING_TAG_VU_MODE,
)

from .helpers import (
    BASE_URL,
    GET_MODEL,
    GET_SCREENSHOT,
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

# Just enough of a PNG for ``infer_image_type`` to recognise it — the entity
# only sniffs the magic number, it never decodes the image.
FAKE_PNG = b"\x89PNG\r\n\x1a\nrest-of-a-fake-panel-screenshot"
SCREENSHOT_ERROR_BODY = {"status": 804, "msg": "Url error"}


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
    """A unit without the VU/spectrum tags gets no preview images either.

    The panel screenshot is unconditional (#37), so it is still there — this
    only pins down that the *option* previews are gone.
    """
    prime_device(
        aioclient_mock,
        {
            GET_SYSTEM_SETTINGS: {
                "json": settings_without(SETTING_TAG_VU_MODE, SETTING_TAG_SPECTRUM_MODE)
            }
        },
    )
    await setup_integration(hass)

    assert not _images_matching(hass, "vu_style_preview")
    assert not _images_matching(hass, "spectrum_style_preview")
    assert _images_matching(hass, "panel_screenshot")


async def test_panel_screenshot_entity_appears_unconditionally(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The screenshot entity exists from setup, with a stamp already set."""
    prime_device(aioclient_mock)
    await setup_integration(hass)

    entity_id = entity_id_for(hass, "_panel_screenshot")
    entity = entity_object(hass, entity_id)

    assert entity.image_last_updated is not None
    assert entity.content_type == "image/png"
    state = hass.states.get(entity_id)
    assert state.attributes["friendly_name"].endswith("Panel screenshot")


async def test_panel_screenshot_fetches_the_devices_png(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """``async_image`` returns the raw bytes ``getScreenShot`` answered with."""
    prime_device(aioclient_mock, {GET_SCREENSHOT: {"content": FAKE_PNG}})
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_panel_screenshot"))

    assert await entity.async_image() == FAKE_PNG


async def test_panel_screenshot_does_not_surface_the_firmwares_error_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Firmware without ``getScreenShot`` answers 200 with a JSON error body.

    That must not come back as "the image", or the frontend would try to
    render JSON as a picture (#37's acceptance criteria).
    """
    prime_device(aioclient_mock, {GET_SCREENSHOT: {"json": SCREENSHOT_ERROR_BODY}})
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_panel_screenshot"))

    assert await entity.async_image() is None


async def test_panel_screenshot_degrades_gracefully_when_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A device that is off or unreachable yields no picture, not an exception."""
    prime_device(
        aioclient_mock,
        {GET_SCREENSHOT: {"exc": aiohttp.ClientError("device unreachable")}},
    )
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_panel_screenshot"))

    assert await entity.async_image() is None


async def test_panel_screenshot_restamps_on_its_own_timer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """The timestamp bumps on ``SCREENSHOT_REFRESH_INTERVAL``, not the live tier.

    Nothing here fetches a picture — it only has to force the frontend to ask
    again, on a cadence independent of the coordinator's own polling.
    """
    prime_device(aioclient_mock)
    await setup_integration(hass)
    entity = entity_object(hass, entity_id_for(hass, "_panel_screenshot"))
    first_stamp = entity.image_last_updated

    # A handful of live cycles, well short of the screenshot's own interval.
    await advance_cycles(hass, freezer, 5)
    assert entity.image_last_updated == first_stamp

    freezer.tick(timedelta(seconds=SCREENSHOT_REFRESH_INTERVAL))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert entity.image_last_updated is not None
    assert entity.image_last_updated > first_stamp
