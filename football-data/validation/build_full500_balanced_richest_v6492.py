#!/usr/bin/env python3
"""V6.49.2 balanced richest-data Full500 with an 8-book hard market gate.

V6.49.1 improved seven of eight completeness dimensions but sacrificed the mean number
of independent closing 1X2 books (7.492 vs eligible-pool 7.694). This revision fixes
that sole failure result-blindly by requiring >=8 complete individual closing-book
1X2 triplets before balanced selection.

All other governance is unchanged: no result, score, market correctness, model metric,
league accuracy, confidence filter, or seed shopping is used. Identities freeze before
100/300/100 partitioning. CURRENT V5.0.1 unchanged; formal_weight=0.
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

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_full500_richest_data_v6490 as v0  # noqa: E402

TARGET = 500
FAST_N = 100
CONFIRM_N = 300
SEALED_N = 100
PARTITION_SEED = 649502
MIN_BOOKS = 8

OUT_DIR = ROOT / "manifests" / "full500_v6492"
FEATURES_OUT = OUT_DIR / "full500_features_v6492.jsonl"
LABELS_OUT = OUT_DIR / "full500_development_labels_v6492.jsonl"
MANIFEST_OUT = ROOT / "manifests" / "v6_full500_balanced_richest_v6492_status.json"

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
BALANCE_DIMS = tuple(k for k in DIMS if k != "individual_closing_book_count")


def percentile_maps(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    return {k: sorted(float(r["richness"][k]) for r in rows) for k in BALANCE_DIMS}


def percentile(sorted_values: list[float], x: float) -> float:
    left = bisect.bisect_left(sorted_values, x)
    right = bisect.bisect_right(sorted_values, x)
    return (left + right) / (2.0 * len(sorted_values))


def partition_name(i: int) -> str:
    if i < FAST_N:
        return "A_FAST100"
    if i < FAST_N + CONFIRM_N:
        return "B_CONFIRM300"
    return "C_SEALED100"


def mean_map(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {k: statistics.fmean(float(r["richness"][k]) for r in rows) for k in DIMS}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    eligible, audit, base_feature_names = v0.build_complete_intersection()
    if len(eligible) < TARGET:
        raise RuntimeError(f"eligible complete intersection below {TARGET}: {len(eligible)}")

    rich_pool = [
        r for r in eligible
        if bool(r["richness"]["full_market_packet"])
        and int(r["richness"]["average_packet_count"]) == 3
        and int(r["richness"]["packet_count"]) == 4
        and int(r["richness"]["individual_closing_book_count"]) >= MIN_BOOKS
    ]
    if len(rich_pool) < TARGET:
        payload = {
            "schema_version": "V6.49.2-full500-balanced-richest-r1",
            "status": "FAIL_INSUFFICIENT_EIGHT_BOOK_POOL",
            "formal_current_version": "V5.0.1",
            "formal_weight": 0,
            "eligible": len(eligible),
            "eight_book_complete_market_pool": len(rich_pool),
            "required": TARGET,
            "result_used_for_selection": False,
        }
        MANIFEST_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError(f"8-book complete-market pool below {TARGET}: {len(rich_pool)}")

    maps = percentile_maps(rich_pool)
    scored = []
    for r in rich_pool:
        q = {k: percentile(maps[k], float(r["richness"][k])) for k in BALANCE_DIMS}
        z = dict(r)
        z["balanced_percentiles"] = q
        z["balanced_min_percentile"] = min(q.values())
        z["balanced_mean_percentile"] = statistics.fmean(q.values())
        scored.append(z)

    scored.sort(
        key=lambda r: (
            -float(r["balanced_min_percentile"]),
            -float(r["balanced_mean_percentile"]),
            -float(r["richness"]["individual_closing_book_count"]),
            -float(r["richness"]["min_current_prior_match_count"]),
            -float(r["richness"]["min_valuation_coverage"]),
            r["rank_tiebreak_sha256"],
        )
    )
    selected_identity_order = scored[:TARGET]

    eligible_means = mean_map(eligible)
    rich_pool_means = mean_map(rich_pool)
    selected_means = mean_map(selected_identity_order)
    quality_gate = {
        k: {
            "eligible_mean": eligible_means[k],
            "rich_pool_mean": rich_pool_means[k],
            "selected_mean": selected_means[k],
            "noninferior_vs_eligible": selected_means[k] + 1e-12 >= eligible_means[k],
        }
        for k in DIMS
    }
    gate_passed = all(v["noninferior_vs_eligible"] for v in quality_gate.values())
    if not gate_passed:
        payload = {
            "schema_version": "V6.49.2-full500-balanced-richest-r1",
            "status": "FAIL_BALANCED_QUALITY_GATE",
            "formal_current_version": "V5.0.1",
            "formal_weight": 0,
            "eligible": len(eligible),
            "eight_book_complete_market_pool": len(rich_pool),
            "quality_gate": quality_gate,
            "result_used_for_selection": False,
        }
        MANIFEST_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError(f"V6.49.2 quality gate failed: {quality_gate}")

    identity_material = [
        {
            "competition_id": r["competition_id"], "season": r["season"], "date": r["date"],
            "home_team": r["home_team"], "away_team": r["away_team"],
            "balanced_min_percentile": r["balanced_min_percentile"],
            "balanced_mean_percentile": r["balanced_mean_percentile"],
            "closing_book_count": r["richness"]["individual_closing_book_count"],
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
            "balanced_percentiles": r["balanced_percentiles"],
            "balanced_min_percentile": r["balanced_min_percentile"],
            "balanced_mean_percentile": r["balanced_mean_percentile"],
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
        "schema_version": "V6.49.2-full500-balanced-richest-r1",
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_RESULT_BLIND_EIGHT_BOOK_BALANCED_RICHEST_DATA_500",
        "selection_contract": {
            "season": v0.TEST_SEASON,
            "eligible_complete_intersection": len(eligible),
            "eight_book_complete_market_pool": len(rich_pool),
            "selected_count": TARGET,
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
            "minimum_complete_individual_closing_books": MIN_BOOKS,
        },
        "balanced_ranking": {
            "dimensions": list(BALANCE_DIMS),
            "primary": "maximize minimum percentile across non-book evidence dimensions",
            "secondary": "maximize mean percentile across non-book evidence dimensions",
            "selected_min_bottleneck_percentile": min(float(r["balanced_min_percentile"]) for r in selected_identity_order),
            "selected_mean_of_mean_percentile": statistics.fmean(float(r["balanced_mean_percentile"]) for r in selected_identity_order),
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
            "V6490_and_V6491_superseded_for_new_research": True,
            "old_Gold500_not_deleted": True,
            "no_accuracy_test_run_by_builder": True,
            "B_CONFIRM300_open_only_after_new_A_FAST100_gate": True,
            "C_SEALED100_labels_not_emitted": True,
        },
    }
    MANIFEST_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "eligible": len(eligible),
        "eight_book_complete_market_pool": len(rich_pool),
        "selected": TARGET,
        "identity_freeze_sha256": identity_sha,
        "quality_gate": quality_gate,
        "competition_counts": dict(sorted(comp_counts.items())),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
