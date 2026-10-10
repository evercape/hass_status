"""Hass Status integration.

Home Assistant imports stay inside the setup functions so the verdict
logic can be tested without installing Home Assistant.
"""

from __future__ import annotations

from typing import Any


async def async_setup(hass: Any, config: dict[str, Any]) -> bool:
    """YAML setup is unused. Boards are created in the UI."""
    return True


async def async_setup_entry(hass: Any, entry: Any) -> bool:
    """Set up one status board."""
    from homeassistant.helpers import entity_registry as er

    from .const import PLATFORMS
    from .engine import HassStatusRuntime, kept_unique_ids

    runtime = HassStatusRuntime(hass, entry)
    runtime.load()
    runtime.async_sync_labels()
    runtime.async_start()
    entry.runtime_data = runtime
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    _async_remove_stale_entities(hass, entry, er, kept_unique_ids)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: Any, entry: Any) -> bool:
    """Unload a board and stop its listeners."""
    from .const import PLATFORMS

    entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: Any, entry: Any) -> None:
    """Reload after a check or group is added, edited, or removed."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_remove_stale_entities(hass: Any, entry: Any, er: Any, kept_unique_ids: Any) -> None:
    """Drop entities whose check or group was removed.

    The board uses one device. Entities are not linked to individual
    subentries, because a device can belong to only one subentry.
    """
    keep = kept_unique_ids(entry.entry_id, entry.runtime_data)
    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.unique_id not in keep:
            registry.async_remove(item.entity_id)
