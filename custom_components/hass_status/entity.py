"""Shared entity base for Hass Status."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, VERSION
from .engine import HassStatusRuntime


class HassStatusEntity(Entity):
    """A board entity that writes state only when the runtime asks it to."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: HassStatusRuntime, listener_key: str) -> None:
        """Bind this entity to one board and one listener key."""
        self._runtime = runtime
        self._listener_key = listener_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry.entry_id)},
            name=runtime.entry.title,
            manufacturer="Hass Status",
            model="Status board",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe after the entity can write state."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.listen(self._listener_key, self._request_state)
        )

    @callback
    def _request_state(self) -> None:
        self.async_write_ha_state()
