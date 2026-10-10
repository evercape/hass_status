"""Stored shape of boards, checks, groups, and conditions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import (
    COMPARATOR_IS,
    COMPARATORS,
    CONDITION_RANGE,
    CONDITION_STATE,
    CONDITION_TEMPLATE,
    CONDITION_TYPES,
    CONF_CATEGORY,
    CONF_COMPARATOR,
    CONF_CONDITION_ID,
    CONF_CONDITIONS,
    CONF_DISPLAY_ENTITY,
    CONF_ENTITY_ID,
    CONF_EXPECTED_STATE,
    CONF_FORMULA,
    CONF_HOLD,
    CONF_HYSTERESIS,
    CONF_ICON,
    CONF_LOGIC,
    CONF_MAXIMUM,
    CONF_MEMBERS,
    CONF_MESSAGE_FAIL,
    CONF_MESSAGE_HIGH,
    CONF_MESSAGE_LOW,
    CONF_MESSAGE_OK,
    CONF_MESSAGE_UNAVAILABLE,
    CONF_MINIMUM,
    CONF_NAME,
    CONF_OBJECT_ID,
    CONF_SEVERITY,
    CONF_TEMPLATE,
    CONF_TYPE,
    CONF_UNAVAILABLE_SEVERITY,
    DEFAULT_CHECK_ICON,
    DEFAULT_GROUP_ICON,
    FORMULA_KEYWORDS,
    LOGIC_ALL,
    LOGIC_FORMULA,
    LOGICS,
    MAX_CONDITIONS,
    OBJECT_ID_RE,
    REASON_HIGH,
    REASON_LOW,
    REASON_OK,
    REASON_STATE,
    REASON_TEMPLATE,
    REASON_UNAVAILABLE,
    SEVERITY_ATTENTION,
    SEVERITY_CRITICAL,
    SEVERITIES,
)
from .formula import FormulaError, compile_formula

DEFAULT_MESSAGES = {
    REASON_OK: "OK",
    REASON_LOW: "Below the minimum",
    REASON_HIGH: "Above the maximum",
    REASON_STATE: "Unexpected state",
    REASON_UNAVAILABLE: "Not reporting",
    REASON_TEMPLATE: "Condition not met",
    "formula": "Formula not satisfied",
}


class ConfigError(Exception):
    """Configuration that cannot be turned into a check or group."""

    def __init__(self, code: str, field: str = "base", detail: str = "") -> None:
        self.code = code
        self.field = field
        self.detail = detail
        super().__init__(detail or code)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_float(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as err:
        raise ConfigError("invalid_number", field) from err


def duration_to_seconds(value: Any) -> int:
    """Convert a Home Assistant duration selector value to seconds."""
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if not isinstance(value, Mapping):
        raise ConfigError("invalid_hold", CONF_HOLD)
    try:
        total = (
            float(value.get("days", 0) or 0) * 86400
            + float(value.get("hours", 0) or 0) * 3600
            + float(value.get("minutes", 0) or 0) * 60
            + float(value.get("seconds", 0) or 0)
            + float(value.get("milliseconds", 0) or 0) / 1000
        )
    except (TypeError, ValueError) as err:
        raise ConfigError("invalid_hold", CONF_HOLD) from err
    return max(0, int(total))


def seconds_to_duration(seconds: int) -> dict[str, int]:
    """Convert seconds to the duration selector's dictionary form."""
    remaining = max(0, int(seconds))
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, secs = divmod(remaining, 60)
    data: dict[str, int] = {}
    if days:
        data["days"] = days
    if hours:
        data["hours"] = hours
    if minutes:
        data["minutes"] = minutes
    if secs or not data:
        data["seconds"] = secs
    return data


def _require_object_id(value: Any, field: str = CONF_OBJECT_ID) -> str:
    object_id = _text(value)
    if not OBJECT_ID_RE.match(object_id):
        raise ConfigError("invalid_object_id", field)
    if object_id in FORMULA_KEYWORDS:
        raise ConfigError("keyword_id", field)
    return object_id


