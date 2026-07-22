#!/usr/bin/env python3
"""Ledger-native V6.1.1 evaluator for the frozen V6.1.0 forward test.

Unlike the original reconstruction evaluator, this version scores only predictions that
exist as pre-kickoff PREDICTION_FROZEN events and have a linked RESULT_SETTLED event in
the V6.1.2 hash-chain ledger. It never reconstructs a prediction after the match.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import v6_pristine_forward_ledger_v612 as ledgerlib
from platform_core import PlatformError, atomic_write_json, load_json, parse_iso_datetime, validate_probability_vector

FREEZE = ROOT / "manifests" / "v6_pristine_forward_freeze_v610_status.json"
LEDGER = ROOT / "forward" / "v6_pristine_forward_events_v612.json"
OUT = ROOT / "manifests" / "v6_pristine_forward_evaluation_v611_status.json"
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 611
Z90 = 1.6448536269514722
ARMS = ("arm_a_v605_asymmetric", "arm_b_home_only", "benchmark_v601_pooled_top5")


def _wilson_lower(hits: int, count: int) -> float | None:
    if count <= 0:
        return None
    p = hits / count
    z2 = Z90 * Z90
    denominator = 1.0 + z2 / count
    center = p + z2 / (2.0 * count)
    spread = Z90 * math.sqrt((p * (1.0 - p) + z2 / (4.0 * count)) / count)
    return (center - spread) / denominator


def _summary(rows: list[dict[str, Any]], arm: str, total_settled: int) -> dict[str, Any]:
    selected = [row for row in rows if bool(row["selected_arms"].get(arm, False))]
    hits = sum(int(row["hit"]) for row in selected)
    by_direction: dict[str, Any] = {}
    for direction in ("home", "away"):
        subset = [row for row in selected if row["pick"] == direction]
        direction_hits = sum(int(row["hit"]) for row in subset)
        by_direction[direction] = {
            "count": len(subset),
            "hits": direction_hits,
            "accuracy": direction_hits / len(subset) if subset else None,
            "wilson90_lower": _wilson_lower(direction_hits, len(subset)),
        }
    competitions = Counter(str(row["competition_id"]) for row in selected)
    return {
        "count": len(selected),
        "coverage": len(selected) / total_settled if total_settled else 0.0,
        "hits": hits,
        "accuracy": hits / len(selected) if selected else None,
        "wilson90_lower": _wilson_lower(hits, len(selected)),
        "competitions_represented": len(competitions),
        "by_direction": by_direction,
        "by_competition": {
            cid: {
                "count": count,
                "hits": sum(int(row["hit"]) for row in selected if row["competition_id"] == cid),
                "accuracy": (
                    sum(int(row["hit"]) for row in selected if row["competition_id"] == cid) / count
                    if count else None
                ),
            }
            for cid, count in sorted(competitions.items())
        },
    }


def _bootstrap(rows: list[dict[str, Any]], arm: str, benchmark: str) -> dict[str, Any] | None:
    arm_rows = [row for row in rows if row["selected_arms"].get(arm)]
    benchmark_rows = [row for row in rows if row["selected_arms"].get(benchmark)]
    if not rows or not arm_rows or not benchmark_rows:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    values: list[float] = []
    n = len(rows)
    for _ in range(BOOTSTRAP_REPS):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        a = [row for row in sample if row["selected_arms"].get(arm)]
        b = [row for row in sample if row["selected_arms"].get(benchmark)]
        if not a or not b:
            continue
        a_acc = sum(int(row["hit"]) for row in a) / len(a)
        b_acc = sum(int(row["hit"]) for row in b) / len(b)
        values.append(a_acc - b_acc)
    if not values:
        return None
    values.sort()
    m = len(values)
    return {
        "repetitions_requested": BOOTSTRAP_REPS,
        "repetitions_valid": m,
        "seed": BOOTSTRAP_SEED,
        "ci90": [values[int(0.05 * (m - 1))], values[int(0.95 * (m - 1))]],
        "ci95": [values[int(0.025 * (m - 1))], values[int(0.975 * (m - 1))]],
        "probability_arm_better": sum(1 for value in values if value > 0.0) / m,
    }


def _materialize(freeze: dict[str, Any], ledger: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], int]:
    predictions: dict[str, dict[str, Any]] = {}
    settlements: dict[str, dict[str, Any]] = {}
    for event in ledger.get("events", []):
        match_id = str(event.get("match_id") or "")
        if event.get("event_type") == "PREDICTION_FROZEN":
            predictions[match_id] = event
        elif event.get("event_type") == "RESULT_SETTLED":
            settlements[match_id] = event

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for match_id, settlement_event in sorted(settlements.items()):
        prediction_event = predictions.get(match_id)
        if prediction_event is None:
            errors.append(f"settlement without prediction: {match_id}")
            continue
        pred_payload = prediction_event.get("payload") or {}
        settlement_payload = settlement_event.get("payload") or {}
        identity = pred_payload.get("fixture_identity") or {}
        prediction = pred_payload.get("prediction") or {}
        result = settlement_payload.get("result") or {}
        try:
            kickoff = parse_iso_datetime(str(identity.get("kickoff_at") or ""), "kickoff_at")
            frozen_at = parse_iso_datetime(str(prediction_event.get("event_timestamp_utc") or ""), "prediction_event_timestamp")
            settled_at = parse_iso_datetime(str(settlement_event.get("event_timestamp_utc") or ""), "settlement_event_timestamp")
            if frozen_at >= kickoff:
                errors.append(f"prediction not frozen before kickoff: {match_id}")
            if settled_at < kickoff:
                errors.append(f"result settled before kickoff: {match_id}")
            if settlement_payload.get("prediction_event_hash") != prediction_event.get("event_hash"):
                errors.append(f"prediction event reference mismatch: {match_id}")

            probabilities = validate_probability_vector(
                prediction.get("pooled_probabilities") or {},
                ("home", "draw", "away"),
                field=f"{match_id}.pooled_probabilities",
            )
            home_goals = int(result["home_goals_90"])
            away_goals = int(result["away_goals_90"])
            actual = "home" if home_goals > away_goals else "away" if home_goals < away_goals else "draw"
            if actual != result.get("actual_result"):
                errors.append(f"settled score/result mismatch: {match_id}")
            item = {
                "pick": str(prediction.get("pick")),
                "agreement": int(bool(prediction.get("agreement"))),
                "confidence": float(prediction.get("confidence")),
            }
            expected_arms = ledgerlib._selected_arms(item, freeze["frozen_arms"])
            recorded_arms = {name: bool((prediction.get("selected_arms") or {}).get(name, False)) for name in ARMS}
            if expected_arms != recorded_arms:
                errors.append(f"recorded arm selection mismatch: {match_id}")
            rows.append({
                "match_id": match_id,
                "competition_id": identity["competition_id"],
                "season": identity["season"],
                "kickoff_at": identity["kickoff_at"],
                "home_team": identity["home_team"],
                "away_team": identity["away_team"],
                "pick": item["pick"],
                "truth": actual,
                "hit": int(item["pick"] == actual),
                "agreement": item["agreement"],
                "confidence": item["confidence"],
                "probabilities": probabilities,
                "selected_arms": recorded_arms,
                "prediction_event_hash": prediction_event["event_hash"],
                "settlement_event_hash": settlement_event["event_hash"],
            })
        except Exception as exc:
            errors.append(f"{match_id}: {type(exc).__name__}: {exc}")
    return rows, errors, len(predictions) - len(settlements)


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    freeze = load_json(FREEZE)
    if freeze.get("status") != "PASS":
        raise PlatformError("V6.1.0 freeze receipt must be PASS")
    ledger = load_json(LEDGER) if LEDGER.exists() else {"schema_version": ledgerlib.LEDGER_SCHEMA, "events": []}
    chain_audit = ledgerlib._audit_chain(ledger)
    source_integrity = ledgerlib._source_integrity(freeze)
    rows, semantic_errors, open_predictions = _materialize(freeze, ledger)
    integrity_status = "PASS" if chain_audit["status"] == "PASS" and source_integrity["status"] == "PASS" and not semantic_errors else "FAIL"
    if integrity_status != "PASS":
        payload = {
            "schema_version": "V6.1.1-pristine-forward-evaluation-r2-ledger-native",
            "generated_at_utc": generated.isoformat(),
            "status": "FAIL_LEDGER_INTEGRITY",
            "evaluation_status": "BLOCKED",
            "ledger_chain_audit": chain_audit,
            "frozen_source_integrity": source_integrity,
            "semantic_errors": semantic_errors,
            "governance": {"automatic_promotion": False, "current_rule_change": False},
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    summaries = {arm: _summary(rows, arm, len(rows)) for arm in ARMS}
    arm_a = summaries["arm_a_v605_asymmetric"]
    arm_b = summaries["arm_b_home_only"]
    benchmark = summaries["benchmark_v601_pooled_top5"]
    gates = freeze["forward_evaluation_gates"]
    minimums_met = (
        len(rows) >= int(gates["minimum_completed_forward_matches"])
        and int(arm_a["count"]) >= int(gates["minimum_arm_a_selections"])
        and int(arm_b["count"]) >= int(gates["minimum_arm_b_selections"])
        and int(benchmark["count"]) >= int(gates["minimum_benchmark_selections"])
        and int(arm_a["competitions_represented"]) >= int(gates["minimum_competitions_represented"])
    )
    arm_a_bootstrap = _bootstrap(rows, "arm_a_v605_asymmetric", "benchmark_v601_pooled_top5")
    arm_b_bootstrap = _bootstrap(rows, "arm_b_home_only", "benchmark_v601_pooled_top5")
    fail_reasons: list[str] = []
    promotion_gate_passed = False
    if minimums_met:
        if arm_a["accuracy"] is None or benchmark["accuracy"] is None or float(arm_a["accuracy"]) < float(benchmark["accuracy"]):
            fail_reasons.append("arm A accuracy below benchmark")
        if arm_a["wilson90_lower"] is None or float(arm_a["wilson90_lower"]) < float(gates["arm_a_primary"]["wilson90_lower_minimum"]):
            fail_reasons.append("arm A Wilson 90% lower bound below gate")
        if arm_a_bootstrap is None or float(arm_a_bootstrap["ci90"][0]) < float(gates["arm_a_primary"]["paired_bootstrap90_lower_minimum"]):
            fail_reasons.append("arm A bootstrap lower bound below gate")
        if arm_b["wilson90_lower"] is None or float(arm_b["wilson90_lower"]) < float(gates["arm_b_secondary"]["wilson90_lower_minimum"]):
            fail_reasons.append("arm B Wilson 90% lower bound below gate")
        if arm_b_bootstrap is None or float(arm_b_bootstrap["ci90"][0]) < float(gates["arm_b_secondary"]["paired_bootstrap90_lower_minimum"]):
            fail_reasons.append("arm B bootstrap lower bound below gate")
        promotion_gate_passed = not fail_reasons

    if not rows:
        evaluation_status = "PENDING_NO_SETTLED_FORWARD_PREDICTIONS"
    elif not minimums_met:
        evaluation_status = "PENDING_MINIMUM_SAMPLE"
    elif promotion_gate_passed:
        evaluation_status = "FORWARD_GATE_PASS_REQUIRES_MANUAL_REVIEW"
    else:
        evaluation_status = "FORWARD_GATE_FAIL"

    payload = {
        "schema_version": "V6.1.1-pristine-forward-evaluation-r2-ledger-native",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "evaluation_status": evaluation_status,
        "freeze_timestamp_utc": freeze["freeze_timestamp_utc"],
        "forward_start_date_utc": freeze["forward_start_date_utc"],
        "ledger_chain_audit": chain_audit,
        "frozen_source_integrity": source_integrity,
        "semantic_errors": semantic_errors,
        "completed_forward_match_count": len(rows),
        "open_prediction_count": open_predictions,
        "arms": summaries,
        "arm_a_vs_benchmark_bootstrap": arm_a_bootstrap,
        "arm_b_vs_benchmark_bootstrap": arm_b_bootstrap,
        "minimum_sample_gate_met": minimums_met,
        "promotion_gate_passed": promotion_gate_passed,
        "promotion_gate_fail_reasons": fail_reasons,
        "governance": {
            "ledger_native_pre_match_predictions_only": True,
            "postmatch_prediction_reconstruction": False,
            "frozen_forward_evaluation_only": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "manual_review_required_even_if_gate_passes": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
