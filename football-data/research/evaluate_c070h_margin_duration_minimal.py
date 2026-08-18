#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import numpy as np
import pandas as pd

import evaluate_c070f_first_two as f
import evaluate_c070g_state_duration_decomp as g

SCHEMA_VERSION = "C070H_MARGIN_DURATION_MINIMAL_V1"
FEATURES = {
    "markov": list(f.MARKOV_FEATURES),
    "static_margin_control": list(f.MARKOV_FEATURES) + ["is_margin1"],
    "margin_duration_candidate": list(f.MARKOV_FEATURES) + ["is_margin1", "duration_x_margin1"],
}


def _row_delta(frame: pd.DataFrame, p0: np.ndarray, p1: np.ndarray):
    y = frame.outcome.to_numpy(int)
    idx = np.arange(len(y))
    return -np.log(np.clip(p1[idx, y], 1e-15, 1.0)) + np.log(np.clip(p0[idx, y], 1e-15, 1.0))


def _subset_delta(frame: pd.DataFrame, p0: np.ndarray, p1: np.ndarray, mask):
    d = _row_delta(frame, p0, p1)
    m = np.asarray(mask, bool)
    return {"rows": int(m.sum()), "mean_delta_log_loss": float(d[m].mean()) if m.any() else None}


def run(db: Path, manifest_path: Path, out: Path):
    manifest = f.load_manifest(manifest_path)
    raw = f.load_first_two(db, manifest)
    if collections.Counter(raw.block) != {"warmup": 1202, "calibration": 1201}:
        raise RuntimeError("first-two payload drift")
    prematch = f.build_prematch(raw)
    minute, mismatches, multi = g._augmented_minute_rows(raw, prematch)
    structural = minute[minute.include_structural].copy().reset_index(drop=True)
    fold_specs = g._folds(structural)

    folds = {}
    pooled_rows = []
    pooled = {k: [] for k in FEATURES}
    wins_markov = 0
    wins_static = 0

    for name, train_end, test_start, test_end in fold_specs:
        train = structural[structural.date < train_end].copy()
        test = structural[structural.date >= test_start].copy()
        if test_end is not None:
            test = test[test.date < test_end].copy()
        models = {k: g._fit(train, feat) for k, feat in FEATURES.items()}
        probs = {k: models[k].predict_proba(test[FEATURES[k]]) for k in FEATURES}
        metrics = {k: g._metric(test, probs[k]) for k in FEATURES}
        d_m = float(metrics["margin_duration_candidate"]["log_loss"] - metrics["markov"]["log_loss"])
        d_s = float(metrics["margin_duration_candidate"]["log_loss"] - metrics["static_margin_control"]["log_loss"])
        wins_markov += int(d_m < 0)
        wins_static += int(d_s < 0)
        folds[name] = {
            "train_date_max_exclusive": str(train_end),
            "test_date_min_inclusive": str(test_start),
            "test_date_max_exclusive": str(test_end) if test_end is not None else None,
            "train_rows": int(len(train)), "test_rows": int(len(test)),
            "metrics": metrics,
            "candidate_minus_markov": d_m,
            "candidate_minus_static_margin_control": d_s,
        }
        pooled_rows.append(test)
        for k in FEATURES:
            pooled[k].append(probs[k])

    all_test = pd.concat(pooled_rows, ignore_index=True)
    p = {k: np.vstack(v) for k, v in pooled.items()}
    metrics = {k: g._metric(all_test, p[k]) for k in FEATURES}
    d_markov = float(metrics["margin_duration_candidate"]["log_loss"] - metrics["markov"]["log_loss"])
    d_static = float(metrics["margin_duration_candidate"]["log_loss"] - metrics["static_margin_control"]["log_loss"])
    boot_markov = g._bootstrap(all_test, p["markov"], p["margin_duration_candidate"], 7408)
    boot_static = g._bootstrap(all_test, p["static_margin_control"], p["margin_duration_candidate"], 7409)

    diagnostics = {
        "margin1_rows": _subset_delta(all_test, p["markov"], p["margin_duration_candidate"], all_test.is_margin1.to_numpy(float) > 0),
        "non_margin1_rows": _subset_delta(all_test, p["markov"], p["margin_duration_candidate"], all_test.is_margin1.to_numpy(float) == 0),
    }

    gate = bool(
        d_markov < 0
        and boot_markov["ci90_high"] < 0
        and wins_markov == 3
        and d_static < 0
        and boot_static["ci90_high"] < 0
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "C070H_POSTVIEW_REFINEMENT_COMPLETE",
        "verdict": "C070H_MARGIN_DURATION_NET_DEVELOPMENT_SIGNAL" if gate else "C070H_MARGIN_DURATION_NET_INCREMENT_NOT_ESTABLISHED",
        "identity": {"manifest_sha256": f.EXPECTED_MANIFEST_SHA, "warmup": 1202, "calibration": 1201, "confirmation": 1597},
        "payload_boundary": {
            "opened_blocks": ["warmup", "calibration"],
            "confirmation_payload_opened": False,
            "confirmation_score_rows_read": 0,
            "confirmation_event_rows_read": 0,
        },
        "parser": {
            "warmup_mismatch_ids": sorted(x["match_id"] for x in mismatches if x["block"] == "warmup"),
            "calibration_mismatch_count": int(sum(x["block"] == "calibration" for x in mismatches)),
            "multi_goal_bins_excluded": int(multi),
        },
        "folds": folds,
        "pooled_metrics": metrics,
        "primary": {
            "candidate_minus_markov": d_markov,
            "fold_wins_vs_markov": int(wins_markov),
            "bootstrap_vs_markov": boot_markov,
            "candidate_minus_static_margin_control": d_static,
            "fold_wins_vs_static_margin_control": int(wins_static),
            "bootstrap_vs_static_margin_control": boot_static,
            "development_gate": gate,
        },
        "diagnostics": diagnostics,
        "boundary": {
            "postview_hypothesis_refinement_only": True,
            "independent_evidence": False,
            "confirmation_scored": False,
            "fresh_confirmation_claim_allowed": False,
            "formal_weight": 0,
            "formal_promotion_allowed": False,
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    run(a.db, a.manifest, a.out)
