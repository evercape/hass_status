"""Verdict calculation.

Sensor values are read while calculating and then discarded. The objects
returned here contain the verdict, not the reading, so the recorder only
grows when the verdict changes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Optional

from .const import (
    COMPARATOR_IS,
    CONDITION_RANGE,
    CONDITION_STATE,
    CONDITION_TEMPLATE,
    LOGIC_ALL,
    LOGIC_ANY,
    LOGIC_FORMULA,
    REASON_FORMULA,
    REASON_HIGH,
    REASON_LOW,
    REASON_OK,
    REASON_STATE,
    REASON_TEMPLATE,
    REASON_UNAVAILABLE,
    SEVERITY_ATTENTION,
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_RANK,
    UNAVAILABLE_STATES,
)
from .formula import FormulaError, compile_formula
from .models import DEFAULT_MESSAGES, CheckConfig, ConditionConfig, GroupConfig

StateReader = Callable[[str], Optional[str]]
TemplateRenderer = Callable[[str], Optional[bool]]
LatchMap = dict[tuple[str, str], Optional[str]]

UNCHANGED = "unchanged"
COMMIT = "commit"
ARM = "arm"
WAITING = "waiting"


@dataclass(frozen=True)
class Verdict:
    """The published result of one check."""

    is_problem: bool
    severity: str
    reason: str
    focus_entity: str | None
    active_condition: str | None
    failed_conditions: tuple[str, ...]
    message: str
    category: str | None
    display_entity: str | None
    sources: tuple[str, ...]
    logic: str
    object_id: str

    def snapshot(self) -> tuple[object, ...]:
        """Return the parts that should cause a new recorder row."""
        return (
            self.is_problem,
            self.severity,
            self.reason,
            self.focus_entity,
            self.active_condition,
            self.failed_conditions,
            self.message,
            self.category,
            self.display_entity,
            self.sources,
            self.logic,
            self.object_id,
        )


@dataclass(frozen=True)
class GroupVerdict:
    """The published result of one group."""

    is_problem: bool
    severity: str
    reason: str
    focus_entity: str | None
    open_count: int
    open_checks: tuple[str, ...]
    members: tuple[str, ...]
    message: str
    category: str | None
    object_id: str


@dataclass(frozen=True)
class SummaryVerdict:
    """How many checks are currently open."""

    open_count: int
    checks: tuple[str, ...]
    by_category: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _ConditionVerdict:
    condition_id: str
    satisfied: bool
    reason: str
    severity: str
    entity_id: str | None
    message: str


class Hold:
    """Delay a verdict change until it has held for a number of seconds.

    The first sample is published immediately. Later samples must stay in the
    new verdict until the hold expires. A return to the published verdict
    cancels the wait. Time is supplied by the caller so this can be tested
    without a clock.
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = max(0.0, float(seconds))
        self.current: tuple[object, ...] | None = None
        self.pending: tuple[object, ...] | None = None
        self.pending_at: float | None = None

    def push(self, snapshot: tuple[object, ...], now: float) -> str:
        """Advance the hold and return UNCHANGED, COMMIT, ARM, or WAITING."""
        if self.current == snapshot:
            self.pending = None
            self.pending_at = None
            return UNCHANGED
        if self.seconds <= 0 or self.current is None:
            self.current = snapshot
            self.pending = None
            self.pending_at = None
            return COMMIT
        if self.pending != snapshot:
            self.pending = snapshot
            self.pending_at = now
            return ARM
        assert self.pending_at is not None
        if now - self.pending_at >= self.seconds:
            self.current = snapshot
            self.pending = None
            self.pending_at = None
            return COMMIT
        return WAITING


