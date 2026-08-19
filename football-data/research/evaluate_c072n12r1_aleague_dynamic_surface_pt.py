#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

import evaluate_c072n12_aleague_dynamic_surface_pt as frozen


def read_event_level_development_targets(
    root: Path,
    authorized: set[tuple[str, str]],
) -> tuple[dict[tuple[str, str], int], dict[str, Any]]:
    valid_values: dict[tuple[str, str], set[int]] = defaultdict(set)
    cells_indexed = 0
    unavailable_cells = 0
    invalid_nonempty_cells = 0

    for season in frozen.DEV_SEASONS:
        path = root / f"A-League_{season}_All_Markets.csv"
        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            index = {name.strip(): i for i, name in enumerate(header)}
            event_idx = index["EVENT_ID"]
            target_idx = index["TOTAL_GOALS"]
            for row in reader:
                if len(row) < len(header):
                    continue
                event_id = row[event_idx].strip()
                key = (season, event_id)
                if key not in authorized:
                    continue
                # R1 reads only TOTAL_GOALS for already-authorized development identities.
                raw = row[target_idx].strip()
                cells_indexed += 1
                if raw == "":
                    unavailable_cells += 1
                    continue
                try:
                    x = float(raw)
                    xi = int(x)
                    if not math.isfinite(x) or x < 0 or abs(x - xi) > 1e-9:
                        raise ValueError(raw)
                except (ValueError, OverflowError):
                    invalid_nonempty_cells += 1
                    continue
                valid_values[key].add(xi)

    conflicting = {key: sorted(vals) for key, vals in valid_values.items() if len(vals) > 1}
    if conflicting:
        # Do not fit after a target-identity contradiction.
        raise RuntimeError(
            "event-level TOTAL_GOALS conflict before model fit: "
            + json.dumps({f"{k[0]}::{k[1]}": v for k, v in sorted(conflicting.items())}, sort_keys=True)
        )

    targets = {key: min(next(iter(vals)), 7) for key, vals in valid_values.items() if len(vals) == 1}
    missing = sorted(authorized - set(targets))
    return targets, {
        "authorized_development_events_before_target_availability": len(authorized),
        "development_total_goals_cells_indexed": cells_indexed,
        "blank_unavailable_total_goals_cells": unavailable_cells,
        "invalid_nonempty_total_goals_cells": invalid_nonempty_cells,
        "conflicting_event_level_targets": 0,
        "valid_development_event_targets": len(targets),
        "missing_target_development_events": len(missing),
        "missing_target_identities": [f"{season}::{event_id}" for season, event_id in missing],
        "usable_development_events_after_target_availability": len(targets),
        "target_values_2025_2026_read": 0,
        "forbidden_non_total_goals_outcome_values_read": 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", required=True)
    ap.add_argument("--out-summary", required=True)
    ap.add_argument("--out-predictions", required=True)
    args = ap.parse_args()
    root = Path(args.source_dir)

    # Reuse the frozen N12 zero-label feature builder byte-for-byte.
    feature_rows, source_meta = frozen.load_zero_label_features(root)
    authorized_dev = {key for key in feature_rows if key[0] in frozen.DEV_SEASONS}
    targets, target_meta = read_event_level_development_targets(root, authorized_dev)

    if target_meta["conflicting_event_level_targets"] != 0:
        raise RuntimeError(f"target identity conflict: {target_meta}")
    if target_meta["missing_target_development_events"] != 3:
        raise RuntimeError(f"unexpected target availability count: {target_meta}")
    if target_meta["valid_development_event_targets"] != 779:
        raise RuntimeError(f"unexpected usable development count: {target_meta}")

    # The three no-target events are excluded symmetrically, without replacement.
    usable_dev = set(targets)

    folds = [
        (("2020-2021",), "2021-2022"),
        (("2020-2021", "2021-2022"), "2022-2023"),
        (("2020-2021", "2021-2022", "2022-2023"), "2023-2024"),
        (("2020-2021", "2021-2022", "2022-2023", "2023-2024"), "2024-2025"),
    ]

    pooled_y: list[int] = []
    pooled_pb: list[np.ndarray] = []
    pooled_pc: list[np.ndarray] = []
    pooled_dates: list[str] = []
    prediction_rows: list[dict[str, Any]] = []
    fold_results = []

    for train_seasons, test_season in folds:
        train_keys = sorted(k for k in usable_dev if k[0] in train_seasons)
        test_keys = sorted(k for k in usable_dev if k[0] == test_season)
        xb_train = np.stack([feature_rows[k]["baseline"] for k in train_keys])
        xc_train = np.stack([feature_rows[k]["candidate"] for k in train_keys])
        y_train = np.array([targets[k] for k in train_keys], dtype=int)
        xb_test = np.stack([feature_rows[k]["baseline"] for k in test_keys])
        xc_test = np.stack([feature_rows[k]["candidate"] for k in test_keys])
        y_test = np.array([targets[k] for k in test_keys], dtype=int)

        # Reuse the frozen N12 estimator path byte-for-byte.
        pb = frozen.fit_predict(xb_train, y_train, xb_test)
        pc = frozen.fit_predict(xc_train, y_train, xc_test)
        mb = frozen.score_metrics(y_test, pb)
        mc = frozen.score_metrics(y_test, pc)
        dm = frozen.delta_metrics(mb, mc)
        fold_results.append({
            "train_seasons": list(train_seasons),
            "test_season": test_season,
            "train_n": len(train_keys),
            "test_n": len(test_keys),
            "train_class_counts": {str(k): int(v) for k, v in sorted(__import__("collections").Counter(y_train.tolist()).items())},
            "baseline": mb,
            "candidate": mc,
            "delta_candidate_minus_baseline": dm,
        })

        pooled_y.extend(y_test.tolist())
        pooled_pb.extend(pb)
        pooled_pc.extend(pc)
        for i, key in enumerate(test_keys):
            d = feature_rows[key]["event_date"]
            pooled_dates.append(d)
            row = {
                "season": key[0],
                "event_id": key[1],
                "event_date": d,
                "target_T": int(y_test[i]),
                "baseline_top1": int(np.argmax(pb[i])),
                "candidate_top1": int(np.argmax(pc[i])),
            }
            for cls in range(frozen.N_CLASSES):
                row[f"baseline_p{cls}"] = float(pb[i, cls])
                row[f"candidate_p{cls}"] = float(pc[i, cls])
            prediction_rows.append(row)

    y = np.array(pooled_y, dtype=int)
    pb = np.stack(pooled_pb)
    pc = np.stack(pooled_pc)
    mb = frozen.score_metrics(y, pb)
    mc = frozen.score_metrics(y, pc)
    dm = frozen.delta_metrics(mb, mc)
    boot = frozen.cluster_bootstrap_delta(y, pb, pc, pooled_dates)
    fold_wins = sum(f["delta_candidate_minus_baseline"]["log_loss"] < 0 for f in fold_results)
    max_resid = max(mb["probability_residual_max"], mc["probability_residual_max"])

    gates = {
        "pooled_dlogloss_lt_0": dm["log_loss"] < 0.0,
        "bootstrap90_upper_lt_0": boot["ci90_high"] < 0.0,
        "fold_logloss_wins_ge_3_of_4": fold_wins >= 3,
        "brier_nonworse": dm["brier"] <= 0.0,
        "rps_nonworse": dm["rps"] <= 0.0,
        "probability_residual_le_1e12": max_resid <= 1e-12,
        "reserve_2025_26_target_values_read_zero": target_meta["target_values_2025_2026_read"] == 0,
        "c073_c077_scientific_results_unused": True,
        "c070f_confirmation_unopened": True,
    }
    development_pass = all(gates.values())
    practical_breakthrough = development_pass and dm["log_loss"] <= -0.003
    if practical_breakthrough:
        terminal = "C072N12_BREAKTHROUGH_CANDIDATE"
    elif development_pass:
        terminal = "C072N12_STABLE_SMALL_INCREMENT"
    else:
        terminal = "C072N12_DYNAMIC_SURFACE_PT_DEVELOPMENT_PARK"

    summary = {
        "schema_version": "C072N12R1_ALEAGUE_DYNAMIC_SURFACE_PT_DEVELOPMENT_V1",
        "project": "football3",
        "execution_revision": "R1_TARGET_EXTRACTION_ENGINEERING_REPLAY",
        "scientific_contract_unchanged": True,
        "source_revision": frozen.SOURCE_REVISION,
        "classification": "NEW_DOMAIN_DEVELOPMENT",
        "target": "T=min(int(TOTAL_GOALS),7); classes 0..6,7+",
        "baseline_features": [f"z_2.5_T{m}" for m in frozen.SNAPSHOTS],
        "candidate_features": [f"z_{line}_T{m}" for line in frozen.PREFERRED for m in frozen.SNAPSHOTS],
        "estimator": {"scaler": "StandardScaler", "model": "LogisticRegression", "C": 0.1, "penalty": "l2", "solver": "lbfgs", "max_iter": 3000, "class_weight": None, "random_state": 0},
        "source_meta": source_meta,
        "target_access": target_meta,
        "development_oos_n": len(y),
        "folds": fold_results,
        "fold_logloss_wins": fold_wins,
        "pooled_baseline": mb,
        "pooled_candidate": mc,
        "pooled_delta_candidate_minus_baseline": dm,
        "paired_date_cluster_bootstrap_dlogloss": boot,
        "baseline_class_diagnostics": frozen.class_diagnostics(y, pb),
        "candidate_class_diagnostics": frozen.class_diagnostics(y, pc),
        "gates": gates,
        "development_pass": development_pass,
        "practical_breakthrough_threshold_dlogloss": -0.003,
        "practical_breakthrough_candidate": practical_breakthrough,
        "target_values_2025_26_read": 0,
        "c073_c077_scientific_results_used": False,
        "c070f_confirmation_opened": False,
        "max_probability_conservation_residual": max_resid,
        "terminal": terminal,
        "authorization": "N13_CONFIRMATION_ONLY_IF_BREAKTHROUGH_CANDIDATE; otherwise PARK and keep 2025-26 target unread",
    }

    out_summary = Path(args.out_summary)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out_predictions = Path(args.out_predictions)
    out_predictions.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(prediction_rows[0].keys()) if prediction_rows else []
    with out_predictions.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(prediction_rows)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
