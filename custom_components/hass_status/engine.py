"""Track source entities and publish a new state only when the verdict changes."""

from __future__ import annotations

from collections.abc import Callable
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.event import (
    TrackTemplate,
    async_call_later,
    async_track_state_change_event,
    async_track_template_result,
)
from homeassistant.helpers.template import Template

from .const import (
    LISTENER_SUMMARY,
    SUBENTRY_CHECK,
    SUBENTRY_GROUP,
    listener_key_check,
    listener_key_group,
)
from .evaluator import (
    ARM,
    COMMIT,
    UNCHANGED,
    WAITING,
    GroupVerdict,
    Hold,
    LatchMap,
    SummaryVerdict,
    Verdict,
    evaluate_check,
    evaluate_group,
    evaluate_summary,
    interpret_truth,
)
from .models import CheckConfig, ConfigError, GroupConfig

_LOGGER = logging.getLogger(__name__)

HassStatusConfigEntry = ConfigEntry


class HassStatusRuntime:
    """In-memory board. Configuration lives on the config entry, not here."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.checks: dict[str, CheckConfig] = {}
        self.groups: dict[str, GroupConfig] = {}
        self.verdicts: dict[str, Verdict] = {}
        self.group_verdicts: dict[str, GroupVerdict] = {}
        self.summary = SummaryVerdict(0, (), ())
        self._holds: dict[str, Hold] = {}
        self._latches: LatchMap = {}
        self._listeners: dict[str, Callable[[], None]] = {}
        self._unsubs: list[Callable[[], None]] = []
        self._timers: dict[str, Callable[[], None]] = {}
        self._stopped = False

    def load(self) -> None:
        """Read checks and groups from the config entry."""
        checks: dict[str, CheckConfig] = {}
        groups: dict[str, GroupConfig] = {}
        holds: dict[str, Hold] = {}
        for subentry in self.entry.subentries.values():
            if subentry.subentry_type == SUBENTRY_CHECK:
                try:
                    check = CheckConfig.from_storage(subentry.data, _object_id(subentry.unique_id))
                except ConfigError as err:
                    _LOGGER.error(
                        "Skipping check %s on %s: %s",
                        subentry.title,
                        self.entry.title,
                        err.detail or err.code,
                    )
                    continue
                checks[check.object_id] = check
                holds[check.object_id] = Hold(check.hold_seconds)
            elif subentry.subentry_type == SUBENTRY_GROUP:
                try:
                    group = GroupConfig.from_storage(
                        subentry.data, _object_id(subentry.unique_id)
                    )
                except ConfigError as err:
                    _LOGGER.error(
                        "Skipping group %s on %s: %s",
                        subentry.title,
                        self.entry.title,
                        err.detail or err.code,
                    )
                    continue
                groups[group.object_id] = group
        self.checks = checks
        self.groups = groups
        self._holds = holds

    @callback
    def async_start(self) -> None:
        """Evaluate once, then listen. No await, so no event can land in between."""
        self._reevaluate(initial=True)
        self._subscribe()

    @callback
    def async_stop(self) -> None:
        """Drop listeners and pending holds."""
        if self._stopped:
            return
        self._stopped = True
        while self._unsubs:
            self._unsubs.pop()()
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()

    @callback
    def listen(self, key: str, update: Callable[[], None]) -> Callable[[], None]:
        """Register an entity callback. Returns an unsubscribe function."""
        self._listeners[key] = update

        def _unsub() -> None:
            if self._listeners.get(key) is update:
                self._listeners.pop(key, None)

        return _unsub

    def _subscribe(self) -> None:
        entity_ids: list[str] = []
        seen: set[str] = set()
        templates: list[str] = []
        seen_templates: set[str] = set()
        for check in self.checks.values():
            for condition in check.conditions:
                if condition.entity_id and condition.entity_id not in seen:
                    seen.add(condition.entity_id)
                    entity_ids.append(condition.entity_id)
                if condition.template and condition.template not in seen_templates:
                    seen_templates.add(condition.template)
                    templates.append(condition.template)
        if entity_ids:
            self._unsubs.append(
                async_track_state_change_event(self.hass, entity_ids, self._on_state)
            )
        if templates:
            info = async_track_template_result(
                self.hass,
                [TrackTemplate(Template(template, self.hass), None) for template in templates],
                self._on_template,
            )
            info.async_refresh()
            self._unsubs.append(info.async_remove)

    def _read_state(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return state.state

    def _render(self, template: str) -> bool | None:
        try:
            rendered = Template(template, self.hass).async_render(parse_result=False)
        except TemplateError:
            _LOGGER.debug("Template failed for %s", self.entry.title, exc_info=True)
            return None
        return interpret_truth(rendered)

    @callback
    def _on_state(self, _event: Event) -> None:
        if not self._stopped:
            self._reevaluate()

    @callback
    def _on_template(self, *_args: object) -> None:
        if not self._stopped:
            self._reevaluate()

    @callback
    def _on_hold(self, object_id: str) -> None:
        self._timers.pop(object_id, None)
        if self._stopped:
            return
        self._reevaluate()

    def _reevaluate(self, initial: bool = False) -> None:
        if self._stopped:
            return
        now = time.monotonic()
        aggregates = initial
        for check in self.checks.values():
            verdict = evaluate_check(check, self._read_state, self._render, self._latches)
            if self._apply_check(check.object_id, verdict, now, initial):
                aggregates = True
        if aggregates:
            self._refresh_aggregates()

    def _apply_check(self, object_id: str, verdict: Verdict, now: float, initial: bool) -> bool:
        hold = self._holds[object_id]
        snapshot = verdict.snapshot()
        if initial:
            hold.current = snapshot
            hold.pending = None
            hold.pending_at = None
            self.verdicts[object_id] = verdict
            return True
        action = hold.push(snapshot, now)
        if action == UNCHANGED:
            self._cancel_timer(object_id)
            return False
        if action == COMMIT:
            self._cancel_timer(object_id)
            self.verdicts[object_id] = verdict
            self._notify(listener_key_check(object_id))
            return True
        if action in (ARM, WAITING):
            self._arm(object_id, hold, now)
        return False

    def _arm(self, object_id: str, hold: Hold, now: float) -> None:
        self._cancel_timer(object_id)
        remaining = hold.seconds
        if hold.pending_at is not None:
            remaining = hold.seconds - (now - hold.pending_at)
        # A zero delay that is still a fraction early would schedule itself
        # again immediately. Keep a small gap so the clock can move forward.
        if remaining <= 0:
            remaining = 0.05
        self._timers[object_id] = async_call_later(
            self.hass,
            remaining,
            lambda _now, check_id=object_id: self._on_hold(check_id),
        )

    def _cancel_timer(self, object_id: str) -> None:
        cancel = self._timers.pop(object_id, None)
        if cancel is not None:
            cancel()

    def _refresh_aggregates(self) -> None:
        for group in self.groups.values():
            verdict = evaluate_group(group, self.checks, self.verdicts)
            previous = self.group_verdicts.get(group.object_id)
            self.group_verdicts[group.object_id] = verdict
            if previous != verdict:
                self._notify(listener_key_group(group.object_id))
        summary = evaluate_summary(self.checks, self.verdicts)
        if summary != self.summary:
            self.summary = summary
            self._notify(LISTENER_SUMMARY)

    def _notify(self, key: str) -> None:
        listener = self._listeners.get(key)
        if listener is not None:
            listener()


def _object_id(unique_id: str | None) -> str | None:
    if not unique_id or ":" not in unique_id:
        return unique_id
    return unique_id.split(":", 1)[1]


def kept_unique_ids(entry_id: str, runtime: HassStatusRuntime) -> set[str]:
    """Entity registry ids that this board still owns."""
    from .const import unique_id_check, unique_id_group, unique_id_summary

    ids = {unique_id_summary(entry_id)}
    ids.update(unique_id_check(entry_id, object_id) for object_id in runtime.checks)
    ids.update(unique_id_group(entry_id, object_id) for object_id in runtime.groups)
    return ids
