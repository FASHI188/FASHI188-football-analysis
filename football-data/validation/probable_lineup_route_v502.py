#!/usr/bin/env python3
"""V5.0.2 timestamp-safe probable-XI shadow validator.

Only observed starting-XI labels are accepted. A prior lineup may inform a target
prediction only when it belongs to the same competition/season/team, its match
kickoff is earlier than the target freeze, and its source observation timestamp
is strictly earlier than the target freeze.

This validator scores lineup prediction quality only. It has formal weight 0 and
cannot alter football probabilities without later competition-specific OOF and
a hash-bound promotion receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
DATA_ROOT = FOOTBALL / "lineups"
REGISTRY = FOOTBALL / "config" / "platform_registry.json"
REPORT_ROOT = FOOTBALL / "validation" / "reports" / "probable_lineup_v502"
MANIFEST_PATH = FOOTBALL / "manifests" / "probable_lineup_v502_status.json"
SCRIPT_PATH = Path(__file__).resolve()

LOOKBACK = 8
DECAY = 0.78
MIN_HISTORY = 3
MIN_VALIDATION_PREDICTIONS = 200
OBSERVED_LABEL_TYPES = {
    "observed_starting_xi",
    "confirmed_starting_xi",
    "actual_starting_xi",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_iso(value: Any, field: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        raise ValueError(f"missing {field}")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def load_registry() -> dict[str, Any]:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def load_rows(competition_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    path = DATA_ROOT / competition_id / "historical_lineups.jsonl"
    if not path.is_file():
        return [], []
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
            if str(row.get("competition_id") or "") != competition_id:
                raise ValueError("competition_id mismatch")
            if str(row.get("label_type") or "").strip().lower() not in OBSERVED_LABEL_TYPES:
                raise ValueError("non-observed label_type")
            starters = [str(item).strip() for item in row.get("starters") or []]
            if len(starters) != 11 or len(set(starters)) != 11 or not all(starters):
                raise ValueError("starters must be 11 unique non-empty identifiers")
            normalized = dict(row)
            normalized["starters"] = starters
            normalized["kickoff"] = parse_iso(row.get("kickoff_utc"), "kickoff_utc")
            normalized["source_observed_at"] = parse_iso(
                row.get("source_observed_at_utc"), "source_observed_at_utc"
            )
            normalized["season"] = str(row.get("season") or "").strip()
            normalized["team"] = str(row.get("team_source_id") or row.get("team") or "").strip()
            normalized["fixture_id"] = str(row.get("fixture_id") or "").strip()
            if not normalized["season"] or not normalized["team"] or not normalized["fixture_id"]:
                raise ValueError("missing season/team/fixture_id")
            rows.append(normalized)
        except Exception as exc:
            errors.append(f"line_{line_number}:{exc}")
    rows.sort(key=lambda item: (item["kickoff"], item["source_observed_at"], item["team"]))
    return rows, errors


def predict(prior: list[dict[str, Any]]) -> dict[str, Any]:
    prior = prior[-LOOKBACK:]
    players = sorted({player for row in prior for player in row["starters"]})
    raw = {player: 0.0 for player in players}
    total_weight = 0.0
    for age, row in enumerate(reversed(prior)):
        weight = DECAY ** age
        total_weight += weight
        starters = set(row["starters"])
        for player in players:
            if player in starters:
                raw[player] += weight
    probabilities = {
        player: raw[player] / max(total_weight, 1e-12) for player in players
    }
    ranking = sorted(probabilities, key=lambda player: (-probabilities[player], player))
    return {"probabilities": probabilities, "probable_xi": ranking[:11]}


def validate_competition(competition_id: str, *, write: bool) -> dict[str, Any]:
    rows, row_errors = load_rows(competition_id)
    path = DATA_ROOT / competition_id / "historical_lineups.jsonl"
    if not rows:
        report = {
            "schema_version": "V5.0.2-probable-lineup-shadow-r1",
            "generated_at_utc": utc_now(),
            "competition_id": competition_id,
            "status": "LINEUP_ROUTE_DATA_UNAVAILABLE" if not path.is_file() else "LINEUP_DATA_UNUSABLE",
            "validated_for_shadow_training": False,
            "prediction_count": 0,
            "row_error_count": len(row_errors),
            "row_error_examples": row_errors[:25],
            "data_path": path.relative_to(ROOT).as_posix(),
            "implementation_sha256": sha256_file(SCRIPT_PATH),
            "formal_weight": 0,
        }
        if write:
            atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
        return report

    history: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    overlaps: list[int] = []
    jaccards: list[float] = []
    briers: list[float] = []
    predictions = 0
    seasons: set[str] = set()
    rows_blocked_by_observation_time = 0

    for row in rows:
        freeze = row["kickoff"]
        key = (row["season"], row["team"])
        all_prior_kickoffs = [item for item in history[key] if item["kickoff"] < freeze]
        prior = [
            item
            for item in all_prior_kickoffs
            if item["source_observed_at"] < freeze
        ]
        rows_blocked_by_observation_time += len(all_prior_kickoffs) - len(prior)
        if len(prior) >= MIN_HISTORY:
            forecast = predict(prior)
            actual = set(row["starters"])
            probable = set(forecast["probable_xi"])
            overlap = len(actual & probable)
            overlaps.append(overlap)
            jaccards.append(overlap / max(1, len(actual | probable)))
            universe = set(forecast["probabilities"]) | actual
            briers.append(mean(
                (forecast["probabilities"].get(player, 0.0) - (1.0 if player in actual else 0.0)) ** 2
                for player in universe
            ))
            predictions += 1
            seasons.add(row["season"])
        history[key].append(row)

    sufficient = predictions >= MIN_VALIDATION_PREDICTIONS and not row_errors
    report = {
        "schema_version": "V5.0.2-probable-lineup-shadow-r1",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": "PROBABLE_LINEUP_SHADOW_VALIDATED" if sufficient else "INSUFFICIENT_OR_UNSAFE_LINEUP_VALIDATION",
        "validated_for_shadow_training": bool(sufficient),
        "prediction_count": predictions,
        "minimum_validation_predictions": MIN_VALIDATION_PREDICTIONS,
        "mean_top11_overlap": mean(overlaps) if overlaps else None,
        "mean_jaccard": mean(jaccards) if jaccards else None,
        "mean_player_brier": mean(briers) if briers else None,
        "lookback_matches": LOOKBACK,
        "decay": DECAY,
        "minimum_prior_lineups": MIN_HISTORY,
        "seasons_evaluated": sorted(seasons),
        "row_error_count": len(row_errors),
        "row_error_examples": row_errors[:25],
        "prior_rows_blocked_by_source_observation_time": rows_blocked_by_observation_time,
        "point_in_time_policy": "Same competition/season/team; prior kickoff and source_observed_at_utc must both be strictly earlier than target freeze.",
        "observed_label_policy": "Predicted/probable XI rows are rejected from the observed-label store.",
        "data_path": path.relative_to(ROOT).as_posix(),
        "data_sha256": sha256_file(path),
        "implementation_sha256": sha256_file(SCRIPT_PATH),
        "formal_weight": 0,
        "probability_change": False,
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
    return report


def run_all(*, write: bool) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    for item in load_registry().get("competitions", []):
        competition_id = str(item.get("competition_id") or "")
        if not competition_id:
            continue
        try:
            report = validate_competition(competition_id, write=write)
            reports[competition_id] = {
                "status": report["status"],
                "validated_for_shadow_training": report["validated_for_shadow_training"],
                "prediction_count": report["prediction_count"],
                "mean_top11_overlap": report.get("mean_top11_overlap"),
                "mean_jaccard": report.get("mean_jaccard"),
                "mean_player_brier": report.get("mean_player_brier"),
                "row_error_count": report.get("row_error_count"),
            }
        except Exception as exc:
            failures.append({"competition_id": competition_id, "error": str(exc)})

    manifest = {
        "schema_version": "V5.0.2-probable-lineup-shadow-aggregate-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS" if not failures else "FAIL",
        "competition_count_requested": len(reports) + len(failures),
        "competition_count_failed": len(failures),
        "validated_shadow_route_count": sum(
            bool(item["validated_for_shadow_training"]) for item in reports.values()
        ),
        "reports": reports,
        "failures": failures,
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Lineup-prediction shadow validation only; no football probability influence.",
    }
    if write:
        atomic_write_json(MANIFEST_PATH, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    manifest = run_all(write=not args.check_only)
    if args.print_summary:
        print(json.dumps({
            "status": manifest["status"],
            "validated_shadow_route_count": manifest["validated_shadow_route_count"],
            "competition_count_failed": manifest["competition_count_failed"],
            "reports": manifest["reports"],
        }, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
