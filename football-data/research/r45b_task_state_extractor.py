#!/usr/bin/env python3
"""Deterministic zero-label task-state extractor for R45B.

This module converts source-backed, pre-freeze facts into structured task-state
facts only. It never reads target result labels and never emits probability,
goal, sentiment, motivation-score, or fatigue-weight adjustments.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EXTRACTOR_VERSION = "R45B-TASK-STATE-EXTRACTOR-R1"
INPUT_SCHEMA = "R45B-TASK-STATE-INPUT-R1"
OUTPUT_SCHEMA = "R45B-TASK-STATE-OUTPUT-R1"
SUPPORTED = {
    "schedule_fatigue",
    "travel",
    "rotation",
    "motivation_task_state",
    "aggregate_or_qualification_state",
}
FORBIDDEN_OUTPUT_KEYS = {
    "manual_probability_override",
    "manual_goal_adjustment",
    "sentiment_score",
    "motivation_score",
    "fatigue_penalty_weight",
}


def parse_ts(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"missing:{field}")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid_timestamp:{field}") from exc
    if dt.tzinfo is None:
        raise ValueError(f"timezone_required:{field}")
    return dt.astimezone(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_text(obj: dict[str, Any], key: str) -> str:
    value = str(obj.get(key) or "").strip()
    if not value:
        raise ValueError(f"missing:{key}")
    return value


def canonical_names(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"missing_or_empty:{field}")
    names: list[str] = []
    seen: set[str] = set()
    for raw in value:
        name = str(raw or "").strip()
        if not name:
            raise ValueError(f"empty_name:{field}")
        key = name.casefold()
        if key in seen:
            raise ValueError(f"duplicate_name:{field}:{name}")
        seen.add(key)
        names.append(name)
    return names


def extract_schedule_fatigue(inp: dict[str, Any], freeze: datetime, target: datetime) -> dict[str, Any]:
    raw = inp.get("strictly_prior_fixtures")
    if not isinstance(raw, list) or not raw:
        raise ValueError("missing_or_empty:strictly_prior_fixtures")
    prior: list[datetime] = []
    for idx, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"fixture_not_object:{idx}")
        kickoff = parse_ts(row.get("kickoff_at_utc"), f"strictly_prior_fixtures[{idx}].kickoff_at_utc")
        if kickoff >= freeze:
            raise ValueError(f"fixture_not_strictly_before_freeze:{idx}")
        if kickoff >= target:
            raise ValueError(f"fixture_not_before_target:{idx}")
        prior.append(kickoff)
    prior.sort()
    previous = prior[-1]
    return {
        "hours_since_previous_fixture": round((target - previous).total_seconds() / 3600.0, 6),
        "previous_fixture_kickoff_at_utc": iso_z(previous),
        "prior_fixtures_last_7d": sum(target - timedelta(days=7) <= dt < target for dt in prior),
        "prior_fixtures_last_14d": sum(target - timedelta(days=14) <= dt < target for dt in prior),
    }


def extract_travel(inp: dict[str, Any]) -> dict[str, Any]:
    return {
        "location_pair": {
            "previous_fixture_location": require_text(inp, "source_backed_previous_fixture_location"),
            "target_fixture_location": require_text(inp, "source_backed_target_fixture_location"),
        }
    }


def extract_rotation(inp: dict[str, Any]) -> dict[str, Any]:
    current = canonical_names(inp.get("source_backed_expected_or_confirmed_xi"), "source_backed_expected_or_confirmed_xi")
    prior = canonical_names(inp.get("strictly_prior_source_backed_xi"), "strictly_prior_source_backed_xi")
    prior_keys = {x.casefold() for x in prior}
    overlap = sum(x.casefold() in prior_keys for x in current)
    return {
        "prior_xi_overlap_count": overlap,
        "changed_player_count": len(current) - overlap,
    }


def extract_passthrough(inp: dict[str, Any], source_key: str, output_key: str) -> dict[str, Any]:
    fact = inp.get(source_key)
    if not isinstance(fact, (dict, list, str, int, float, bool)) or fact in ("", [], {}):
        raise ValueError(f"missing_or_empty:{source_key}")
    return {output_key: fact}


def extract(inp: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(inp, dict):
        raise ValueError("input_not_object")
    if inp.get("schema_version") != INPUT_SCHEMA:
        raise ValueError("input_schema_mismatch")
    team = require_text(inp, "team")
    state = require_text(inp, "task_state_type")
    if state not in SUPPORTED:
        raise ValueError(f"unsupported_task_state_type:{state}")

    freeze = parse_ts(inp.get("freeze_at_utc"), "freeze_at_utc")
    target = parse_ts(inp.get("target_kickoff_at_utc"), "target_kickoff_at_utc")
    if not freeze < target:
        raise ValueError("freeze_must_precede_target_kickoff")

    if state == "schedule_fatigue":
        facts = extract_schedule_fatigue(inp, freeze, target)
    elif state == "travel":
        facts = extract_travel(inp)
    elif state == "rotation":
        facts = extract_rotation(inp)
    elif state == "motivation_task_state":
        facts = extract_passthrough(
            inp,
            "official_or_independent_structured_competition_fact",
            "structured_competition_fact",
        )
    else:
        facts = extract_passthrough(
            inp,
            "official_first_leg_or_group_state_known_before_freeze",
            "aggregate_state_or_qualification_fact",
        )

    if FORBIDDEN_OUTPUT_KEYS.intersection(facts):
        raise AssertionError("forbidden_output_key_emitted")

    return {
        "schema_version": OUTPUT_SCHEMA,
        "team": team,
        "task_state_type": state,
        "structured_facts": facts,
        "extractor_version": EXTRACTOR_VERSION,
        "freeze_at_utc": iso_z(freeze),
        "target_kickoff_at_utc": iso_z(target),
        "target_result_labels_used": 0,
        "manual_adjustments_emitted": 0,
    }


def self_test() -> None:
    schedule = extract(
        {
            "schema_version": INPUT_SCHEMA,
            "team": "Example FC",
            "task_state_type": "schedule_fatigue",
            "freeze_at_utc": "2026-08-13T08:30:00Z",
            "target_kickoff_at_utc": "2026-08-15T17:30:00Z",
            "strictly_prior_fixtures": [
                {"kickoff_at_utc": "2026-08-08T14:00:00Z"},
                {"kickoff_at_utc": "2026-08-06T18:00:00Z"},
            ],
        }
    )
    facts = schedule["structured_facts"]
    assert facts["hours_since_previous_fixture"] == 171.5
    assert facts["prior_fixtures_last_7d"] == 0
    assert facts["prior_fixtures_last_14d"] == 2
    assert schedule["target_result_labels_used"] == 0

    travel = extract(
        {
            "schema_version": INPUT_SCHEMA,
            "team": "Example FC",
            "task_state_type": "travel",
            "freeze_at_utc": "2026-08-13T08:30:00Z",
            "target_kickoff_at_utc": "2026-08-15T17:30:00Z",
            "source_backed_previous_fixture_location": "Hotspur Way, London",
            "source_backed_target_fixture_location": "Mendizorroza, Vitoria-Gasteiz",
        }
    )
    assert travel["structured_facts"]["location_pair"]["previous_fixture_location"] == "Hotspur Way, London"

    rotation = extract(
        {
            "schema_version": INPUT_SCHEMA,
            "team": "Example FC",
            "task_state_type": "rotation",
            "freeze_at_utc": "2026-08-13T08:30:00Z",
            "target_kickoff_at_utc": "2026-08-15T17:30:00Z",
            "source_backed_expected_or_confirmed_xi": [f"P{i}" for i in range(1, 12)],
            "strictly_prior_source_backed_xi": [f"P{i}" for i in range(1, 10)] + ["Q1", "Q2"],
        }
    )
    assert rotation["structured_facts"] == {"prior_xi_overlap_count": 9, "changed_player_count": 2}

    print("R45B_TASK_STATE_EXTRACTOR_SELF_TEST_PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if args.input is None or args.output is None:
        parser.error("--input and --output are required unless --self-test is used")

    inp = json.loads(args.input.read_text(encoding="utf-8"))
    out = extract(inp)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
