#!/usr/bin/env python3
"""V6.49.3 constrained-optimal richest-data Full500.

After V6.49.1 and V6.49.2 showed that manual lexicographic/hard-threshold rules trade
one completeness family against another, this revision formulates sample construction
as a result-blind binary optimization problem.

Candidate pool:
- V6.36.1 2025/26 complete 71-feature intersection;
- complete preferred market packet required: early 1X2, early+closing OU2.5,
  early+closing AH, >=2 complete individual closing 1X2 books, and average-market
  versions of early1X2/OU/AH all present.

Choose exactly 500 matches subject to EACH selected-sample raw mean being >= the
corresponding mean in the original 1115-match complete intersection for:
1) individual closing bookmaker count,
2) minimum home/away valuation coverage,
3) expected-core/value overlap,
4) strict-prior current-season lineup-match depth,
5) unique prior starter depth,
6) valued-player count,
7) xG history depth,
8) panel history depth.

Among feasible selections, maximize the sum of within-pool percentile completeness
across all eight dimensions. Selection never reads result, score, market correctness,
model metric, or league accuracy. Identity is frozen before partition seed 649503.
Research only; CURRENT V5.0.1 unchanged; formal_weight=0.
"""
from __future__ import annotations

import bisect
import json
import random
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_full500_richest_data_v6490 as v0  # noqa: E402

TARGET = 500
FAST_N = 100
CONFIRM_N = 300
SEALED_N = 100
PARTITION_SEED = 649503
OUT_DIR = ROOT / "manifests" / "full500_v6493"
FEATURES_OUT = OUT_DIR / "full500_features_v6493.jsonl"
LABELS_OUT = OUT_DIR / "full500_development_labels_v6493.jsonl"
MANIFEST_OUT = ROOT / "manifests" / "v6_full500_constrained_optimal_v6493_status.json"

DIMS = (
    "individual_closing_book_count",
    "min_valuation_coverage",
    "min_expected_vs_value_overlap",
    "min_current_prior_match_count",
    "min_current_unique_starters",
    "min_valued_player_count",
    "xg_min_history_scaled",
    "panel_min_n_scaled",
)


def partition_name(i: int) -> str:
    if i < FAST_N:
        return "A_FAST100"
    if i < FAST_N + CONFIRM_N:
        return "B_CONFIRM300"
    return "C_SEALED100"


def percentile(sorted_values: list[float], x: float) -> float:
    left = bisect.bisect_left(sorted_values, x)
    right = bisect.bisect_right(sorted_values, x)
    return (left + right) / (2.0 * len(sorted_values))


