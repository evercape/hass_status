"""Open-issue count for a status board."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_BY_CATEGORY, ATTR_CHECKS, LISTENER_SUMMARY, unique_id_summary
from .engine import HassStatusConfigEntry
from .entity import HassStatusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HassStatusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the open-issue sensor for this board."""
    async_add_entities([OpenIssuesSensor(entry.runtime_data)])


class OpenIssuesSensor(HassStatusEntity, SensorEntity):
    """How many checks are currently a problem.

    The number has no statistics class. It changes when a check opens or
    closes, not when a source sensor reports a new reading.
    """

    _attr_translation_key = "open_issues"

    def __init__(self, runtime) -> None:
        """Create the summary sensor."""
        super().__init__(runtime, LISTENER_SUMMARY)
        self._attr_unique_id = unique_id_summary(runtime.entry.entry_id)

    @property
    def native_value(self) -> int:
        """Return the number of open checks."""
        return self._runtime.summary.open_count

    @property
    def icon(self) -> str:
        """Use a calm icon at zero and an alert icon when something is open."""
        if self._runtime.summary.open_count:
            return "mdi:alert-circle"
        return "mdi:check-circle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the open check ids and the count in each category."""
        summary = self._runtime.summary
        return {
            ATTR_CHECKS: list(summary.checks),
            ATTR_BY_CATEGORY: dict(summary.by_category),
        }
