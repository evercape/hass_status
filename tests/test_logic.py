"""Tests for formulas, checks, groups, and the hold timer."""

from __future__ import annotations

import unittest

import tests.bootstrap  # noqa: F401

from custom_components.hass_status.const import LOGIC_ALL, LOGIC_ANY, LOGIC_FORMULA
from custom_components.hass_status.evaluator import (
    ARM,
    COMMIT,
    UNCHANGED,
    WAITING,
    Hold,
    evaluate_check,
    evaluate_group,
    evaluate_summary,
    interpret_truth,
    parse_number,
)
from custom_components.hass_status.formula import FormulaError, compile_formula
from custom_components.hass_status.models import (
    CategoryConfig,
    CheckConfig,
    ConfigError,
    GroupConfig,
    decorate_check,
    duration_to_seconds,
    seconds_to_duration,
)


def _range_check(**overrides) -> CheckConfig:
    raw = {
        "name": "Pool pH",
        "logic": "all",
        "category": "Pool",
        "display_entity": "sensor.pool_ph",
        "message_ok": "Pool chemistry is in range",
        "conditions": [
            {
                "id": "ph",
                "type": "range",
                "entity_id": "sensor.pool_ph",
                "minimum": 6.5,
                "maximum": 8.5,
                "hysteresis": 0.1,
                "severity": "attention",
                "message_low": "pH is {value}, below {min}",
                "message_high": "pH is {value}, above {max}",
            }
        ],
    }
    raw.update(overrides)
    return CheckConfig.from_storage(raw, "pool_ph")


class FormulaTests(unittest.TestCase):
    def test_and_or_not_and_case(self) -> None:
        run, names = compile_formula("A AND NOT B")
        self.assertEqual(names, frozenset({"a", "b"}))
        self.assertTrue(run({"a": True, "b": False}))
        self.assertFalse(run({"a": True, "b": True}))
        self.assertFalse(run({"a": False, "b": False}))

    def test_parentheses(self) -> None:
        run, _names = compile_formula("(a or b) and not c")
        self.assertTrue(run({"a": False, "b": True, "c": False}))
        self.assertFalse(run({"a": True, "b": True, "c": True}))

    def test_rejects_python(self) -> None:
        with self.assertRaises(FormulaError):
            compile_formula("__import__('os')")
        with self.assertRaises(FormulaError):
            compile_formula("a and")


class ModelTests(unittest.TestCase):
    def test_duration_round_trip(self) -> None:
        self.assertEqual(duration_to_seconds({"minutes": 5}), 300)
        self.assertEqual(seconds_to_duration(90), {"minutes": 1, "seconds": 30})

    def test_category_round_trip_and_color(self) -> None:
        category = CategoryConfig.from_storage(
            {"name": "Water", "icon": "mdi:water", "color": "blue"},
            "water",
        )
        self.assertEqual(CategoryConfig.from_storage(category.to_storage()), category)
        with self.assertRaises(ConfigError) as raised:
            CategoryConfig.from_storage(
                {"name": "Water", "icon": "mdi:water", "color": "nope"},
                "water",
            )
        self.assertEqual(raised.exception.code, "invalid_color")

    def test_decorate_check_uses_category_name(self) -> None:
        check = _range_check(category="water")
        decorated = decorate_check(
            check,
            {"water": CategoryConfig("water", "Water", "mdi:water", "blue")},
        )
        self.assertEqual(decorated.category, "Water")
        self.assertEqual(decorated.category_id, "water")
        self.assertEqual(decorated.category_icon, "mdi:water")
        self.assertEqual(decorated.category_color, "blue")
        self.assertEqual(check.category, "water")
        self.assertIsNone(decorate_check(check, {}).category_icon)

    def test_rejects_bad_range_and_keywords(self) -> None:
        with self.assertRaises(ConfigError) as raised:
            CheckConfig.from_storage(
                {
                    "name": "Pressure",
                    "conditions": [
                        {
                            "id": "and",
                            "type": "range",
                            "entity_id": "sensor.pressure",
                            "minimum": 4,
                            "maximum": 3,
                        }
                    ],
                },
                "pressure",
            )
        self.assertEqual(raised.exception.code, "keyword_id")
        with self.assertRaises(ConfigError) as raised_range:
            CheckConfig.from_storage(
                {
                    "name": "Pressure",
                    "conditions": [
                        {
                            "id": "pressure",
                            "type": "range",
                            "entity_id": "sensor.pressure",
                            "minimum": 4,
                            "maximum": 3,
                        }
                    ],
                },
                "pressure",
            )
        self.assertEqual(raised_range.exception.code, "min_above_max")

    def test_formula_must_name_real_conditions(self) -> None:
        with self.assertRaises(ConfigError) as raised:
            CheckConfig.from_storage(
                {
                    "name": "Water",
                    "logic": "formula",
                    "formula": "pressure and missing",
                    "conditions": [
                        {
                            "id": "pressure",
                            "type": "range",
                            "entity_id": "sensor.pressure",
                            "minimum": 3,
                            "maximum": 4,
                        }
                    ],
                },
                "house_water",
            )
        self.assertEqual(raised.exception.code, "formula_unknown")
        self.assertEqual(raised.exception.detail, "missing")


