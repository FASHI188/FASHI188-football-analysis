#!/usr/bin/env python3
"""Point-in-time probable-lineup route and validator.

Input contract (JSONL): football-data/lineups/<competition_id>/historical_lineups.jsonl
Each row must contain competition_id, season, kickoff_utc, team, and exactly 11
starter identifiers. Optional source fields are preserved for audit.

For every target team-match, the predictor uses only that team's earlier lineups
from the SAME target season. Older seasons may remain archived for research/audit
but carry zero weight in the active probable-lineup route. It produces starter
probabilities from exponentially decayed prior starts, takes the top eleven as the
probable XI, and scores overlap/Jaccard/player-level Brier against the subsequently
observed starters. The route never uses the target match's own lineup as an input
feature.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import sys
ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from platform_core import ROOT, PlatformError, atomic_write_json, load_registry, parse_iso_datetime, sha256_file, utc_now  # noqa: E402

DATA_ROOT = ROOT / "lineups"
REPORT_ROOT = ROOT / "validation" / "reports" / "probable_lineup_v462"
MANIFEST_PATH = ROOT / "manifests" / "probable_lineup_v462_status.json"
SCRIPT_PATH = Path(__file__).resolve()
LOOKBACK = 8
DECAY = 0.78
MIN_HISTORY = 3
MIN_VALIDATION_PREDICTIONS = 200


def _load_rows(competition_id: str) -> list[dict[str, Any]]:
    path = DATA_ROOT / competition_id / "historical_lineups.jsonl"
    if not path.exists():
        return []
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PlatformError(f"invalid lineup JSONL {path}:{line_number}: {exc}") from exc
        starters = row.get("starters")
        if not isinstance(starters, list) or len(starters) != 11 or len(set(map(str, starters))) != 11:
            raise PlatformError(f"lineup row must contain 11 unique starters: {path}:{line_number}")
        if str(row.get("competition_id")) != competition_id:
            raise PlatformError(f"competition_id mismatch in lineup row: {path}:{line_number}")
        row = dict(row)
        row["kickoff"] = parse_iso_datetime(row.get("kickoff_utc"), "kickoff_utc")
        row["team"] = str(row.get("team") or "").strip()
        row["season"] = str(row.get("season") or "").strip()
        if not row["team"]:
            raise PlatformError(f"lineup row missing team: {path}:{line_number}")
        if not row["season"]:
            raise PlatformError(f"lineup row missing season: {path}:{line_number}")
        row["starters"] = [str(player) for player in starters]
        rows.append(row)
    rows.sort(key=lambda item: (item["kickoff"], item["season"], item["team"]))
    return rows


def _predict(prior: list[dict[str, Any]]) -> dict[str, Any]:
    prior = prior[-LOOKBACK:]
    players = sorted({player for row in prior for player in row["starters"]})
    raw: dict[str, float] = {player: 0.0 for player in players}
    total_weight = 0.0
    for age, row in enumerate(reversed(prior)):
        weight = DECAY ** age
        total_weight += weight
        starters = set(row["starters"])
        for player in players:
            if player in starters:
                raw[player] += weight
    probabilities = {player: raw[player] / max(1e-12, total_weight) for player in players}
    ranking = sorted(probabilities, key=lambda player: (-probabilities[player], player))
    return {"probabilities": probabilities, "probable_xi": ranking[:11]}


def validate_competition(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    rows = _load_rows(competition_id)
    path = DATA_ROOT / competition_id / "historical_lineups.jsonl"
    if not rows:
        report = {
            "schema_version": "V4.6.3",
            "generated_at_utc": utc_now(),
            "competition_id": competition_id,
            "status": "LINEUP_ROUTE_DATA_UNAVAILABLE",
            "validated_for_a_grade": False,
            "prediction_count": 0,
            "data_path": str(path.relative_to(ROOT)),
            "implementation_sha256": sha256_file(SCRIPT_PATH),
            "season_scope": "same_target_season_only",
            "reason": "No point-in-time starting-lineup dataset is installed for this competition.",
        }
        if write:
            atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
        return report

    # Active prediction history is isolated by (season, team). Older seasons are
    # intentionally retained only as archived evidence and never enter a target
    # match's lineup probabilities.
    history_by_season_team: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    overlaps: list[int] = []
    jaccards: list[float] = []
    briers: list[float] = []
    predictions = 0
    seasons_evaluated: set[str] = set()
    for row in rows:
        key = (row["season"], row["team"])
        prior = [item for item in history_by_season_team[key] if item["kickoff"] < row["kickoff"]]
        if len(prior) >= MIN_HISTORY:
            forecast = _predict(prior)
            actual = set(row["starters"])
            probable = set(forecast["probable_xi"])
            overlap = len(actual & probable)
            overlaps.append(overlap)
            jaccards.append(overlap / max(1, len(actual | probable)))
            universe = set(forecast["probabilities"]) | actual
            briers.append(mean((forecast["probabilities"].get(player, 0.0) - (1.0 if player in actual else 0.0)) ** 2 for player in universe))
            predictions += 1
            seasons_evaluated.add(row["season"])
        history_by_season_team[key].append(row)

    sufficient = predictions >= MIN_VALIDATION_PREDICTIONS
    report = {
        "schema_version": "V4.6.3",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": "PROBABLE_LINEUP_ROUTE_VALIDATED" if sufficient else "INSUFFICIENT_LINEUP_VALIDATION_SAMPLE",
        "validated_for_a_grade": bool(sufficient),
        "prediction_count": predictions,
        "minimum_validation_predictions": MIN_VALIDATION_PREDICTIONS,
        "mean_top11_overlap": mean(overlaps) if overlaps else None,
        "mean_jaccard": mean(jaccards) if jaccards else None,
        "mean_player_brier": mean(briers) if briers else None,
        "lookback_matches": LOOKBACK,
        "decay": DECAY,
        "minimum_prior_lineups": MIN_HISTORY,
        "season_scope": "same_target_season_only",
        "seasons_evaluated": sorted(seasons_evaluated),
        "older_season_active_weight": 0.0,
        "point_in_time_policy": "Only lineups from the same target season with kickoff strictly earlier than the target kickoff are used as predictors.",
        "data_path": str(path.relative_to(ROOT)),
        "data_sha256": sha256_file(path),
        "implementation_sha256": sha256_file(SCRIPT_PATH),
        "governance_note": "validated_for_a_grade means the same-season point-in-time route has enough OOS lineup predictions; A-grade still requires all other CURRENT gates and does not assert that the lineup forecast is perfect.",
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
    return report


def run_all(*, write: bool = True) -> dict[str, Any]:
    reports = {}
    failures = []
    for item in load_registry()["competitions"]:
        competition_id = item["competition_id"]
        try:
            report = validate_competition(competition_id, write=write)
            reports[competition_id] = {
                "status": report["status"],
                "validated_for_a_grade": report["validated_for_a_grade"],
                "prediction_count": report["prediction_count"],
                "season_scope": report.get("season_scope"),
            }
        except Exception as exc:
            failures.append({"competition_id": competition_id, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.3",
        "generated_at_utc": utc_now(),
        "implementation_sha256": sha256_file(SCRIPT_PATH),
        "competition_count_requested": len(reports) + len(failures),
        "competition_count_failed": len(failures),
        "validated_route_count": sum(bool(item["validated_for_a_grade"]) for item in reports.values()),
        "active_season_scope": "same_target_season_only",
        "older_season_active_weight": 0.0,
        "reports": reports,
        "failures": failures,
    }
    if write:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"probable lineup route validation failed: {failures}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        manifest = run_all(write=not args.check_only)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
