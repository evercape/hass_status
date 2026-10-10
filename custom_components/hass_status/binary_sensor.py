"""Problem sensors for checks and groups."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_ACTIVE_CONDITION,
    ATTR_CATEGORY,
    ATTR_CATEGORY_COLOR,
    ATTR_CATEGORY_ICON,
    ATTR_DISPLAY_ENTITY,
    ATTR_FAILED_CONDITIONS,
    ATTR_FOCUS_ENTITY,
    ATTR_LOGIC,
    ATTR_MEMBERS,
    ATTR_MESSAGE,
    ATTR_OBJECT_ID,
    ATTR_OPEN_CHECKS,
    ATTR_OPEN_COUNT,
    ATTR_REASON,
    ATTR_SEVERITY,
    ATTR_SOURCES,
    listener_key_check,
    listener_key_group,
    unique_id_check,
    unique_id_group,
)
from .engine import HassStatusConfigEntry
from .entity import HassStatusEntity
from .evaluator import GroupVerdict, Verdict
from .models import CheckConfig, GroupConfig


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HassStatusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one binary sensor per check and per group."""
    runtime = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        CheckBinarySensor(runtime, check) for check in runtime.checks.values()
    ]
    entities.extend(
        GroupBinarySensor(runtime, group) for group in runtime.groups.values()
    )
    async_add_entities(entities)


def _check_attributes(verdict: Verdict) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        ATTR_OBJECT_ID: verdict.object_id,
        ATTR_SEVERITY: verdict.severity,
        ATTR_REASON: verdict.reason,
        ATTR_FOCUS_ENTITY: verdict.focus_entity,
        ATTR_DISPLAY_ENTITY: verdict.display_entity,
        ATTR_SOURCES: list(verdict.sources),
        ATTR_FAILED_CONDITIONS: list(verdict.failed_conditions),
        ATTR_ACTIVE_CONDITION: verdict.active_condition,
        ATTR_LOGIC: verdict.logic,
        ATTR_MESSAGE: verdict.message,
    }
    if verdict.category:
        attributes[ATTR_CATEGORY] = verdict.category
    if verdict.category_icon:
        attributes[ATTR_CATEGORY_ICON] = verdict.category_icon
    if verdict.category_color:
        attributes[ATTR_CATEGORY_COLOR] = verdict.category_color
    return attributes


def _group_attributes(verdict: GroupVerdict) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        ATTR_OBJECT_ID: verdict.object_id,
        ATTR_SEVERITY: verdict.severity,
        ATTR_REASON: verdict.reason,
        ATTR_FOCUS_ENTITY: verdict.focus_entity,
        ATTR_OPEN_COUNT: verdict.open_count,
        ATTR_OPEN_CHECKS: list(verdict.open_checks),
        ATTR_MEMBERS: list(verdict.members),
        ATTR_MESSAGE: verdict.message,
    }
    if verdict.category:
        attributes[ATTR_CATEGORY] = verdict.category
    if verdict.category_icon:
        attributes[ATTR_CATEGORY_ICON] = verdict.category_icon
    if verdict.category_color:
        attributes[ATTR_CATEGORY_COLOR] = verdict.category_color
    return attributes


class CheckBinarySensor(HassStatusEntity, BinarySensorEntity):
    """One check. On means it needs attention."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, runtime, check: CheckConfig) -> None:
        """Create the sensor for a check."""
        super().__init__(runtime, listener_key_check(check.object_id))
        self._object_id = check.object_id
        self._category_id = check.category_id
        self._attr_unique_id = unique_id_check(runtime.entry.entry_id, check.object_id)
        self._attr_name = check.name
        self._attr_icon = check.icon

    async def async_added_to_hass(self) -> None:
        """Subscribe, then attach the category label."""
        await super().async_added_to_hass()
        self._runtime.async_attach_category_label(self.entity_id, self._category_id)

    @property
    def is_on(self) -> bool | None:
        """Return True when the check is a problem."""
        verdict = self._runtime.verdicts.get(self._object_id)
        if verdict is None:
            return None
        return verdict.is_problem

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the verdict. Values of the source sensors are not copied."""
        verdict = self._runtime.verdicts.get(self._object_id)
        if verdict is None:
            return {}
        return _check_attributes(verdict)


class GroupBinarySensor(HassStatusEntity, BinarySensorEntity):
    """One group. On means a member check needs attention."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, runtime, group: GroupConfig) -> None:
        """Create the sensor for a group."""
        super().__init__(runtime, listener_key_group(group.object_id))
        self._object_id = group.object_id
        self._category_id = group.category_id
        self._attr_unique_id = unique_id_group(runtime.entry.entry_id, group.object_id)
        self._attr_name = group.name
        self._attr_icon = group.icon

    async def async_added_to_hass(self) -> None:
        """Subscribe, then attach the category label."""
        await super().async_added_to_hass()
        self._runtime.async_attach_category_label(self.entity_id, self._category_id)

    @property
    def is_on(self) -> bool | None:
        """Return True when any member check is a problem."""
        verdict = self._runtime.group_verdicts.get(self._object_id)
        if verdict is None:
            return None
        return verdict.is_problem

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return which member checks are open."""
        verdict = self._runtime.group_verdicts.get(self._object_id)
        if verdict is None:
            return {}
        return _group_attributes(verdict)