class EvaluatorTests(unittest.TestCase):
    def test_range_and_a_new_reading_do_not_change_the_verdict(self) -> None:
        check = _range_check()
        latches: dict = {}
        first = evaluate_check(check, lambda _entity: "7.1", lambda _template: None, latches)
        second = evaluate_check(check, lambda _entity: "7.2", lambda _template: None, latches)
        self.assertFalse(first.is_problem)
        self.assertEqual(first.snapshot(), second.snapshot())
        self.assertEqual(first.message, "Pool chemistry is in range")
        self.assertNotIn("7.1", first.message)
        self.assertEqual(first.focus_entity, "sensor.pool_ph")

    def test_placeholders_stay_unrendered(self) -> None:
        check = _range_check()
        verdict = evaluate_check(check, lambda _entity: "9.1", lambda _template: None, {})
        self.assertTrue(verdict.is_problem)
        self.assertEqual(verdict.reason, "high")
        self.assertEqual(verdict.severity, "attention")
        self.assertEqual(verdict.message, "pH is {value}, above {max}")
        self.assertNotIn("9.1", verdict.message)

    def test_comma_decimal_and_hysteresis(self) -> None:
        self.assertEqual(parse_number("6,4"), 6.4)
        check = _range_check()
        latches: dict = {}
        low = evaluate_check(check, lambda _entity: "6,4", lambda _template: None, latches)
        self.assertEqual(low.reason, "low")
        still = evaluate_check(check, lambda _entity: "6.55", lambda _template: None, latches)
        self.assertEqual(still.reason, "low")
        cleared = evaluate_check(check, lambda _entity: "6.6", lambda _template: None, latches)
        self.assertEqual(cleared.reason, "ok")

    def test_unavailable_does_not_clear_hysteresis(self) -> None:
        check = _range_check()
        latches: dict = {}
        evaluate_check(check, lambda _entity: "6.0", lambda _template: None, latches)
        missing = evaluate_check(check, lambda _entity: None, lambda _template: None, latches)
        self.assertEqual(missing.reason, "unavailable")
        still = evaluate_check(check, lambda _entity: "6.55", lambda _template: None, latches)
        self.assertEqual(still.reason, "low")

    def test_boundary_is_ok(self) -> None:
        check = _range_check()
        verdict = evaluate_check(check, lambda _entity: "6.5", lambda _template: None, {})
        self.assertFalse(verdict.is_problem)

    def test_combined_check_uses_the_worst_failure(self) -> None:
        check = CheckConfig.from_storage(
            {
                "name": "House water",
                "logic": "all",
                "category": "Water",
                "display_entity": "sensor.house_pressure",
                "conditions": [
                    {
                        "id": "pressure",
                        "type": "range",
                        "entity_id": "sensor.house_pressure",
                        "minimum": 3,
                        "maximum": 4,
                        "severity": "attention",
                        "message_low": "Pressure is low",
                    },
                    {
                        "id": "leak",
                        "type": "state",
                        "entity_id": "binary_sensor.kitchen_leak",
                        "expected_state": "off",
                        "severity": "critical",
                        "message_fail": "Kitchen leak sensor is wet",
                    },
                ],
            },
            "house_water",
        )
        states = {
            "sensor.house_pressure": "2.5",
            "binary_sensor.kitchen_leak": "on",
        }
        verdict = evaluate_check(
            check, lambda entity: states.get(entity), lambda _template: None, {}
        )
        self.assertEqual(verdict.severity, "critical")
        self.assertEqual(verdict.reason, "state")
        self.assertEqual(verdict.active_condition, "leak")
        self.assertEqual(verdict.focus_entity, "binary_sensor.kitchen_leak")
        self.assertEqual(verdict.failed_conditions, ("pressure", "leak"))
        self.assertEqual(verdict.message, "Pressure is low · Kitchen leak sensor is wet")

        states["binary_sensor.kitchen_leak"] = "off"
        recovered = evaluate_check(
            check, lambda entity: states.get(entity), lambda _template: None, {}
        )
        self.assertEqual(recovered.reason, "low")
        self.assertEqual(recovered.focus_entity, "sensor.house_pressure")

    def test_any_and_state_is_not(self) -> None:
        check = CheckConfig.from_storage(
            {
                "name": "Internet",
                "logic": "any",
                "conditions": [
                    {
                        "id": "ping",
                        "type": "state",
                        "entity_id": "binary_sensor.ping",
                        "expected_state": "on",
                    },
                    {
                        "id": "router",
                        "type": "state",
                        "entity_id": "binary_sensor.router",
                        "expected_state": "off",
                        "comparator": "is_not",
                    },
                ],
            },
            "internet",
        )
        states = {"binary_sensor.ping": "off", "binary_sensor.router": "on"}
        verdict = evaluate_check(
            check, lambda entity: states.get(entity), lambda _template: None, {}
        )
        self.assertFalse(verdict.is_problem)

    def test_formula(self) -> None:
        check = CheckConfig.from_storage(
            {
                "name": "Water",
                "logic": LOGIC_FORMULA,
                "formula": "pressure and leak",
                "conditions": [
                    {
                        "id": "pressure",
                        "type": "range",
                        "entity_id": "sensor.pressure",
                        "minimum": 3,
                        "maximum": 4,
                    },
                    {
                        "id": "leak",
                        "type": "state",
                        "entity_id": "binary_sensor.leak",
                        "expected_state": "off",
                    },
                ],
            },
            "water",
        )
        states = {"sensor.pressure": "3.5", "binary_sensor.leak": "off"}
        ok = evaluate_check(check, lambda entity: states.get(entity), lambda _template: None, {})
        self.assertFalse(ok.is_problem)
        states["binary_sensor.leak"] = "on"
        bad = evaluate_check(
            check, lambda entity: states.get(entity), lambda _template: None, {}
        )
        self.assertTrue(bad.is_problem)
        self.assertEqual(bad.active_condition, "leak")

    def test_template_truth(self) -> None:
        self.assertTrue(interpret_truth("YES"))
        self.assertFalse(interpret_truth("false"))
        self.assertIsNone(interpret_truth(None))
        check = CheckConfig.from_storage(
            {
                "name": "Custom",
                "conditions": [
                    {"id": "custom", "type": "template", "template": "{{ 1 }}"}
                ],
            },
            "custom",
        )
        ok = evaluate_check(check, lambda _entity: None, lambda _template: True, {})
        failed = evaluate_check(check, lambda _entity: None, lambda _template: False, {})
        missing = evaluate_check(check, lambda _entity: None, lambda _template: None, {})
        self.assertFalse(ok.is_problem)
        self.assertEqual(failed.reason, "template")
        self.assertEqual(missing.reason, "unavailable")

    def test_group_and_summary_ignore_live_values(self) -> None:
        pressure = CheckConfig.from_storage(
            {
                "name": "Pressure",
                "category": "Water",
                "conditions": [
                    {
                        "id": "pressure",
                        "type": "range",
                        "entity_id": "sensor.pressure",
                        "minimum": 3,
                        "maximum": 4,
                    }
                ],
            },
            "pressure",
        )
        ph = _range_check()
        checks = {"pressure": pressure, "pool_ph": ph}
        states = {"sensor.pressure": "3.2", "sensor.pool_ph": "7.0"}
        verdicts = {
            object_id: evaluate_check(
                check, lambda entity: states.get(entity), lambda _template: None, {}
            )
            for object_id, check in checks.items()
        }
        group = GroupConfig.from_storage(
            {"name": "Water", "category": "Water", "members": ["pressure", "missing"]},
            "water",
        )
        grouped = evaluate_group(group, checks, verdicts)
        self.assertTrue(grouped.is_problem)
        self.assertEqual(grouped.open_checks, ("missing",))
        self.assertEqual(grouped.message, "missing")

        summary = evaluate_summary(checks, verdicts)
        self.assertEqual(summary.open_count, 0)
        self.assertEqual(summary.checks, ())
        states["sensor.pool_ph"] = "9"
        verdicts["pool_ph"] = evaluate_check(
            ph, lambda entity: states.get(entity), lambda _template: None, {}
        )
        summary = evaluate_summary(checks, verdicts)
        self.assertEqual(summary.open_count, 1)
        self.assertEqual(summary.checks, ("pool_ph",))
        self.assertIn(("Pool", 1), summary.by_category)
        self.assertIn(("Water", 0), summary.by_category)
        self.assertNotIn("9", str(summary))


