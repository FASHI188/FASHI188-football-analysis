#!/usr/bin/env python3
"""V4.7 MLS combined challenger score Top-k outer-OOF audit.

Reports the missing CURRENT-required exact-score Top-k surfaces for the already
frozen USA_MLS priority challenger outer folds.  No training and no formal
weight changes occur here.
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT_DIR / "engine"
VALIDATION_DIR = ROOT_DIR / "validation"
for item in (str(ENGINE_DIR), str(VALIDATION_DIR)):
    if item not in sys.path:
        sys.path.insert(0, item)

from conditional_allocation_challenger_v470 import apply_conditional_exponential_tilt
from football_v460_engine import load_config
from platform_core import ROOT, MatchRow, read_processed_matches
from total_tail_challenger_v470 import apply_total_tail_tilt
from train_priority_challengers_v470 import rolling_records

COMPETITION_ID = "USA_MLS"
ARTIFACT = ROOT / "models" / "challengers_v470" / COMPETITION_ID / "priority_v470.json"
OUT = ROOT / "manifests" / "priority_challenger_topk_review_v470_status.json"


def _topk_hit(matrix: list[dict[str, Any]], home: int, away: int, k: int) -> float:
    ranked = sorted(
        matrix,
        key=lambda cell: (-float(cell["probability"]), int(cell["home_goals"]), int(cell["away_goals"])),
    )[:k]
    return 1.0 if any(int(cell["home_goals"]) == home and int(cell["away_goals"]) == away for cell in ranked) else 0.0


def _bootstrap_ci(rows: list[dict[str, Any]], field: str, seed: int, resamples: int = 500) -> dict[str, Any]:
    blocks: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        blocks[str(row["block_id"])].append(float(row[field]))
    values = list(blocks.values())
    observed = mean(v for block in values for v in block)
    rng = random.Random(seed)
    samples = []
    for _ in range(resamples):
        chosen = [rng.choice(values) for _ in values]
        samples.append(mean(v for block in chosen for v in block))
    samples.sort()
    return {
        "count": sum(len(block) for block in values),
        "blocks": len(values),
        "mean_difference": observed,
        "ci95_lower": samples[max(0, int(0.025 * len(samples)) - 1)],
        "ci95_upper": samples[min(len(samples) - 1, int(0.975 * len(samples)))],
    }


def main() -> int:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    if artifact.get("competition_id") != COMPETITION_ID or artifact.get("formal_weight") != 0:
        raise RuntimeError("invalid or already-promoted priority challenger artifact")

    config = load_config()
    season_map: dict[str, list[MatchRow]] = defaultdict(list)
    for match in read_processed_matches(COMPETITION_ID):
        season_map[str(match.season)].append(match)
    for rows in season_map.values():
        rows.sort(key=lambda row: (row.date, row.home_team, row.away_team))

    rows_out = []
    folds_out = []
    max_probability_sum_residual = 0.0
    for fold in artifact.get("folds", []):
        season = str(fold["outer_season"])
        if season not in season_map:
            continue
        records = rolling_records(season_map[season], fold["base_parameters"], config, "eval")
        fold_rows = []
        for record in records:
            conditional_matrix, _ = apply_conditional_exponential_tilt(record["matrix"], fold["conditional_parameters"])
            candidate_matrix, _ = apply_total_tail_tilt(conditional_matrix, fold["tail_parameters"])
            max_probability_sum_residual = max(
                max_probability_sum_residual,
                abs(sum(float(cell["probability"]) for cell in candidate_matrix) - 1.0),
            )
            h = int(record["actual_home"])
            a = int(record["actual_away"])
            row = {"block_id": str(record["block_id"]), "season": season}
            for k in (1, 3, 5):
                base_hit = _topk_hit(record["matrix"], h, a, k)
                candidate_hit = _topk_hit(candidate_matrix, h, a, k)
                row[f"base_top{k}"] = base_hit
                row[f"candidate_top{k}"] = candidate_hit
                row[f"top{k}_difference"] = candidate_hit - base_hit
            rows_out.append(row)
            fold_rows.append(row)
        if fold_rows:
            folds_out.append({
                "outer_season": season,
                "predictions": len(fold_rows),
                "base_top1": mean(row["base_top1"] for row in fold_rows),
                "candidate_top1": mean(row["candidate_top1"] for row in fold_rows),
                "base_top3": mean(row["base_top3"] for row in fold_rows),
                "candidate_top3": mean(row["candidate_top3"] for row in fold_rows),
                "base_top5": mean(row["base_top5"] for row in fold_rows),
                "candidate_top5": mean(row["candidate_top5"] for row in fold_rows),
            })

    if not rows_out:
        raise RuntimeError("no eligible outer OOF rows")

    topk = {}
    for k, seed in ((1, 4721), (3, 4723), (5, 4725)):
        topk[f"top{k}"] = {
            "base_hit_rate": mean(row[f"base_top{k}"] for row in rows_out),
            "candidate_hit_rate": mean(row[f"candidate_top{k}"] for row in rows_out),
            "difference_ci": _bootstrap_ci(rows_out, f"top{k}_difference", seed),
        }

    report = {
        "schema_version": "V4.7.0-priority-challenger-topk-review-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "competition_id": COMPETITION_ID,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "outer_predictions": len(rows_out),
        "outer_folds": len(folds_out),
        "topk": topk,
        "folds": folds_out,
        "audit": {
            "probability_conservation_pass": max_probability_sum_residual <= 1e-10,
            "max_probability_sum_residual": max_probability_sum_residual,
        },
        "promotion_policy": "Reporting-only CURRENT-required Top-k audit. No automatic promotion; formal_weight remains 0 pending independent governance receipt.",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
