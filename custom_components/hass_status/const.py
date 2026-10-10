"""Constants for the Hass Status integration."""

from __future__ import annotations

import re

DOMAIN = "hass_status"
VERSION = "0.1.1"

PLATFORMS: list[str] = ["binary_sensor", "sensor"]

SUBENTRY_CHECK = "check"
SUBENTRY_GROUP = "group"

LOGIC_ALL = "all"
LOGIC_ANY = "any"
LOGIC_FORMULA = "formula"
LOGICS = (LOGIC_ALL, LOGIC_ANY, LOGIC_FORMULA)

CONDITION_RANGE = "range"
CONDITION_STATE = "state"
CONDITION_TEMPLATE = "template"
CONDITION_TYPES = (CONDITION_RANGE, CONDITION_STATE, CONDITION_TEMPLATE)

SEVERITY_OK = "ok"
SEVERITY_ATTENTION = "attention"
SEVERITY_CRITICAL = "critical"
SEVERITIES = (SEVERITY_ATTENTION, SEVERITY_CRITICAL)
SEVERITY_RANK = {
    SEVERITY_OK: 0,
    SEVERITY_ATTENTION: 1,
    SEVERITY_CRITICAL: 2,
}

REASON_OK = "ok"
REASON_LOW = "low"
REASON_HIGH = "high"
REASON_STATE = "state"
REASON_UNAVAILABLE = "unavailable"
REASON_TEMPLATE = "template"
REASON_FORMULA = "formula"

COMPARATOR_IS = "is"
COMPARATOR_IS_NOT = "is_not"
COMPARATORS = (COMPARATOR_IS, COMPARATOR_IS_NOT)

CONF_NAME = "name"
CONF_OBJECT_ID = "object_id"
CONF_ICON = "icon"
CONF_CATEGORY = "category"
CONF_DISPLAY_ENTITY = "display_entity"
CONF_LOGIC = "logic"
CONF_FORMULA = "formula"
CONF_HOLD = "hold"
CONF_MESSAGE_OK = "message_ok"
CONF_CONDITIONS = "conditions"
CONF_MEMBERS = "members"

CONF_CONDITION_ID = "id"
CONF_TYPE = "type"
CONF_ENTITY_ID = "entity_id"
CONF_MINIMUM = "minimum"
CONF_MAXIMUM = "maximum"
CONF_HYSTERESIS = "hysteresis"
CONF_EXPECTED_STATE = "expected_state"
CONF_COMPARATOR = "comparator"
CONF_TEMPLATE = "template"
CONF_SEVERITY = "severity"
CONF_UNAVAILABLE_SEVERITY = "unavailable_severity"
CONF_MESSAGE_LOW = "message_low"
CONF_MESSAGE_HIGH = "message_high"
CONF_MESSAGE_FAIL = "message_fail"
CONF_MESSAGE_UNAVAILABLE = "message_unavailable"

DEFAULT_CHECK_ICON = "mdi:check-circle-outline"
DEFAULT_GROUP_ICON = "mdi:format-list-checks"
DEFAULT_BOARD_NAME = "Status"

MAX_CONDITIONS = 30
MAX_ID_LENGTH = 48
OBJECT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
FORMULA_KEYWORDS = frozenset({"and", "or", "not"})

UNAVAILABLE_STATES = frozenset({"unknown", "unavailable", ""})

ATTR_OBJECT_ID = "object_id"
ATTR_SEVERITY = "severity"
ATTR_CATEGORY = "category"
ATTR_REASON = "reason"
ATTR_FOCUS_ENTITY = "focus_entity"
ATTR_DISPLAY_ENTITY = "display_entity"
ATTR_SOURCES = "sources"
ATTR_FAILED_CONDITIONS = "failed_conditions"
ATTR_ACTIVE_CONDITION = "active_condition"
ATTR_LOGIC = "logic"
ATTR_MESSAGE = "message"
ATTR_OPEN_COUNT = "open_count"
ATTR_OPEN_CHECKS = "open_checks"
ATTR_MEMBERS = "members"
ATTR_CHECKS = "checks"
ATTR_BY_CATEGORY = "by_category"

LISTENER_SUMMARY = "summary"


def listener_key_check(object_id: str) -> str:
    """Return the in-memory listener key for a check."""
    return f"check:{object_id}"


def listener_key_group(object_id: str) -> str:
    """Return the in-memory listener key for a group."""
    return f"group:{object_id}"


def unique_id_check(entry_id: str, object_id: str) -> str:
    """Return the entity-registry id for a check."""
    return f"{entry_id}:check:{object_id}"


def unique_id_group(entry_id: str, object_id: str) -> str:
    """Return the entity-registry id for a group."""
    return f"{entry_id}:group:{object_id}"


def unique_id_summary(entry_id: str) -> str:
    """Return the entity-registry id for the open-count sensor."""
    return f"{entry_id}:summary"


def subentry_uid(kind: str, object_id: str) -> str:
    """Return a config-subentry id unique within one board."""
    return f"{kind}:{object_id}"


def slugify(value: str) -> str:
    """Turn a display name into a safe id."""
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:MAX_ID_LENGTH]