@dataclass(frozen=True)
class ConditionConfig:
    """One test inside a check."""

    condition_id: str
    condition_type: str
    severity: str
    unavailable_severity: str
    entity_id: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    hysteresis: float = 0.0
    expected_state: str | None = None
    comparator: str = COMPARATOR_IS
    template: str | None = None
    message_ok: str = ""
    message_low: str = ""
    message_high: str = ""
    message_fail: str = ""
    message_unavailable: str = ""

    def message_for(self, reason: str) -> str:
        """Return the stored sentence for a reason, or the generic default."""
        custom = {
            REASON_OK: self.message_ok,
            REASON_LOW: self.message_low,
            REASON_HIGH: self.message_high,
            REASON_STATE: self.message_fail,
            REASON_TEMPLATE: self.message_fail,
            REASON_UNAVAILABLE: self.message_unavailable,
        }.get(reason, "")
        if custom:
            return custom
        return DEFAULT_MESSAGES.get(reason, DEFAULT_MESSAGES[REASON_UNAVAILABLE])

    def severity_for(self, reason: str) -> str:
        """Return how serious this reason is."""
        if reason == REASON_OK:
            return "ok"
        if reason == REASON_UNAVAILABLE:
            return self.unavailable_severity
        return self.severity

    def to_storage(self) -> dict[str, Any]:
        """Serialize this condition."""
        data: dict[str, Any] = {
            CONF_CONDITION_ID: self.condition_id,
            CONF_TYPE: self.condition_type,
            CONF_SEVERITY: self.severity,
            CONF_UNAVAILABLE_SEVERITY: self.unavailable_severity,
        }
        if self.entity_id:
            data[CONF_ENTITY_ID] = self.entity_id
        if self.minimum is not None:
            data[CONF_MINIMUM] = self.minimum
        if self.maximum is not None:
            data[CONF_MAXIMUM] = self.maximum
        if self.hysteresis:
            data[CONF_HYSTERESIS] = self.hysteresis
        if self.expected_state is not None:
            data[CONF_EXPECTED_STATE] = self.expected_state
        if self.comparator != COMPARATOR_IS:
            data[CONF_COMPARATOR] = self.comparator
        if self.template:
            data[CONF_TEMPLATE] = self.template
        for key, value in (
            (CONF_MESSAGE_OK, self.message_ok),
            (CONF_MESSAGE_LOW, self.message_low),
            (CONF_MESSAGE_HIGH, self.message_high),
            (CONF_MESSAGE_FAIL, self.message_fail),
            (CONF_MESSAGE_UNAVAILABLE, self.message_unavailable),
        ):
            if value:
                data[key] = value
        return data

    @classmethod
    def from_storage(cls, raw: Mapping[str, Any]) -> ConditionConfig:
        """Validate a stored condition."""
        condition_id = _require_object_id(raw.get(CONF_CONDITION_ID), CONF_CONDITION_ID)
        condition_type = _text(raw.get(CONF_TYPE))
        if condition_type not in CONDITION_TYPES:
            raise ConfigError("invalid_type", CONF_TYPE)
        severity = _text(raw.get(CONF_SEVERITY)) or SEVERITY_CRITICAL
        unavailable = _text(raw.get(CONF_UNAVAILABLE_SEVERITY)) or SEVERITY_ATTENTION
        if severity not in SEVERITIES:
            raise ConfigError("invalid_severity", CONF_SEVERITY)
        if unavailable not in SEVERITIES:
            raise ConfigError("invalid_severity", CONF_UNAVAILABLE_SEVERITY)
        entity_id = _text(raw.get(CONF_ENTITY_ID)) or None
        minimum = _optional_float(raw.get(CONF_MINIMUM), CONF_MINIMUM)
        maximum = _optional_float(raw.get(CONF_MAXIMUM), CONF_MAXIMUM)
        hysteresis = _optional_float(raw.get(CONF_HYSTERESIS), CONF_HYSTERESIS) or 0.0
        if hysteresis < 0:
            raise ConfigError("invalid_hysteresis", CONF_HYSTERESIS)
        expected_state = raw.get(CONF_EXPECTED_STATE)
        expected = None if expected_state is None else str(expected_state)
        comparator = _text(raw.get(CONF_COMPARATOR)) or COMPARATOR_IS
        if comparator not in COMPARATORS:
            raise ConfigError("invalid_comparator", CONF_COMPARATOR)
        template = _text(raw.get(CONF_TEMPLATE)) or None
        if condition_type == CONDITION_RANGE:
            if entity_id is None:
                raise ConfigError("entity_required", CONF_ENTITY_ID)
            if minimum is None and maximum is None:
                raise ConfigError("range_required", CONF_MINIMUM)
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ConfigError("min_above_max", CONF_MINIMUM)
        elif condition_type == CONDITION_STATE:
            if entity_id is None:
                raise ConfigError("entity_required", CONF_ENTITY_ID)
            if expected is None or expected == "":
                raise ConfigError("state_required", CONF_EXPECTED_STATE)
        elif condition_type == CONDITION_TEMPLATE and not template:
            raise ConfigError("template_required", CONF_TEMPLATE)
        return cls(
            condition_id=condition_id,
            condition_type=condition_type,
            severity=severity,
            unavailable_severity=unavailable,
            entity_id=entity_id,
            minimum=minimum,
            maximum=maximum,
            hysteresis=hysteresis,
            expected_state=expected,
            comparator=comparator,
            template=template,
            message_ok=_text(raw.get(CONF_MESSAGE_OK)),
            message_low=_text(raw.get(CONF_MESSAGE_LOW)),
            message_high=_text(raw.get(CONF_MESSAGE_HIGH)),
            message_fail=_text(raw.get(CONF_MESSAGE_FAIL)),
            message_unavailable=_text(raw.get(CONF_MESSAGE_UNAVAILABLE)),
        )