class HoldTests(unittest.TestCase):
    def test_first_sample_is_immediate_and_blips_are_ignored(self) -> None:
        hold = Hold(60)
        self.assertEqual(hold.push(("ok",), 0), COMMIT)
        self.assertEqual(hold.push(("ok",), 10), UNCHANGED)
        self.assertEqual(hold.push(("problem",), 10), ARM)
        self.assertEqual(hold.current, ("ok",))
        self.assertEqual(hold.push(("problem",), 50), WAITING)
        self.assertEqual(hold.push(("ok",), 51), UNCHANGED)
        self.assertIsNone(hold.pending)
        self.assertEqual(hold.push(("problem",), 100), ARM)
        self.assertEqual(hold.push(("problem",), 160), COMMIT)
        self.assertEqual(hold.current, ("problem",))

    def test_zero_hold_commits(self) -> None:
        hold = Hold(0)
        self.assertEqual(hold.push(("ok",), 0), COMMIT)
        self.assertEqual(hold.push(("problem",), 1), COMMIT)


class LogicConstantTests(unittest.TestCase):
    def test_logic_names(self) -> None:
        self.assertEqual(LOGIC_ALL, "all")
        self.assertEqual(LOGIC_ANY, "any")
        self.assertEqual(LOGIC_FORMULA, "formula")


if __name__ == "__main__":
    unittest.main()