def means(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {k: statistics.fmean(float(r["richness"][k]) for r in rows) for k in DIMS}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    eligible, audit, base_feature_names = v0.build_complete_intersection()
    if len(eligible) < TARGET:
        raise RuntimeError(f"eligible complete intersection below {TARGET}: {len(eligible)}")

    pool = [
        r for r in eligible
        if bool(r["richness"]["full_market_packet"])
        and int(r["richness"]["average_packet_count"]) == 3
        and int(r["richness"]["packet_count"]) == 4
    ]
    if len(pool) < TARGET:
        raise RuntimeError(f"preferred complete-market pool below {TARGET}: {len(pool)}")

    eligible_means = means(eligible)
    pool_means = means(pool)

    sorted_by_dim = {k: sorted(float(r["richness"][k]) for r in pool) for k in DIMS}
    q = np.zeros((len(pool), len(DIMS)), dtype=float)
    raw = np.zeros((len(pool), len(DIMS)), dtype=float)
    for i, r in enumerate(pool):
        for j, k in enumerate(DIMS):
            x = float(r["richness"][k])
            raw[i, j] = x
            q[i, j] = percentile(sorted_by_dim[k], x)

    # Equal-family completeness utility; deterministic tiny SHA tie utility is result-blind.
    utility = q.mean(axis=1)
    for i, r in enumerate(pool):
        tie = int(str(r["rank_tiebreak_sha256"])[:8], 16) / float(0xFFFFFFFF)
        utility[i] += tie * 1e-9

    # scipy.milp minimizes c, so use -utility.
    c = -utility
    integrality = np.ones(len(pool), dtype=int)
    bounds = Bounds(np.zeros(len(pool)), np.ones(len(pool)))

    # Exactly 500.
    constraints = [
        LinearConstraint(np.ones((1, len(pool))), np.array([TARGET], dtype=float), np.array([TARGET], dtype=float))
    ]
    # Eight non-inferiority constraints versus the ORIGINAL eligible1115 mean.
    for j, k in enumerate(DIMS):
        threshold = TARGET * float(eligible_means[k])
        constraints.append(
            LinearConstraint(raw[:, j][None, :], np.array([threshold], dtype=float), np.array([np.inf], dtype=float))
        )

    result = milp(
        c=c,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={"time_limit": 180.0, "mip_rel_gap": 0.0, "presolve": True},
    )

    solver_audit = {
        "success": bool(result.success),
        "status": int(result.status),
        "message": str(result.message),
        "fun": float(result.fun) if result.fun is not None else None,
        "mip_gap": float(getattr(result, "mip_gap", float("nan"))) if getattr(result, "mip_gap", None) is not None else None,
        "mip_node_count": int(getattr(result, "mip_node_count", -1)) if getattr(result, "mip_node_count", None) is not None else None,
    }
    if result.x is None:
        payload = {
            "schema_version": "V6.49.3-full500-constrained-optimal-r1",
            "status": "FAIL_NO_FEASIBLE_SOLUTION",
            "formal_current_version": "V5.0.1",
            "formal_weight": 0,
            "eligible": len(eligible),
            "preferred_complete_market_pool": len(pool),
            "eligible_means": eligible_means,
            "pool_means": pool_means,
            "solver": solver_audit,
            "result_used_for_selection": False,
        }
        MANIFEST_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError(f"V6.49.3 no feasible selection: {solver_audit}")

    chosen_idx = [i for i, x in enumerate(np.asarray(result.x)) if x > 0.5]
    if len(chosen_idx) != TARGET:
        raise RuntimeError(f"MILP returned {len(chosen_idx)} selected rows, expected {TARGET}")
    selected_identity_order = [pool[i] for i in chosen_idx]
    # Stable identity order independent of solver internals before partition shuffle.
    selected_identity_order.sort(key=lambda r: (r["competition_id"], r["date"], r["home_team"], r["away_team"]))

    selected_means = means(selected_identity_order)
    quality_gate = {
        k: {
            "eligible_mean": eligible_means[k],
            "pool_mean": pool_means[k],
            "selected_mean": selected_means[k],
            "delta_vs_eligible": selected_means[k] - eligible_means[k],
            "noninferior_vs_eligible": selected_means[k] + 1e-10 >= eligible_means[k],
        }
        for k in DIMS
    }
    if not all(x["noninferior_vs_eligible"] for x in quality_gate.values()):
        payload = {
            "schema_version": "V6.49.3-full500-constrained-optimal-r1",
            "status": "FAIL_POST_SOLVER_QUALITY_AUDIT",
            "formal_current_version": "V5.0.1",
            "formal_weight": 0,
            "quality_gate": quality_gate,
            "solver": solver_audit,
            "result_used_for_selection": False,
        }
        MANIFEST_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError(f"post-solver quality audit failed: {quality_gate}")

    identity_material = [
        {
            "competition_id": r["competition_id"], "season": r["season"], "date": r["date"],
            "home_team": r["home_team"], "away_team": r["away_team"],
        }
        for r in selected_identity_order
    ]
    identity_sha = v0.sha256_bytes(v0.canonical_json_bytes(identity_material))

    full = list(selected_identity_order)
    random.Random(PARTITION_SEED).shuffle(full)
    feature_lines = []
    label_lines = []
    sealed_hashes = []
    comp_counts = Counter()
    part_counts = Counter()
    for i, r in enumerate(full):
        part = partition_name(i)
        comp_counts[r["competition_id"]] += 1
        part_counts[part] += 1
        public = {
            "full_index": i, "partition": part,
            "competition_id": r["competition_id"], "season": r["season"], "date": r["date"],
            "home_team": r["home_team"], "away_team": r["away_team"],
            "base_features": r["base_features"], "player_features": r["player_features"],
            "market": r["market"], "formal": r["formal"],
            "home_player_context": r["home_player_context"], "away_player_context": r["away_player_context"],
            "richness": r["richness"],
        }
        feature_lines.append(json.dumps(public, ensure_ascii=False, sort_keys=True))
        label_payload = {
            "full_index": i, "partition": part,
            "label": r["label"], "actual_score": r["actual_score"],
        }
        if part != "C_SEALED100":
            label_lines.append(json.dumps(label_payload, ensure_ascii=False, sort_keys=True))
        else:
            sealed_hashes.append({
                "full_index": i,
                "identity": list(r["identity_key"]),
                "label_hash": v0.sha256_bytes(v0.canonical_json_bytes(label_payload)),
            })

    FEATURES_OUT.write_text("\n".join(feature_lines) + "\n", encoding="utf-8")
    LABELS_OUT.write_text("\n".join(label_lines) + "\n", encoding="utf-8")

    payload = {
        "schema_version": "V6.49.3-full500-constrained-optimal-r1",
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_RESULT_BLIND_CONSTRAINED_OPTIMAL_RICHEST_DATA_500",
        "selection_contract": {
            "season": v0.TEST_SEASON,
            "eligible_complete_intersection": len(eligible),
            "preferred_complete_market_pool": len(pool),
            "selected_count": TARGET,
            "selection_basis": "BINARY_CONSTRAINED_PREMATCH_DATA_COMPLETENESS_OPTIMIZATION",
            "identity_freeze_sha256": identity_sha,
            "partition_seed_after_identity_freeze": PARTITION_SEED,
            "fast100": FAST_N, "confirm300": CONFIRM_N, "sealed100": SEALED_N,
            "result_used_for_selection": False,
            "score_used_for_selection": False,
            "market_correctness_used_for_selection": False,
            "model_metric_used_for_selection": False,
            "league_accuracy_used_for_selection": False,
            "confidence_filtering": False,
            "post_result_league_dropping": False,
            "seed_replacement": False,
        },
        "hard_market_gate": {
            "full_market_packet_required": True,
            "average_packet_count_required": 3,
            "complete_market_subfamily_count_required": 4,
            "minimum_individual_closing_books": 2,
        },
        "optimization": {
            "dimensions": list(DIMS),
            "objective": "maximize equal-family mean percentile completeness",
            "constraints": "exactly500 + each raw selected mean >= original1115 eligible mean",
            "solver": solver_audit,
        },
        "quality_gate": quality_gate,
        "quality_gate_passed": True,
        "coverage": {
            "competition_counts": dict(sorted(comp_counts.items())),
            "partition_counts": dict(part_counts),
        },
        "feature_contract": {
            "base_feature_count": len(base_feature_names),
            "player_feature_count": len(v0.g0.PLAYER_FEATURE_NAMES),
            "core_numeric_feature_count": len(base_feature_names) + len(v0.g0.PLAYER_FEATURE_NAMES),
            "explicit_nonclaims": [
                "historical injury onsets lack per-row publication timestamps and do not rank strict completeness",
                "published historical expected-XI coverage is not complete",
                "retrospective market snapshots remain research-only",
            ],
        },
        "audits": audit,
        "artifacts": {
            "features_path": str(FEATURES_OUT.relative_to(ROOT)),
            "features_sha256": v0.sha256_bytes(FEATURES_OUT.read_bytes()),
            "development_labels_path": str(LABELS_OUT.relative_to(ROOT)),
            "development_labels_sha256": v0.sha256_bytes(LABELS_OUT.read_bytes()),
            "sealed100_label_hashes": sealed_hashes,
        },
        "governance": {
            "CURRENT_unchanged": True,
            "research_only": True,
            "V6490_V6491_V6492_superseded_for_new_research": True,
            "old_Gold500_not_deleted": True,
            "no_accuracy_test_run_by_builder": True,
            "B_CONFIRM300_open_only_after_new_A_FAST100_gate": True,
            "C_SEALED100_labels_not_emitted": True,
        },
    }
    MANIFEST_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "eligible": len(eligible),
        "preferred_complete_market_pool": len(pool),
        "selected": TARGET,
        "identity_freeze_sha256": identity_sha,
        "quality_gate": quality_gate,
        "competition_counts": dict(sorted(comp_counts.items())),
        "solver": solver_audit,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
