"""EversoloEntity class, and how capability-gated platforms add their entities."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, NAME
from .coordinator import EversoloDataUpdateCoordinator
from .data import EversoloCapabilities


@callback
def async_add_capability_gated(
    coordinator: EversoloDataUpdateCoordinator,
    async_add_entities: AddEntitiesCallback,
    build: Callable[[EversoloCapabilities], Iterable[Entity]],
    *,
    keep_watching: Callable[[], bool] | None = None,
) -> None:
    """Add the entities this unit's hardware justifies, as it admits to them.

    Capabilities come from the setup-time profile read, which a unit that was
    switched off when Home Assistant started has not answered yet. Deciding
    "no CD, then" at that moment would leave the CD controls missing until the
    user reloaded the entry, so an unknown answer means waiting: the platform
    subscribes and adds them on the poll where the device finally says.

    The answers do not all arrive together, either. The DSP and EQ gates are
    fields of ``getState`` rather than answers from an endpoint, so they can be
    published still provisional and gain their real value a few cycles later
    (see the coordinator's ``_settle_capability_gates``). So this adds
    *incrementally* — every update, whatever the current capabilities justify
    that is not already present — until the coordinator reports the gates
    settled and nothing further can appear.

    ``keep_watching`` covers a narrower, independent race: a ``build`` whose
    output depends on the settings tier (a per-*option* platform, not a fixed
    one-entity-per-capability platform) can still be waiting on a settings key
    that has not been fetched even once — e.g. because the setup-time profile
    read itself failed its first attempt, which defers that key's first fetch
    past the cycle the DSP/EQ gates happen to settle on. Passed, this is
    consulted alongside ``capabilities_settled``, so this keeps listening
    until ``build`` has nothing further to add *and* whatever data it depends
    on has actually arrived — not just until the unrelated DSP/EQ gates say so.

    Growth only, never shrinkage: an entity already added is never removed if a
    later reading disagrees. Entities vanishing under a running Home Assistant
    is its own class of problem, and a gate that has answered once is a
    statement about hardware, which does not change while the unit runs.
    """
    added: set[str] = set()

    @callback
    def _add_newly_justified() -> None:
        """Create whatever the current capabilities justify and we lack.

        Deduplicated against ``added`` from earlier calls *and* within this
        call's own output — a ``build`` that ever yielded two entities sharing
        a unique_id (device data carrying a repeated index, say) would
        otherwise hand ``async_add_entities`` a batch with a collision.
        """
        if (capabilities := coordinator.data.capabilities) is None:
            return
        fresh = []
        for entity in build(capabilities):
            if entity.unique_id in added:
                continue
            added.add(entity.unique_id)
            fresh.append(entity)
        if fresh:
            async_add_entities(fresh)

    def _done_watching() -> bool:
        return coordinator.capabilities_settled and (
            keep_watching is None or not keep_watching()
        )

    _add_newly_justified()
    if _done_watching():
        return

    remove_listener: Callable[[], None] | None = None

    @callback
    def _stop_waiting() -> None:
        """Unsubscribe, whether every answer arrived or the entry unloaded."""
        nonlocal remove_listener
        if remove_listener is not None:
            remove_listener()
            remove_listener = None

    @callback
    def _on_update() -> None:
        _add_newly_justified()
        if _done_watching():
            _stop_waiting()

    remove_listener = coordinator.async_add_listener(_on_update)
    coordinator.config_entry.async_on_unload(_stop_waiting)


class EversoloEntity(CoordinatorEntity[EversoloDataUpdateCoordinator]):
    """EversoloEntity class."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: EversoloDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.config_entry.entry_id
        # Model and firmware come from the coordinator's setup-time getModel
        # read; entry data holds the host alone.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=coordinator.config_entry.title,
            model=coordinator.device.model,
            sw_version=coordinator.device.firmware,
            manufacturer=NAME,
        )
