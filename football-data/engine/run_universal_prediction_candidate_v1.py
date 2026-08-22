#!/usr/bin/env python3
"""Default all-competition prediction router with explicit downgrade labels.

The hash-bound V460 formal core is always attempted first. Only ordinary
coverage gaps may downgrade to the cold-start candidate. Integrity failures
(hash, receipt, schema, validation-report or PIT binding failures) remain hard
errors and are never converted into a baseline prediction.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from football_v460_engine import current_season_history, predict_joint_distribution
from formal_cold_start_candidate_v1 import predict_cold_start_from_history
from platform_core import PlatformError, atomic_write_json, parse_iso_datetime, read_processed_matches

ALLOWED_COVERAGE_GAPS = (
    "processed competition directory missing:",
    "no usable processed matches for ",
    "no completed competition matches strictly before prediction cutoff date",
    "validated formal-core artifact missing for ",
    "current-season history has ",
    "one or both teams have no current-season history",
    "venue-specific sample below minimum:",
    "no point-in-time validated parameter set for target season ",
)

INTEGRITY_FAILURE_MARKERS = (
    "hash does not match",
    "validation report missing",
    "is not operational",
    "schema",
    "duplicate",
    "negative goals",
)


def _is_coverage_gap(exc: PlatformError) -> bool:
    message = str(exc)
    if any(marker in message for marker in INTEGRITY_FAILURE_MARKERS):
        return False
    return message.startswith(ALLOWED_COVERAGE_GAPS)


def _explicit_request(payload: Any) -> tuple[str, str, str, str, Any]:
    if not isinstance(payload, dict):
        raise PlatformError("universal prediction input must be an object")
    competition_id = str(payload.get("competition_id") or "").strip()
    season = str(payload.get("season") or "").strip()
    home_team = str(payload.get("home_team") or "").strip()
    away_team = str(payload.get("away_team") or "").strip()
    if not all((competition_id, season, home_team, away_team)):
        raise PlatformError("competition_id, season, home_team and away_team are required")
    cutoff = parse_iso_datetime(payload.get("cutoff_utc"), "cutoff_utc")
    return competition_id, season, home_team, away_team, cutoff


def _pit_history(competition_id: str, season: str, cutoff: Any) -> list[Any]:
    try:
        matches = read_processed_matches(competition_id)
    except PlatformError as exc:
        if _is_coverage_gap(exc):
            return []
        raise
    _, history = current_season_history(matches, cutoff, season)
    return history


def predict_universal(payload: dict[str, Any]) -> dict[str, Any]:
    competition_id, season, home_team, away_team, cutoff = _explicit_request(payload)
    try:
        output = predict_joint_distribution(
            competition_id,
            home_team,
            away_team,
            cutoff,
            season=season,
        )
        output["universal_router"] = {
            "route": "FORMAL_V460",
            "downgraded": False,
            "coverage_guarantee": True,
        }
        return output
    except PlatformError as exc:
        if not _is_coverage_gap(exc):
            raise
        formal_gap = str(exc)

    history = _pit_history(competition_id, season, cutoff)
    output = predict_cold_start_from_history(
        history,
        competition_id,
        season,
        home_team,
        away_team,
        cutoff,
    )
    output["universal_router"] = {
        "route": output["cold_start_candidate"]["state"],
        "downgraded": True,
        "formal_gap": formal_gap,
        "coverage_guarantee": True,
        "not_a_formal_core_prediction": True,
    }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = predict_universal(payload)
        if args.output:
            atomic_write_json(args.output, result)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, PlatformError) as exc:
        print(json.dumps({"status": "HARD_FAIL", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
