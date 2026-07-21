#!/usr/bin/env python3
"""Shared V5.0.3 player-XI governance gate semantics.

Positive checks must be True. Prohibited-condition checks ending in
``_used_as_input`` must be False. This explicit polarity prevents a governance
bug where ``all(checks.values())`` incorrectly rejects a safe False value such
as ``target_actual_xi_used_as_input=False``.
"""

from __future__ import annotations

from typing import Any

PROHIBITED_FALSE_SUFFIXES = ("_used_as_input",)


def adjudicate_checks(checks: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    if not isinstance(checks, dict) or not checks:
        return False, {
            "status": "FAIL_EMPTY_CHECKS",
            "positive_failures": ["checks_missing_or_empty"],
            "prohibited_condition_failures": [],
        }
    positive_failures: list[str] = []
    prohibited_failures: list[str] = []
    evaluated: dict[str, dict[str, Any]] = {}
    for key, raw_value in checks.items():
        value = bool(raw_value)
        prohibited = key.endswith(PROHIBITED_FALSE_SUFFIXES)
        expected = False if prohibited else True
        passed = value is expected
        evaluated[key] = {
            "observed": value,
            "expected": expected,
            "passed": passed,
            "polarity": "prohibited_condition_must_be_false" if prohibited else "required_condition_must_be_true",
        }
        if not passed:
            if prohibited:
                prohibited_failures.append(key)
            else:
                positive_failures.append(key)
    passed = not positive_failures and not prohibited_failures
    return passed, {
        "status": "PASS" if passed else "FAIL",
        "positive_failures": positive_failures,
        "prohibited_condition_failures": prohibited_failures,
        "evaluated": evaluated,
    }
