#!/usr/bin/env python3
"""V6.1.1 evaluator for the frozen V6.1.0 pristine forward test.

Reconstructs predictions for completed matches on or after the frozen forward start
date using only earlier match information, frozen formal parameters, frozen direct-model
coefficients and frozen execution thresholds. It never refits or changes thresholds.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import v6_direct_outcome_mvp_v600 as base
import v6_direct_outcome_draw_boundary_v601 as v601
from backtest_last_complete_season_all_domains_v470 import _actual_result, _predict_from_loaded_matches
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, atomic_write_json, derive_score_marginals, load_json, read_processed_matches

FREEZE = ROOT / "manifests" / "v6_pristine_forward_freeze_v610_status.json"
OUT = ROOT / "manifests" / "v6_pristine_forward_evaluation_v611_status.json"
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 611
Z90 = 1.6448536269514722


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wilson_lower(hits: int, count: int) -> float | None:
    if count <= 0:
        return None
    p = hits / count
    z2 = Z90 * Z90
    denominator = 1.0 + z2 / count
    center = p + z2 / (2.0 * count)
    spread = Z90 * math.sqrt((p * (1.0 - p) + z2 / (4.0 * count)) / count)
    return (center - spread) / denominator


def _arm_selected(item: dict[str, Any], arm: dict[str, Any]) -> bool:
    if int(item["agreement"]) != 1 or item["pick"] == "draw":
        return False
    direction = str(item["pick"])
    if not bool(arm.get(f"{direction}_enabled", False)):
        return False
    if "pooled_confidence_threshold" in arm:
        threshold = float(arm["pooled_confidence_threshold"])
    else:
        threshold = float(arm[f"{direction}_confidence_threshold"])
    return float(item["confidence"]) >= threshold


def _summary(rows: list[dict[str, Any]], total_forward: int) -> dict[str, Any]:
    hits = sum(int(row["hit"]) for row in rows)
    by_direction: dict[str, Any] = {}
    for direction in ("home", "away"):
        selected = [row for row in rows if row["pick"] == direction]
        direction_hits = sum(int(row["hit"]) for row in selected)
        by_direction[direction] = {
            "count": len(selected),
            "hits": direction_hits,
            "accuracy": direction_hits / len(selected) if selected else None,
            "wilson90_lower": _wilson_lower(direction_hits, len(selected)),
        }
    competitions = Counter(str(row["competition_id"]) for row in rows)
    return {
        "count": len(rows),
        "coverage": len(rows) / total_forward if total_forward else 0.0,
        "hits": hits,
        "accuracy": hits / len(rows) if rows else None,
        "wilson90_lower": _wilson_lower(hits, len(rows)),
        "competitions_represented": len(competitions),
        "by_direction": by_direction,
        "by_competition": {
            cid: {"count": count, "hits": sum(int(row["hit"]) for row in rows if row["competition_id"] == cid)}
            for cid, count in sorted(competitions.items())
        },
    }


def _bootstrap_difference(
    all_rows: list[dict[str, Any]],
    arm: dict[str, Any],
    benchmark: dict[str, Any],
) -> dict[str, Any] | None:
    if not all_rows:
        return None
    arm_rows = [row for row in all_rows if _arm_selected(row, arm)]
    benchmark_rows = [row for row in all_rows if _arm_selected(row, benchmark)]
    if not arm_rows or not benchmark_rows:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    samples: list[float] = []
    n = len(all_rows)
    for _ in range(BOOTSTRAP_REPS):
        sample = [all_rows[rng.randrange(n)] for _ in range(n)]
        a = [row for row in sample if _arm_selected(row, arm)]
        b = [row for row in sample if _arm_selected(row, benchmark)]
        if not a or not b:
            continue
        a_acc = sum(int(row["hit"]) for row in a) / len(a)
        b_acc = sum(int(row["hit"]) for row in b) / len(b)
        samples.append(a_acc - b_acc)
    if not samples:
        return None
    samples.sort()
    m = len(samples)
    return {
        "repetitions_requested": BOOTSTRAP_REPS,
        "repetitions_valid": m,
        "seed": BOOTSTRAP_SEED,
        "ci90": [samples[int(0.05 * (m - 1))], samples[int(0.95 * (m - 1))]],
        "ci95": [samples[int(0.025 * (m - 1))], samples[int(0.975 * (m - 1))]],
        "probability_arm_better": sum(1 for value in samples if value > 0.0) / m,
    }


def _integrity(freeze: dict[str, Any]) -> dict[str, Any]:
    expected = freeze["source_integrity"]
    actual = {
        "v600_code_sha256": _sha256(VALIDATION / "v6_direct_outcome_mvp_v600.py"),
        "v601_code_sha256": _sha256(VALIDATION / "v6_direct_outcome_draw_boundary_v601.py"),
        "v604_code_sha256": _sha256(VALIDATION / "v6_selective_direction_lcb_v604.py"),
        "v605_code_sha256": _sha256(VALIDATION / "v6_selective_asymmetric_lcb_v605.py"),
    }
    mismatches = {
        key: {"expected": expected.get(key), "actual": value}
        for key, value in actual.items()
        if expected.get(key) != value
    }
    return {"status": "PASS" if not mismatches else "FAIL", "mismatches": mismatches, "actual": actual}


def main() -> int:
    freeze = load_json(FREEZE)
    if freeze.get("status") != "PASS":
        raise PlatformError("V6.1.0 freeze receipt must be PASS")
    integrity = _integrity(freeze)
    if integrity["status"] != "PASS":
        payload = {
            "schema_version": "V6.1.1-pristine-forward-evaluation-r1",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "status": "FAIL_FROZEN_SOURCE_CHANGED",
            "integrity": integrity,
            "governance": {"automatic_promotion": False},
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    start_date = str(freeze["forward_start_date_utc"])
    frozen_model = freeze["frozen_probability_model"]
    models = frozen_model["models"]
    pool_weight = float(frozen_model["pool_weight"])
    draw_ratio = float(frozen_model["draw_ratio"])
    arms = freeze["frozen_arms"]
    domain_freeze = freeze["domain_freeze"]

    forward_rows: list[dict[str, Any]] = []
    skipped_prediction_errors: dict[str, int] = Counter()
    domain_forward_counts: dict[str, int] = Counter()

    for cid in sorted(domain_freeze):
        all_matches = sorted(read_processed_matches(cid), key=lambda match: (match.date, match.home_team, match.away_team))
        teams: dict[str, base.TeamState] = defaultdict(base.TeamState)
        competition = base.CompetitionState()
        by_date: dict[datetime, list[Any]] = defaultdict(list)
        for match in all_matches:
            by_date[match.date].append(match)
        params = domain_freeze[cid]["formal_selected_parameters"]
        temperature = float(domain_freeze[cid]["temperature"])

        for date in sorted(by_date):
            day_matches = sorted(by_date[date], key=lambda match: (match.home_team, match.away_team))
            if date.date().isoformat() >= start_date:
                for match in day_matches:
                    try:
                        matrix = _predict_from_loaded_matches(
                            all_matches,
                            match.home_team,
                            match.away_team,
                            match.date,
                            str(match.season),
                            params,
                        )
                        if abs(temperature - 1.0) > 1e-15:
                            matrix = temperature_scale_matrix(matrix, temperature)
                        margins = derive_score_marginals(matrix)
                        formal = {key: float(margins["1x2"][key]) for key in base.CLASSES}
                        home_state = teams[base._team_key(match.home_team)]
                        away_state = teams[base._team_key(match.away_team)]
                        draw_x, side_x = base._features(formal, matrix, home_state, away_state, competition, match.date)
                        row = {"formal": formal, "draw_x": draw_x, "side_x": side_x}
                        direct = base._direct_probability(row, models)
                        q = base._log_pool(formal, direct, pool_weight)
                        pick = v601._pick(q, draw_ratio)
                        formal_pick = max(base.CLASSES, key=lambda key: float(formal[key]))
                        ordered = sorted((float(q[key]), key) for key in base.CLASSES)
                        confidence = ordered[-1][0] - ordered[-2][0]
                        truth = _actual_result(int(match.home_goals), int(match.away_goals))
                        forward_rows.append({
                            "competition_id": cid,
                            "match_datetime": match.date.isoformat(),
                            "season": str(match.season),
                            "home_team": match.home_team,
                            "away_team": match.away_team,
                            "pick": pick,
                            "truth": truth,
                            "hit": int(pick == truth),
                            "agreement": int(pick == formal_pick),
                            "confidence": confidence,
                            "probabilities": q,
                        })
                        domain_forward_counts[cid] += 1
                    except Exception:
                        skipped_prediction_errors[cid] += 1
            for match in day_matches:
                base._update_state(
                    teams[base._team_key(match.home_team)],
                    teams[base._team_key(match.away_team)],
                    competition,
                    match,
                )

    summaries: dict[str, Any] = {}
    for name, arm in arms.items():
        selected = [row for row in forward_rows if _arm_selected(row, arm)]
        summaries[name] = _summary(selected, len(forward_rows))

    arm_a = summaries["arm_a_v605_asymmetric"]
    arm_b = summaries["arm_b_home_only"]
    benchmark = summaries["benchmark_v601_pooled_top5"]
    gates = freeze["forward_evaluation_gates"]
    minimums_met = (
        len(forward_rows) >= int(gates["minimum_completed_forward_matches"])
        and int(arm_a["count"]) >= int(gates["minimum_arm_a_selections"])
        and int(arm_b["count"]) >= int(gates["minimum_arm_b_selections"])
        and int(benchmark["count"]) >= int(gates["minimum_benchmark_selections"])
        and int(arm_a["competitions_represented"]) >= int(gates["minimum_competitions_represented"])
    )

    arm_a_bootstrap = _bootstrap_difference(forward_rows, arms["arm_a_v605_asymmetric"], arms["benchmark_v601_pooled_top5"])
    arm_b_bootstrap = _bootstrap_difference(forward_rows, arms["arm_b_home_only"], arms["benchmark_v601_pooled_top5"])
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

    if not forward_rows:
        evaluation_status = "PENDING_NO_FORWARD_MATCHES"
    elif not minimums_met:
        evaluation_status = "PENDING_MINIMUM_SAMPLE"
    elif promotion_gate_passed:
        evaluation_status = "FORWARD_GATE_PASS_REQUIRES_MANUAL_REVIEW"
    else:
        evaluation_status = "FORWARD_GATE_FAIL"

    payload = {
        "schema_version": "V6.1.1-pristine-forward-evaluation-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "evaluation_status": evaluation_status,
        "freeze_timestamp_utc": freeze["freeze_timestamp_utc"],
        "forward_start_date_utc": start_date,
        "integrity": integrity,
        "completed_forward_match_count": len(forward_rows),
        "domain_forward_match_counts": dict(sorted(domain_forward_counts.items())),
        "skipped_prediction_errors": dict(sorted(skipped_prediction_errors.items())),
        "arms": summaries,
        "arm_a_vs_benchmark_bootstrap": arm_a_bootstrap,
        "arm_b_vs_benchmark_bootstrap": arm_b_bootstrap,
        "minimum_sample_gate_met": minimums_met,
        "promotion_gate_passed": promotion_gate_passed,
        "promotion_gate_fail_reasons": fail_reasons,
        "governance": {
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