@dataclass(frozen=True)
class CheckConfig:
    """A row on the status board."""

    object_id: str
    name: str
    logic: str
    conditions: tuple[ConditionConfig, ...]
    icon: str = DEFAULT_CHECK_ICON
    category: str | None = None
    display_entity: str | None = None
    formula: str = ""
    hold_seconds: int = 0
    message_ok: str = ""

    def to_storage(self) -> dict[str, Any]:
        """Serialize this check."""
        data: dict[str, Any] = {
            CONF_OBJECT_ID: self.object_id,
            CONF_NAME: self.name,
            CONF_LOGIC: self.logic,
            CONF_ICON: self.icon,
            CONF_HOLD: self.hold_seconds,
            CONF_CONDITIONS: [condition.to_storage() for condition in self.conditions],
        }
        if self.category:
            data[CONF_CATEGORY] = self.category
        if self.display_entity:
            data[CONF_DISPLAY_ENTITY] = self.display_entity
        if self.formula:
            data[CONF_FORMULA] = self.formula
        if self.message_ok:
            data[CONF_MESSAGE_OK] = self.message_ok
        return data

    @classmethod
    def from_storage(cls, raw: Mapping[str, Any], object_id: str | None = None) -> CheckConfig:
        """Validate a stored check."""
        resolved_id = _require_object_id(object_id or raw.get(CONF_OBJECT_ID))
        name = _text(raw.get(CONF_NAME))
        if not name:
            raise ConfigError("name_required", CONF_NAME)
        logic = _text(raw.get(CONF_LOGIC)) or LOGIC_ALL
        if logic not in LOGICS:
            raise ConfigError("invalid_logic", CONF_LOGIC)
        raw_conditions = raw.get(CONF_CONDITIONS) or []
        if not isinstance(raw_conditions, list) or not raw_conditions:
            raise ConfigError("need_condition", CONF_CONDITIONS)
        if len(raw_conditions) > MAX_CONDITIONS:
            raise ConfigError("too_many_conditions", CONF_CONDITIONS)
        conditions: list[ConditionConfig] = []
        seen: set[str] = set()
        for item in raw_conditions:
            if not isinstance(item, Mapping):
                raise ConfigError("need_condition", CONF_CONDITIONS)
            condition = ConditionConfig.from_storage(item)
            if condition.condition_id in seen:
                raise ConfigError("duplicate_condition", CONF_CONDITION_ID)
            seen.add(condition.condition_id)
            conditions.append(condition)
        formula = _text(raw.get(CONF_FORMULA))
        if logic == LOGIC_FORMULA:
            if not formula:
                raise ConfigError("formula_required", CONF_FORMULA)
            try:
                _runner, names = compile_formula(formula)
            except FormulaError as err:
                raise ConfigError("invalid_formula", CONF_FORMULA) from err
            unknown = sorted(names - seen)
            if unknown:
                raise ConfigError(
                    "formula_unknown",
                    CONF_FORMULA,
                    detail=", ".join(unknown),
                )
        category = _text(raw.get(CONF_CATEGORY)) or None
        display_entity = _text(raw.get(CONF_DISPLAY_ENTITY)) or None
        icon = _text(raw.get(CONF_ICON)) or DEFAULT_CHECK_ICON
        if ":" not in icon:
            raise ConfigError("invalid_icon", CONF_ICON)
        hold_raw = raw.get(CONF_HOLD, 0)
        if isinstance(hold_raw, Mapping):
            hold_seconds = duration_to_seconds(hold_raw)
        else:
            hold_seconds = duration_to_seconds(hold_raw)
        return cls(
            object_id=resolved_id,
            name=name,
            logic=logic,
            conditions=tuple(conditions),
            icon=icon,
            category=category,
            display_entity=display_entity,
            formula=formula,
            hold_seconds=hold_seconds,
            message_ok=_text(raw.get(CONF_MESSAGE_OK)),
        )


