"""Config flow for Hass Status boards, checks, and groups."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigSubentryFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    IconSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.helpers.template import Template
import voluptuous as vol

from .const import (
    CATEGORY_COLORS,
    COMPARATOR_IS,
    COMPARATORS,
    CONDITION_RANGE,
    CONDITION_STATE,
    CONDITION_TEMPLATE,
    CONDITION_TYPES,
    CONF_CATEGORY,
    CONF_COLOR,
    CONF_COMPARATOR,
    CONF_CONDITION_ID,
    CONF_CONDITIONS,
    CONF_DISPLAY_ENTITY,
    CONF_ENTITY_ID,
    CONF_EXPECTED_STATE,
    CONF_FORMULA,
    CONF_HOLD,
    CONF_HOLD_MINUTES,
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
    DEFAULT_BOARD_NAME,
    DEFAULT_CATEGORY_COLOR,
    DEFAULT_CATEGORY_ICON,
    DOMAIN,
    NEW_CATEGORY,
    NO_CATEGORY,
    FORMULA_KEYWORDS,
    LOGIC_ALL,
    LOGICS,
    OBJECT_ID_RE,
    SEVERITY_ATTENTION,
    SEVERITY_CRITICAL,
    SEVERITIES,
    SUBENTRY_CATEGORY,
    SUBENTRY_CHECK,
    SUBENTRY_GROUP,
    slugify,
    subentry_uid,
)
from .models import (
    CategoryConfig,
    CheckConfig,
    ConditionConfig,
    ConfigError,
    GroupConfig,
    duration_to_seconds,
)

_LOGGER = logging.getLogger(__name__)

_ACTION = "action"
_REMOVE = "remove"


def _select(
    options: list[str] | list[dict[str, str]],
    *,
    translation_key: str | None = None,
    multiple: bool = False,
) -> SelectSelector:
    # Omit translation_key when it is empty. Home Assistant rejects null,
    # and that abort is what the form shows as "Unknown error occurred".
    config: dict[str, Any] = {
        "options": options,
        "multiple": multiple,
        "mode": SelectSelectorMode.DROPDOWN,
    }
    if translation_key:
        config["translation_key"] = translation_key
    return SelectSelector(SelectSelectorConfig(**config))


def _number() -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any"))


def _with_suggestion(key: str, selector: Any, suggested: Any, *, required: bool) -> tuple:
    marker = vol.Required if required else vol.Optional
    if suggested is None or suggested == "":
        return (marker(key), selector)
    return (marker(key, description={"suggested_value": suggested}), selector)


class HassStatusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a status board. Checks and groups are subentries."""

    VERSION = 1
    MINOR_VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Checks and groups can be added from the board."""
        return {
            SUBENTRY_CHECK: CheckSubentryFlow,
            SUBENTRY_GROUP: GroupSubentryFlow,
            SUBENTRY_CATEGORY: CategorySubentryFlow,
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Name the board."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            unique_id = slugify(name)
            if not unique_id:
                errors[CONF_NAME] = "invalid_name"
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=name, data={CONF_NAME: name})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, description={"suggested_value": DEFAULT_BOARD_NAME}
                    ): TextSelector(),
                }
            ),
            errors=errors,
        )


class CheckSubentryFlow(ConfigSubentryFlow):
    """Add or edit one check and its conditions."""

    def _ensure(self) -> None:
        if getattr(self, "_ready", False):
            return
        self._ready = True
        self._basics: dict[str, Any] = {}
        self._conditions: list[dict[str, Any]] = []
        self._editing: int | None = None
        self._partial: dict[str, Any] = {}
        self._object_id = ""
        self._pending_category: CategoryConfig | None = None
        self._wants_new_category = False
        self._category_choice = ""
        self._flow_error = ""
        if self.source != SOURCE_USER:
            subentry = self._get_reconfigure_subentry()
            self._basics = dict(subentry.data)
            raw_conditions = self._basics.get(CONF_CONDITIONS, [])
            self._conditions = [dict(item) for item in raw_conditions]
            self._object_id = _object_id_from_unique(subentry.unique_id)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start a new check."""
        self._ensure()
        return await self._async_basics(user_input, "user")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit an existing check. The id stays the same."""
        self._ensure()
        return await self._async_basics(user_input, "reconfigure")

    async def _async_basics(
        self, user_input: dict[str, Any] | None, step_id: str
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._apply_basics(user_input)
            if not errors:
                try:
                    if self._wants_new_category:
                        return await self.async_step_new_category()
                    return await self.async_step_conditions()
                except Exception as err:
                    _LOGGER.exception("Could not open the next check step")
                    errors["base"] = "flow_failed"
                    self._flow_error = str(err)
        return self.async_show_form(
            step_id=step_id,
            data_schema=self._basics_schema(),
            errors=errors,
            last_step=False,
            description_placeholders={
                "object_id": self._object_id or "-",
                "error": self._flow_error,
            },
        )

    def _apply_basics(self, user_input: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        name = str(user_input.get(CONF_NAME, "")).strip()
        icon = str(user_input.get(CONF_ICON, "")).strip()
        logic = user_input.get(CONF_LOGIC, LOGIC_ALL)
        formula = str(user_input.get(CONF_FORMULA, "")).strip()
        category_choice = str(user_input.get(CONF_CATEGORY) or NO_CATEGORY)
        self._basics = {
            CONF_NAME: name,
            CONF_OBJECT_ID: str(user_input.get(CONF_OBJECT_ID, "")).strip(),
            CONF_ICON: icon,
            CONF_CATEGORY: category_choice,
            CONF_DISPLAY_ENTITY: user_input.get(CONF_DISPLAY_ENTITY) or "",
            CONF_LOGIC: logic,
            CONF_FORMULA: formula,
            CONF_HOLD_MINUTES: user_input.get(CONF_HOLD_MINUTES),
            CONF_MESSAGE_OK: str(user_input.get(CONF_MESSAGE_OK, "")).strip(),
        }
        if not name:
            errors[CONF_NAME] = "name_required"
        if self.source == SOURCE_USER:
            raw_id = self._basics[CONF_OBJECT_ID] or slugify(name)
            if not OBJECT_ID_RE.match(raw_id):
                errors[CONF_OBJECT_ID] = "invalid_object_id"
            elif raw_id in FORMULA_KEYWORDS:
                errors[CONF_OBJECT_ID] = "keyword_id"
            elif self._id_taken(subentry_uid(SUBENTRY_CHECK, raw_id)):
                errors[CONF_OBJECT_ID] = "already_configured"
            else:
                self._object_id = raw_id
                self._basics[CONF_OBJECT_ID] = raw_id
        if icon and ":" not in icon:
            errors[CONF_ICON] = "invalid_icon"
        if logic not in LOGICS:
            errors[CONF_LOGIC] = "invalid_logic"
        self._category_choice = category_choice
        if category_choice == NEW_CATEGORY:
            self._wants_new_category = True
            self._basics[CONF_CATEGORY] = ""
        elif category_choice == NO_CATEGORY:
            self._wants_new_category = False
            self._pending_category = None
            self._basics[CONF_CATEGORY] = ""
        else:
            self._wants_new_category = False
            self._pending_category = None
            self._basics[CONF_CATEGORY] = category_choice
        try:
            minutes = _whole_minutes(user_input.get(CONF_HOLD_MINUTES))
        except ConfigError:
            errors[CONF_HOLD_MINUTES] = "invalid_hold"
            minutes = 0
        if errors:
            return errors
        self._basics[CONF_HOLD] = minutes * 60
        self._basics[CONF_HOLD_MINUTES] = minutes
        return {}

    def _basics_schema(self) -> vol.Schema:
        basics = self._basics
        suggested_hold = _suggested_minutes(basics)
        fields = [
            _with_suggestion(CONF_NAME, TextSelector(), basics.get(CONF_NAME), required=True),
        ]
        if self.source == SOURCE_USER:
            fields.append(
                _with_suggestion(
                    CONF_OBJECT_ID,
                    TextSelector(),
                    basics.get(CONF_OBJECT_ID),
                    required=False,
                )
            )
        fields.extend(
            [
                _with_suggestion(CONF_ICON, IconSelector(), basics.get(CONF_ICON), required=False),
                _with_suggestion(
                    CONF_CATEGORY,
                    _select(
                        _category_select_options(
                            self._get_entry(),
                            str(basics.get(CONF_CATEGORY) or ""),
                        )
                    ),
                    self._suggested_category(),
                    required=True,
                ),
                _with_suggestion(
                    CONF_DISPLAY_ENTITY,
                    EntitySelector(),
                    basics.get(CONF_DISPLAY_ENTITY),
                    required=False,
                ),
                _with_suggestion(
                    CONF_LOGIC,
                    _select(list(LOGICS), translation_key="logic"),
                    basics.get(CONF_LOGIC, LOGIC_ALL),
                    required=True,
                ),
                _with_suggestion(
                    CONF_FORMULA,
                    TextSelector(TextSelectorConfig(multiline=True)),
                    basics.get(CONF_FORMULA),
                    required=False,
                ),
                _with_suggestion(
                    CONF_HOLD_MINUTES,
                    _minutes_selector(),
                    suggested_hold,
                    required=False,
                ),
                _with_suggestion(
                    CONF_MESSAGE_OK,
                    TextSelector(TextSelectorConfig(multiline=True)),
                    basics.get(CONF_MESSAGE_OK),
                    required=False,
                ),
            ]
        )
        return vol.Schema(dict(fields))

    async def async_step_conditions(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose the next condition to edit, or save the check."""
        self._ensure()
        errors: dict[str, str] = {}
        placeholders = {"detail": ""}
        if user_input is not None:
            action = str(user_input[_ACTION])
            if action == "add":
                self._editing = None
                self._partial = {}
                return await self.async_step_condition()
            if action == "save":
                return await self._async_finish()
            if action.startswith("edit:"):
                index = int(action.split(":", 1)[1])
                if 0 <= index < len(self._conditions):
                    self._editing = index
                    self._partial = dict(self._conditions[index])
                    return await self.async_step_condition()
        if not self._conditions:
            placeholders["detail"] = "Add at least one condition before saving."
        return self.async_show_form(
            step_id="conditions",
            data_schema=vol.Schema(
                {vol.Required(_ACTION): _select(self._action_options())}
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _action_options(self) -> list[dict[str, str]]:
        options = [{"value": "add", "label": "Add condition"}]
        for index, condition in enumerate(self._conditions):
            label = f"{condition.get(CONF_CONDITION_ID, index)} ({condition.get(CONF_TYPE, '')})"
            options.append({"value": f"edit:{index}", "label": f"Edit {label}"})
        options.append({"value": "save", "label": "Save check"})
        return options

    async def async_step_condition(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Condition id, type, and the entity it watches."""
        self._ensure()
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(_REMOVE) and self._editing is not None:
                self._conditions.pop(self._editing)
                self._editing = None
                self._partial = {}
                return await self.async_step_conditions()
            errors = self._apply_condition_identity(user_input)
            if not errors:
                return await self.async_step_condition_detail()
        return self.async_show_form(
            step_id="condition",
            data_schema=self._condition_schema(),
            errors=errors,
            last_step=False,
        )

    def _apply_condition_identity(self, user_input: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        condition_id = str(user_input.get(CONF_CONDITION_ID, "")).strip()
        condition_type = user_input.get(CONF_TYPE)
        entity_id = user_input.get(CONF_ENTITY_ID) or ""
        severity = user_input.get(CONF_SEVERITY, SEVERITY_CRITICAL)
        unavailable = user_input.get(CONF_UNAVAILABLE_SEVERITY, SEVERITY_ATTENTION)
        self._partial = {
            **self._partial,
            CONF_CONDITION_ID: condition_id,
            CONF_TYPE: condition_type,
            CONF_SEVERITY: severity,
            CONF_UNAVAILABLE_SEVERITY: unavailable,
        }
        if entity_id:
            self._partial[CONF_ENTITY_ID] = entity_id
        if not OBJECT_ID_RE.match(condition_id):
            errors[CONF_CONDITION_ID] = "invalid_object_id"
        elif condition_id in FORMULA_KEYWORDS:
            errors[CONF_CONDITION_ID] = "keyword_id"
        else:
            for index, condition in enumerate(self._conditions):
                if index == self._editing:
                    continue
                if condition.get(CONF_CONDITION_ID) == condition_id:
                    errors[CONF_CONDITION_ID] = "duplicate_condition"
                    break
        if condition_type not in CONDITION_TYPES:
            errors[CONF_TYPE] = "invalid_type"
        if condition_type in (CONDITION_RANGE, CONDITION_STATE) and not entity_id:
            errors[CONF_ENTITY_ID] = "entity_required"
        if severity not in SEVERITIES:
            errors[CONF_SEVERITY] = "invalid_severity"
        if unavailable not in SEVERITIES:
            errors[CONF_UNAVAILABLE_SEVERITY] = "invalid_severity"
        if errors:
            return errors
        self._partial = {
            CONF_CONDITION_ID: condition_id,
            CONF_TYPE: condition_type,
            CONF_SEVERITY: severity,
            CONF_UNAVAILABLE_SEVERITY: unavailable,
        }
        if entity_id and condition_type != CONDITION_TEMPLATE:
            self._partial[CONF_ENTITY_ID] = entity_id
        return {}

    def _condition_schema(self) -> vol.Schema:
        current = self._partial
        fields = [
            _with_suggestion(
                CONF_CONDITION_ID,
                TextSelector(),
                current.get(CONF_CONDITION_ID),
                required=True,
            ),
            _with_suggestion(
                CONF_TYPE,
                _select(list(CONDITION_TYPES), translation_key="condition_type"),
                current.get(CONF_TYPE, CONDITION_RANGE),
                required=True,
            ),
            _with_suggestion(
                CONF_ENTITY_ID,
                EntitySelector(),
                current.get(CONF_ENTITY_ID),
                required=False,
            ),
            _with_suggestion(
                CONF_SEVERITY,
                _select(list(SEVERITIES), translation_key="severity"),
                current.get(CONF_SEVERITY, SEVERITY_CRITICAL),
                required=True,
            ),
            _with_suggestion(
                CONF_UNAVAILABLE_SEVERITY,
                _select(list(SEVERITIES), translation_key="severity"),
                current.get(CONF_UNAVAILABLE_SEVERITY, SEVERITY_ATTENTION),
                required=True,
            ),
        ]
        if self._editing is not None:
            fields.append((vol.Optional(_REMOVE, default=False), BooleanSelector()))
        return vol.Schema(dict(fields))

    async def async_step_condition_detail(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Limits, expected state, or template, plus the sentences."""
        self._ensure()
        errors: dict[str, str] = {}
        placeholders = {"detail": ""}
        if user_input is not None:
            stored, errors = self._apply_condition_detail(user_input)
            if not errors and stored is not None:
                if self._editing is None:
                    self._conditions.append(stored)
                else:
                    self._conditions[self._editing] = stored
                self._editing = None
                self._partial = {}
                return await self.async_step_conditions()
            if errors.get("base") == "template_invalid":
                placeholders["detail"] = "Template could not be parsed."
        return self.async_show_form(
            step_id="condition_detail",
            data_schema=self._detail_schema(),
            errors=errors,
            description_placeholders=placeholders,
            last_step=False,
        )

    def _apply_condition_detail(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        condition_type = self._partial[CONF_TYPE]
        stored = dict(self._partial)
        if condition_type == CONDITION_RANGE:
            if user_input.get(CONF_MINIMUM) is not None:
                stored[CONF_MINIMUM] = user_input[CONF_MINIMUM]
            if user_input.get(CONF_MAXIMUM) is not None:
                stored[CONF_MAXIMUM] = user_input[CONF_MAXIMUM]
            if user_input.get(CONF_HYSTERESIS) is not None:
                stored[CONF_HYSTERESIS] = user_input[CONF_HYSTERESIS]
            _copy_text(stored, user_input, CONF_MESSAGE_LOW)
            _copy_text(stored, user_input, CONF_MESSAGE_HIGH)
        elif condition_type == CONDITION_STATE:
            stored[CONF_COMPARATOR] = user_input.get(CONF_COMPARATOR, COMPARATOR_IS)
            expected = user_input.get(CONF_EXPECTED_STATE)
            if expected is not None:
                stored[CONF_EXPECTED_STATE] = str(expected)
            _copy_text(stored, user_input, CONF_MESSAGE_FAIL)
        else:
            template = str(user_input.get(CONF_TEMPLATE, "")).strip()
            if template:
                try:
                    Template(template, self.hass).ensure_valid()
                except TemplateError:
                    return None, {CONF_TEMPLATE: "template_invalid"}
                stored[CONF_TEMPLATE] = template
            _copy_text(stored, user_input, CONF_MESSAGE_FAIL)
        _copy_text(stored, user_input, CONF_MESSAGE_OK)
        _copy_text(stored, user_input, CONF_MESSAGE_UNAVAILABLE)
        try:
            condition = ConditionConfig.from_storage(stored)
        except ConfigError as err:
            return None, {err.field: err.code}
        return condition.to_storage(), {}

    def _detail_schema(self) -> vol.Schema:
        condition_type = self._partial.get(CONF_TYPE, CONDITION_RANGE)
        current = self._partial
        if self._editing is not None:
            current = {**self._conditions[self._editing], **self._partial}
        fields: list[tuple] = []
        if condition_type == CONDITION_RANGE:
            fields.extend(
                [
                    _with_suggestion(
                        CONF_MINIMUM, _number(), current.get(CONF_MINIMUM), required=False
                    ),
                    _with_suggestion(
                        CONF_MAXIMUM, _number(), current.get(CONF_MAXIMUM), required=False
                    ),
                    _with_suggestion(
                        CONF_HYSTERESIS,
                        _number(),
                        current.get(CONF_HYSTERESIS, 0),
                        required=False,
                    ),
                    _with_suggestion(
                        CONF_MESSAGE_LOW,
                        TextSelector(TextSelectorConfig(multiline=True)),
                        current.get(CONF_MESSAGE_LOW),
                        required=False,
                    ),
                    _with_suggestion(
                        CONF_MESSAGE_HIGH,
                        TextSelector(TextSelectorConfig(multiline=True)),
                        current.get(CONF_MESSAGE_HIGH),
                        required=False,
                    ),
                ]
            )
        elif condition_type == CONDITION_STATE:
            fields.extend(
                [
                    _with_suggestion(
                        CONF_COMPARATOR,
                        _select(list(COMPARATORS), translation_key="comparator"),
                        current.get(CONF_COMPARATOR, COMPARATOR_IS),
                        required=True,
                    ),
                    _with_suggestion(
                        CONF_EXPECTED_STATE,
                        TextSelector(),
                        current.get(CONF_EXPECTED_STATE),
                        required=True,
                    ),
                    _with_suggestion(
                        CONF_MESSAGE_FAIL,
                        TextSelector(TextSelectorConfig(multiline=True)),
                        current.get(CONF_MESSAGE_FAIL),
                        required=False,
                    ),
                ]
            )
        else:
            fields.extend(
                [
                    _with_suggestion(
                        CONF_TEMPLATE,
                        TemplateSelector(),
                        current.get(CONF_TEMPLATE),
                        required=True,
                    ),
                    _with_suggestion(
                        CONF_MESSAGE_FAIL,
                        TextSelector(TextSelectorConfig(multiline=True)),
                        current.get(CONF_MESSAGE_FAIL),
                        required=False,
                    ),
                ]
            )
        fields.extend(
            [
                _with_suggestion(
                    CONF_MESSAGE_OK,
                    TextSelector(TextSelectorConfig(multiline=True)),
                    current.get(CONF_MESSAGE_OK),
                    required=False,
                ),
                _with_suggestion(
                    CONF_MESSAGE_UNAVAILABLE,
                    TextSelector(TextSelectorConfig(multiline=True)),
                    current.get(CONF_MESSAGE_UNAVAILABLE),
                    required=False,
                ),
            ]
        )
        return vol.Schema(dict(fields))

    def _suggested_category(self) -> str:
        if self._wants_new_category:
            return NEW_CATEGORY
        if self._category_choice == NO_CATEGORY:
            return NO_CATEGORY
        current = str(self._basics.get(CONF_CATEGORY) or "")
        if current:
            return current
        if self.source == SOURCE_USER and not _read_categories(self._get_entry()):
            return NEW_CATEGORY
        return NO_CATEGORY

    async def async_step_new_category(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Name, icon, and color for a category created while adding a check."""
        self._ensure()
        errors: dict[str, str] = {}
        current = self._pending_category.to_storage() if self._pending_category else {}
        if user_input is not None:
            category, errors = _category_from_input(
                user_input,
                entry=self._get_entry(),
                object_id=None,
            )
            if category is not None:
                self._pending_category = category
                self._wants_new_category = False
                self._basics[CONF_CATEGORY] = category.object_id
                return await self.async_step_conditions()
            current = user_input
        return self.async_show_form(
            step_id="new_category",
            data_schema=_category_schema(current, include_id=True),
            errors=errors,
            last_step=False,
            description_placeholders={"object_id": "-"},
        )

    async def _async_finish(self) -> FlowResult:
        payload = {
            CONF_OBJECT_ID: self._object_id,
            CONF_NAME: self._basics.get(CONF_NAME, ""),
            CONF_ICON: self._basics.get(CONF_ICON, ""),
            CONF_CATEGORY: self._basics.get(CONF_CATEGORY, ""),
            CONF_DISPLAY_ENTITY: self._basics.get(CONF_DISPLAY_ENTITY, ""),
            CONF_LOGIC: self._basics.get(CONF_LOGIC, LOGIC_ALL),
            CONF_FORMULA: self._basics.get(CONF_FORMULA, ""),
            CONF_HOLD: self._basics.get(CONF_HOLD, 0),
            CONF_MESSAGE_OK: self._basics.get(CONF_MESSAGE_OK, ""),
            CONF_CONDITIONS: self._conditions,
        }
        try:
            check = CheckConfig.from_storage(payload, self._object_id)
        except ConfigError as err:
            return self.async_show_form(
                step_id="conditions",
                data_schema=vol.Schema(
                    {vol.Required(_ACTION): _select(self._action_options())}
                ),
                errors={"base": err.code},
                description_placeholders={"detail": err.detail or err.code},
            )
        data = check.to_storage()
        if self._pending_category is not None:
            try:
                _add_category_subentry(self.hass, self._get_entry(), self._pending_category)
            except Exception:
                _LOGGER.exception("Could not save the new category")
                return self.async_show_form(
                    step_id="conditions",
                    data_schema=vol.Schema(
                        {vol.Required(_ACTION): _select(self._action_options())}
                    ),
                    errors={"base": "already_configured"},
                    description_placeholders={"detail": ""},
                )
            self._pending_category = None
        if self.source == SOURCE_USER:
            return self.async_create_entry(
                title=check.name,
                data=data,
                unique_id=subentry_uid(SUBENTRY_CHECK, check.object_id),
            )
        return self.async_update_and_abort(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            title=check.name,
            data=data,
        )

    def _id_taken(self, unique_id: str) -> bool:
        return any(
            subentry.unique_id == unique_id
            for subentry in self._get_entry().subentries.values()
        )


class GroupSubentryFlow(ConfigSubentryFlow):
    """Add or edit a group of checks."""

    def _ensure(self) -> None:
        if getattr(self, "_ready", False):
            return
        self._ready = True
        self._initial: dict[str, Any] = {}
        self._object_id = ""
        self._pending_category: CategoryConfig | None = None
        self._draft: dict[str, Any] | None = None
        if self.source != SOURCE_USER:
            subentry = self._get_reconfigure_subentry()
            self._initial = dict(subentry.data)
            self._object_id = _object_id_from_unique(subentry.unique_id)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start a new group."""
        self._ensure()
        return await self._async_form(user_input, "user")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit a group. The id stays the same."""
        self._ensure()
        return await self._async_form(user_input, "reconfigure")

    async def _async_form(
        self, user_input: dict[str, Any] | None, step_id: str
    ) -> FlowResult:
        options = self._member_options()
        if not options:
            return self.async_abort(reason="no_checks")
        errors: dict[str, str] = {}
        if user_input is not None:
            return await self._async_save(user_input, options, step_id)
        return self.async_show_form(
            step_id=step_id,
            data_schema=self._schema(options),
            errors=errors,
            description_placeholders={"object_id": self._object_id or "-"},
        )

    async def _async_save(
        self,
        user_input: dict[str, Any],
        options: list[dict[str, str]],
        step_id: str,
    ) -> FlowResult:
        errors: dict[str, str] = {}
        name = str(user_input.get(CONF_NAME, "")).strip()
        if not name:
            errors[CONF_NAME] = "name_required"
        if self.source == SOURCE_USER:
            raw_id = str(user_input.get(CONF_OBJECT_ID, "")).strip() or slugify(name)
            if not OBJECT_ID_RE.match(raw_id):
                errors[CONF_OBJECT_ID] = "invalid_object_id"
            elif raw_id in FORMULA_KEYWORDS:
                errors[CONF_OBJECT_ID] = "keyword_id"
            elif any(
                subentry.unique_id == subentry_uid(SUBENTRY_GROUP, raw_id)
                for subentry in self._get_entry().subentries.values()
            ):
                errors[CONF_OBJECT_ID] = "already_configured"
            else:
                self._object_id = raw_id
        if errors:
            self._initial = {**self._initial, **user_input}
            return self.async_show_form(
                step_id=step_id,
                data_schema=self._schema(options),
                errors=errors,
                description_placeholders={"object_id": self._object_id or "-"},
            )
        raw_members = user_input.get(CONF_MEMBERS) or []
        if isinstance(raw_members, str):
            raw_members = [raw_members]
        category_choice = str(user_input.get(CONF_CATEGORY) or NO_CATEGORY)
        if category_choice in (NEW_CATEGORY, NO_CATEGORY):
            category_id = ""
        else:
            category_id = category_choice
        payload = {
            CONF_OBJECT_ID: self._object_id,
            CONF_NAME: name,
            CONF_ICON: str(user_input.get(CONF_ICON, "")).strip(),
            CONF_CATEGORY: category_id,
            CONF_MEMBERS: list(raw_members),
        }
        try:
            GroupConfig.from_storage(payload, self._object_id)
        except ConfigError as err:
            self._initial = {**self._initial, **user_input}
            return self.async_show_form(
                step_id=step_id,
                data_schema=self._schema(options),
                errors={err.field: err.code},
                description_placeholders={"object_id": self._object_id or "-"},
            )
        if category_choice == NEW_CATEGORY:
            self._draft = payload
            self._initial = {**self._initial, **user_input}
            return await self.async_step_new_category()
        return await self._async_store_group(payload)

    async def async_step_new_category(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Name, icon, and color for a category created while adding a group."""
        self._ensure()
        errors: dict[str, str] = {}
        current: dict[str, Any] = {}
        if user_input is not None:
            category, errors = _category_from_input(
                user_input,
                entry=self._get_entry(),
                object_id=None,
            )
            if category is not None and self._draft is not None:
                self._pending_category = category
                draft = dict(self._draft)
                draft[CONF_CATEGORY] = category.object_id
                return await self._async_store_group(draft)
            current = user_input
        return self.async_show_form(
            step_id="new_category",
            data_schema=_category_schema(current, include_id=True),
            errors=errors,
            last_step=False,
            description_placeholders={"object_id": "-"},
        )

    async def _async_store_group(self, payload: dict[str, Any]) -> FlowResult:
        group = GroupConfig.from_storage(payload, self._object_id)
        data = group.to_storage()
        if self._pending_category is not None:
            try:
                _add_category_subentry(self.hass, self._get_entry(), self._pending_category)
            except Exception:
                _LOGGER.exception("Could not save the new category")
                current = (
                    self._pending_category.to_storage() if self._pending_category else {}
                )
                return self.async_show_form(
                    step_id="new_category",
                    data_schema=_category_schema(current, include_id=True),
                    errors={"base": "already_configured"},
                    description_placeholders={"object_id": "-"},
                )
            self._pending_category = None
        if self.source == SOURCE_USER:
            return self.async_create_entry(
                title=group.name,
                data=data,
                unique_id=subentry_uid(SUBENTRY_GROUP, group.object_id),
            )
        return self.async_update_and_abort(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            title=group.name,
            data=data,
        )

    def _schema(self, options: list[dict[str, str]]) -> vol.Schema:
        initial = self._initial
        fields = [
            _with_suggestion(CONF_NAME, TextSelector(), initial.get(CONF_NAME), required=True),
        ]
        if self.source == SOURCE_USER:
            fields.append(
                _with_suggestion(
                    CONF_OBJECT_ID,
                    TextSelector(),
                    initial.get(CONF_OBJECT_ID),
                    required=False,
                )
            )
        fields.extend(
            [
                _with_suggestion(CONF_ICON, IconSelector(), initial.get(CONF_ICON), required=False),
                _with_suggestion(
                    CONF_CATEGORY,
                    _select(
                        _category_select_options(
                            self._get_entry(),
                            str(initial.get(CONF_CATEGORY) or ""),
                        )
                    ),
                    initial.get(CONF_CATEGORY) or NO_CATEGORY,
                    required=True,
                ),
                _with_suggestion(
                    CONF_MEMBERS,
                    _select(options, multiple=True),
                    initial.get(CONF_MEMBERS),
                    required=True,
                ),
            ]
        )
        return vol.Schema(dict(fields))

    def _member_options(self) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        known: set[str] = set()
        for subentry in self._get_entry().subentries.values():
            if subentry.subentry_type != SUBENTRY_CHECK or not subentry.unique_id:
                continue
            object_id = _object_id_from_unique(subentry.unique_id)
            if not object_id or object_id in known:
                continue
            known.add(object_id)
            options.append({"value": object_id, "label": f"{subentry.title} ({object_id})"})
        for member in self._initial.get(CONF_MEMBERS, []):
            if member not in known:
                options.append({"value": member, "label": f"{member} (missing)"})
        options.sort(key=lambda item: item["label"].lower())
        return options


def _copy_text(target: dict[str, Any], source: dict[str, Any], key: str) -> None:
    value = str(source.get(key, "")).strip()
    if value:
        target[key] = value


def _object_id_from_unique(unique_id: str | None) -> str:
    if not unique_id or ":" not in unique_id:
        return unique_id or ""
    return unique_id.split(":", 1)[1]


def _whole_minutes(value: Any) -> int:
    """Return a whole number of minutes, or raise ConfigError."""
    if value is None or value == "":
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise ConfigError("invalid_hold", CONF_HOLD_MINUTES) from err
    if number < 0 or number > 10080 or number != int(number):
        raise ConfigError("invalid_hold", CONF_HOLD_MINUTES)
    return int(number)


def _suggested_minutes(basics: dict[str, Any]) -> int | None:
    """Minutes to show in the form. Stored holds are seconds."""
    if CONF_HOLD_MINUTES in basics and basics[CONF_HOLD_MINUTES] not in (None, ""):
        try:
            return _whole_minutes(basics[CONF_HOLD_MINUTES])
        except ConfigError:
            return None
    raw = basics.get(CONF_HOLD, 0)
    if isinstance(raw, dict):
        try:
            raw = duration_to_seconds(raw)
        except ConfigError:
            return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and raw:
        minutes = int(raw) // 60
        return minutes or None
    return None


def _minutes_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=0,
            max=10080,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="min",
        )
    )


def _read_categories(entry: Any) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_CATEGORY or not subentry.unique_id:
            continue
        object_id = _object_id_from_unique(subentry.unique_id)
        if object_id:
            found.append((object_id, subentry.title))
    found.sort(key=lambda item: item[1].lower())
    return found


def _category_select_options(entry: Any, current: str = "") -> list[dict[str, str]]:
    options = [{"value": NO_CATEGORY, "label": "No category"}]
    known = {NO_CATEGORY}
    for object_id, title in _read_categories(entry):
        options.append({"value": object_id, "label": title})
        known.add(object_id)
    if current and current not in known and current != NEW_CATEGORY:
        options.append({"value": current, "label": current})
    options.append({"value": NEW_CATEGORY, "label": "New category"})
    return options


def _category_schema(current: dict[str, Any], *, include_id: bool) -> vol.Schema:
    fields = [
        _with_suggestion(CONF_NAME, TextSelector(), current.get(CONF_NAME), required=True),
    ]
    if include_id:
        fields.append(
            _with_suggestion(
                CONF_OBJECT_ID,
                TextSelector(),
                current.get(CONF_OBJECT_ID),
                required=False,
            )
        )
    fields.extend(
        [
            _with_suggestion(
                CONF_ICON,
                IconSelector(),
                current.get(CONF_ICON) or DEFAULT_CATEGORY_ICON,
                required=True,
            ),
            _with_suggestion(
                CONF_COLOR,
                _select(list(CATEGORY_COLORS), translation_key="category_color"),
                current.get(CONF_COLOR) or DEFAULT_CATEGORY_COLOR,
                required=True,
            ),
        ]
    )
    return vol.Schema(dict(fields))


def _category_from_input(
    user_input: dict[str, Any],
    *,
    entry: Any,
    object_id: str | None,
) -> tuple[CategoryConfig | None, dict[str, str]]:
    name = str(user_input.get(CONF_NAME, "")).strip()
    icon = str(user_input.get(CONF_ICON, "")).strip() or DEFAULT_CATEGORY_ICON
    color = str(user_input.get(CONF_COLOR, "")).strip() or DEFAULT_CATEGORY_COLOR
    errors: dict[str, str] = {}
    if not name:
        errors[CONF_NAME] = "name_required"
    if ":" not in icon:
        errors[CONF_ICON] = "invalid_icon"
    resolved = object_id
    if resolved is None:
        raw = str(user_input.get(CONF_OBJECT_ID, "")).strip() or slugify(name)
        if not raw or not OBJECT_ID_RE.match(raw):
            errors[CONF_OBJECT_ID] = "invalid_object_id"
        elif raw in FORMULA_KEYWORDS:
            errors[CONF_OBJECT_ID] = "keyword_id"
        elif any(
            subentry.unique_id == subentry_uid(SUBENTRY_CATEGORY, raw)
            for subentry in entry.subentries.values()
        ):
            errors[CONF_OBJECT_ID] = "already_configured"
        else:
            resolved = raw
    if errors or not resolved:
        return None, errors
    try:
        category = CategoryConfig.from_storage(
            {
                CONF_NAME: name,
                CONF_ICON: icon,
                CONF_COLOR: color,
                CONF_OBJECT_ID: resolved,
            },
            resolved,
        )
    except ConfigError as err:
        return None, {err.field: err.code}
    return category, {}


def _add_category_subentry(hass: Any, entry: Any, category: CategoryConfig) -> None:
    """Add the category before the check or group is saved.

    The reload this schedules runs after the current step returns, so the
    check created in the same step is already stored.
    """
    from homeassistant.config_entries import ConfigSubentry

    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=category.to_storage(),
            subentry_type=SUBENTRY_CATEGORY,
            title=category.name,
            unique_id=subentry_uid(SUBENTRY_CATEGORY, category.object_id),
        ),
    )


class CategorySubentryFlow(ConfigSubentryFlow):
    """Create or edit a category: name, icon, and color together."""

    def _ensure(self) -> None:
        if getattr(self, "_ready", False):
            return
        self._ready = True
        self._initial: dict[str, Any] = {}
        self._object_id = ""
        if self.source != SOURCE_USER:
            subentry = self._get_reconfigure_subentry()
            self._initial = dict(subentry.data)
            self._object_id = _object_id_from_unique(subentry.unique_id)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start a new category."""
        self._ensure()
        return await self._async_form(user_input, "user")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit the name, icon, and color. The id stays the same."""
        self._ensure()
        return await self._async_form(user_input, "reconfigure")

    async def _async_form(
        self, user_input: dict[str, Any] | None, step_id: str
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            fixed_id = None if self.source == SOURCE_USER else self._object_id
            category, errors = _category_from_input(
                user_input,
                entry=self._get_entry(),
                object_id=fixed_id,
            )
            if category is not None:
                data = category.to_storage()
                if self.source == SOURCE_USER:
                    return self.async_create_entry(
                        title=category.name,
                        data=data,
                        unique_id=subentry_uid(SUBENTRY_CATEGORY, category.object_id),
                    )
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    title=category.name,
                    data=data,
                )
            self._initial = {**self._initial, **user_input}
        return self.async_show_form(
            step_id=step_id,
            data_schema=_category_schema(
                self._initial, include_id=self.source == SOURCE_USER
            ),
            errors=errors,
            description_placeholders={"object_id": self._object_id or "-"},
        )