def interpret_truth(value: object) -> bool | None:
    """Map a template result to true, false, or unavailable."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "on", "yes", "1"}:
            return True
        if text in {"false", "off", "no", "0", ""}:
            return False
    return None


def parse_number(value: str) -> float | None:
    """Parse a Home Assistant state into a float.

    A single comma is treated as a decimal separator when the value has no dot.
    """
    text = value.strip()
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _unavailable(raw: str | None) -> bool:
    return raw is None or raw in UNAVAILABLE_STATES


def _range_reason(
    value: float,
    condition: ConditionConfig,
    latched: str | None,
) -> str:
    if condition.minimum is not None and value < condition.minimum:
        return REASON_LOW
    if condition.maximum is not None and value > condition.maximum:
        return REASON_HIGH
    if (
        latched == REASON_LOW
        and condition.minimum is not None
        and value < condition.minimum + condition.hysteresis
    ):
        return REASON_LOW
    if (
        latched == REASON_HIGH
        and condition.maximum is not None
        and value > condition.maximum - condition.hysteresis
    ):
        return REASON_HIGH
    return REASON_OK


def _evaluate_condition(
    check: CheckConfig,
    condition: ConditionConfig,
    read_state: StateReader,
    render: TemplateRenderer,
    latches: LatchMap,
) -> _ConditionVerdict:
    latch_key = (check.object_id, condition.condition_id)
    if condition.condition_type == CONDITION_TEMPLATE:
        assert condition.template is not None
        rendered = render(condition.template)
        if rendered is None:
            reason = REASON_UNAVAILABLE
        elif rendered:
            reason = REASON_OK
        else:
            reason = REASON_TEMPLATE
        return _result(condition, reason)

    assert condition.entity_id is not None
    raw = read_state(condition.entity_id)
    if _unavailable(raw):
        return _result(condition, REASON_UNAVAILABLE)

    if condition.condition_type == CONDITION_STATE:
        assert raw is not None
        assert condition.expected_state is not None
        matches = raw == condition.expected_state
        if condition.comparator == COMPARATOR_IS:
            reason = REASON_OK if matches else REASON_STATE
        else:
            reason = REASON_OK if not matches else REASON_STATE
        return _result(condition, reason)

    assert raw is not None
    value = parse_number(raw)
    if value is None:
        return _result(condition, REASON_UNAVAILABLE)
    reason = _range_reason(value, condition, latches.get(latch_key))
    if reason in (REASON_LOW, REASON_HIGH):
        latches[latch_key] = reason
    elif reason == REASON_OK:
        latches[latch_key] = None
    return _result(condition, reason)


def _result(condition: ConditionConfig, reason: str) -> _ConditionVerdict:
    return _ConditionVerdict(
        condition_id=condition.condition_id,
        satisfied=reason == REASON_OK,
        reason=reason,
        severity=condition.severity_for(reason),
        entity_id=condition.entity_id,
        message=condition.message_for(reason),
    )


def _focus(check: CheckConfig, entity_id: str | None) -> str | None:
    if entity_id:
        return entity_id
    if check.display_entity:
        return check.display_entity
    for condition in check.conditions:
        if condition.entity_id:
            return condition.entity_id
    return None


def _sources(check: CheckConfig) -> tuple[str, ...]:
    sources: list[str] = []
    for condition in check.conditions:
        if condition.entity_id and condition.entity_id not in sources:
            sources.append(condition.entity_id)
    return tuple(sources)


def _join_messages(results: list[_ConditionVerdict]) -> str:
    messages: list[str] = []
    for result in results:
        if result.message and result.message not in messages:
            messages.append(result.message)
    return " · ".join(messages)


def _worst(results: list[_ConditionVerdict]) -> _ConditionVerdict:
    best = results[0]
    best_rank = SEVERITY_RANK.get(best.severity, 0)
    for result in results[1:]:
        rank = SEVERITY_RANK.get(result.severity, 0)
        if rank > best_rank:
            best = result
            best_rank = rank
    return best


def _ok_verdict(check: CheckConfig) -> Verdict:
    message = check.message_ok or DEFAULT_MESSAGES[REASON_OK]
    return Verdict(
        is_problem=False,
        severity=SEVERITY_OK,
        reason=REASON_OK,
        focus_entity=_focus(check, check.display_entity),
        active_condition=None,
        failed_conditions=(),
        message=message,
        category=check.category,
        display_entity=check.display_entity,
        sources=_sources(check),
        logic=check.logic,
        object_id=check.object_id,
    )


def _problem_verdict(
    check: CheckConfig,
    *,
    reason: str,
    severity: str,
    focus: str | None,
    active: str | None,
    failed: tuple[str, ...],
    message: str,
) -> Verdict:
    return Verdict(
        is_problem=True,
        severity=severity,
        reason=reason,
        focus_entity=_focus(check, focus),
        active_condition=active,
        failed_conditions=failed,
        message=message or DEFAULT_MESSAGES.get(reason, DEFAULT_MESSAGES[REASON_UNAVAILABLE]),
        category=check.category,
        display_entity=check.display_entity,
        sources=_sources(check),
        logic=check.logic,
        object_id=check.object_id,
    )


def evaluate_check(
    check: CheckConfig,
    read_state: StateReader,
    render: TemplateRenderer,
    latches: LatchMap,
) -> Verdict:
    """Evaluate a check. Mutates ``latches`` for range hysteresis."""
    results = [
        _evaluate_condition(check, condition, read_state, render, latches)
        for condition in check.conditions
    ]
    satisfied = {result.condition_id: result.satisfied for result in results}
    if check.logic == LOGIC_ALL:
        passed = all(result.satisfied for result in results)
    elif check.logic == LOGIC_ANY:
        passed = any(result.satisfied for result in results)
    elif check.logic == LOGIC_FORMULA:
        try:
            runner, _names = compile_formula(check.formula)
            passed = runner(satisfied)
        except FormulaError:
            return _problem_verdict(
                check,
                reason=REASON_FORMULA,
                severity=SEVERITY_CRITICAL,
                focus=check.display_entity,
                active=None,
                failed=(),
                message=DEFAULT_MESSAGES[REASON_FORMULA],
            )
    else:
        passed = False
    if passed:
        return _ok_verdict(check)
    failed_results = [result for result in results if not result.satisfied]
    if not failed_results:
        return _problem_verdict(
            check,
            reason=REASON_FORMULA,
            severity=SEVERITY_CRITICAL,
            focus=check.display_entity,
            active=None,
            failed=(),
            message=DEFAULT_MESSAGES[REASON_FORMULA],
        )
    chosen = _worst(failed_results)
    return _problem_verdict(
        check,
        reason=chosen.reason,
        severity=chosen.severity if chosen.severity != SEVERITY_OK else SEVERITY_ATTENTION,
        focus=chosen.entity_id,
        active=chosen.condition_id,
        failed=tuple(result.condition_id for result in failed_results),
        message=_join_messages(failed_results),
    )


def evaluate_group(
    group: GroupConfig,
    checks: Mapping[str, CheckConfig],
    verdicts: Mapping[str, Verdict],
) -> GroupVerdict:
    """Combine child checks. Missing checks count as not reporting."""
    if not group.members:
        return GroupVerdict(
            is_problem=True,
            severity=SEVERITY_ATTENTION,
            reason=REASON_UNAVAILABLE,
            focus_entity=None,
            open_count=0,
            open_checks=(),
            members=(),
            message="No checks selected",
            category=group.category,
            object_id=group.object_id,
        )
    open_ids: list[str] = []
    open_verdicts: list[tuple[str, Verdict]] = []
    for member in group.members:
        verdict = verdicts.get(member)
        check = checks.get(member)
        if verdict is None or check is None:
            missing = Verdict(
                is_problem=True,
                severity=SEVERITY_ATTENTION,
                reason=REASON_UNAVAILABLE,
                focus_entity=None,
                active_condition=None,
                failed_conditions=(),
                message="Not reporting",
                category=None,
                display_entity=None,
                sources=(),
                logic=LOGIC_ALL,
                object_id=member,
            )
            open_ids.append(member)
            open_verdicts.append((member, missing))
            continue
        if verdict.is_problem:
            open_ids.append(member)
            open_verdicts.append((member, verdict))
    if not open_verdicts:
        focus = None
        first = group.members[0]
        if first in verdicts:
            focus = verdicts[first].focus_entity
        return GroupVerdict(
            is_problem=False,
            severity=SEVERITY_OK,
            reason=REASON_OK,
            focus_entity=focus,
            open_count=0,
            open_checks=(),
            members=group.members,
            message=DEFAULT_MESSAGES[REASON_OK],
            category=group.category,
            object_id=group.object_id,
        )
    worst_id, worst = open_verdicts[0]
    worst_rank = SEVERITY_RANK.get(worst.severity, 0)
    for member_id, verdict in open_verdicts[1:]:
        rank = SEVERITY_RANK.get(verdict.severity, 0)
        if rank > worst_rank:
            worst_id, worst = member_id, verdict
            worst_rank = rank
    names: list[str] = []
    for member_id, _verdict in open_verdicts:
        check = checks.get(member_id)
        names.append(check.name if check else member_id)
    return GroupVerdict(
        is_problem=True,
        severity=worst.severity if worst.severity != SEVERITY_OK else SEVERITY_ATTENTION,
        reason=worst.reason,
        focus_entity=worst.focus_entity,
        open_count=len(open_ids),
        open_checks=tuple(open_ids),
        members=group.members,
        message=" · ".join(names),
        category=group.category,
        object_id=group.object_id,
    )


def evaluate_summary(
    checks: Mapping[str, CheckConfig],
    verdicts: Mapping[str, Verdict],
) -> SummaryVerdict:
    """Count open checks. Groups are not included."""
    open_ids = tuple(
        check.object_id
        for check in checks.values()
        if check.object_id in verdicts and verdicts[check.object_id].is_problem
    )
    counts: dict[str, int] = {}
    has_uncategorized = False
    for check in checks.values():
        if check.category:
            counts.setdefault(check.category, 0)
        else:
            has_uncategorized = True
    if has_uncategorized:
        counts["Uncategorized"] = 0
    for object_id in open_ids:
        label = checks[object_id].category or "Uncategorized"
        counts[label] = counts.get(label, 0) + 1
    return SummaryVerdict(
        open_count=len(open_ids),
        checks=open_ids,
        by_category=tuple(sorted(counts.items())),
    )