@dataclass(frozen=True)
class GroupConfig:
    """A category row that follows its checks."""

    object_id: str
    name: str
    members: tuple[str, ...]
    icon: str = DEFAULT_GROUP_ICON
    category: str | None = None

    def to_storage(self) -> dict[str, Any]:
        """Serialize this group."""
        data: dict[str, Any] = {
            CONF_OBJECT_ID: self.object_id,
            CONF_NAME: self.name,
            CONF_ICON: self.icon,
            CONF_MEMBERS: list(self.members),
        }
        if self.category:
            data[CONF_CATEGORY] = self.category
        return data

    @classmethod
    def from_storage(cls, raw: Mapping[str, Any], object_id: str | None = None) -> GroupConfig:
        """Validate a stored group."""
        resolved_id = _require_object_id(object_id or raw.get(CONF_OBJECT_ID))
        name = _text(raw.get(CONF_NAME))
        if not name:
            raise ConfigError("name_required", CONF_NAME)
        raw_members = raw.get(CONF_MEMBERS) or []
        if not isinstance(raw_members, list) or not raw_members:
            raise ConfigError("members_required", CONF_MEMBERS)
        members: list[str] = []
        for member in raw_members:
            member_id = _require_object_id(member, CONF_MEMBERS)
            if member_id not in members:
                members.append(member_id)
        icon = _text(raw.get(CONF_ICON)) or DEFAULT_GROUP_ICON
        if ":" not in icon:
            raise ConfigError("invalid_icon", CONF_ICON)
        category = _text(raw.get(CONF_CATEGORY)) or None
        return cls(
            object_id=resolved_id,
            name=name,
            members=tuple(members),
            icon=icon,
            category=category,
        )
