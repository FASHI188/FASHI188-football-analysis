#!/usr/bin/env python3
"""Post-view diagnostic of existing frozen multi-line total ladders.

The representation is learned from market CDF geometry only. Labels are used only after
PCA orientation is frozen to report descriptive 4+ discrimination. No selector, threshold,
or football model is fitted and no network access is performed.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "manifests" / "v6_special_market_forward_eval_v6211_status.json"
OUT_DIR = ROOT / "manifests" / "existing_multiline_geometry_r1"
LINES = (1.5, 2.5, 3.5, 4.5)


def byline_cdf(row: dict, line: float) -> float | None:
    raw = ((row.get("total_ladder") or {}).get("byline") or {})
    for key, value in raw.items():
        try:
            if abs(float(key) - line) <= 1e-9:
                return float(value["cdf_under"])
        except (TypeError, ValueError, KeyError):
            continue
    return None


def auc(y: np.ndarray, score: np.ndarray) -> float | None:
    pos = score[y == 1]
    neg = score[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    wins = 0.0
    for p in pos:
        wins += float(np.sum(p > neg)) + 0.5 * float(np.sum(p == neg))
    return wins / float(len(pos) * len(neg))


def bootstrap_auc(y: np.ndarray, score: np.ndarray, seed: int = 6211, draws: int = 5000) -> dict:
    rng = np.random.default_rng(seed)
    vals = []
    n = len(y)
    for _ in range(draws):
        idx = rng.integers(0, n, size=n)
        a = auc(y[idx], score[idx])
        if a is not None:
            vals.append(a)
    arr = np.asarray(vals, dtype=float)
    return {
        "valid_draws": int(len(arr)),
        "p05": float(np.quantile(arr, 0.05)),
        "median": float(np.quantile(arr, 0.50)),
        "p95": float(np.quantile(arr, 0.95)),
    }


def main() -> int:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    assert source["classification"] == "DERIVED_ONLY_FROM_IMMUTABLE_PREMATCH_RAW_AT_FROZEN_PREDICTION_TIME"
    assert source["governance"]["network_refetch"] is False
    assert source["governance"]["postmatch_odds_used"] is False

    ladder_rows = [r for r in source["rows"] if r.get("total_ladder")]
    coverage = {}
    for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5):
        coverage[str(line)] = int(sum(byline_cdf(r, line) is not None for r in ladder_rows))

    core_rows = []
    cdfs = []
    for row in ladder_rows:
        vals = [byline_cdf(row, line) for line in LINES]
        if all(v is not None for v in vals):
            core_rows.append(row)
            cdfs.append([float(v) for v in vals])
    A = np.asarray(cdfs, dtype=float)
    if len(A) < 4:
        raise RuntimeError("INSUFFICIENT_CORE_MULTILINE_ROWS")
    if np.any(np.diff(A, axis=1) < -1e-6):
        raise RuntimeError("NON_MONOTONE_CORE_CDF")

    mu = A.mean(axis=0)
    sd = A.std(axis=0, ddof=1)
    if np.any(sd <= 0):
        raise RuntimeError("DEGENERATE_CDF_AXIS")
    Z = (A - mu) / sd
    corr = np.corrcoef(A, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(np.cov(Z, rowvar=False))
    order = np.argsort(eigvals)[::-1]
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]
    shares = eigvals / eigvals.sum()

    # Deterministic, label-free orientations.
    location_vec = eigvecs[:, 0].copy()
    if float(np.mean(location_vec)) > 0:
        location_vec *= -1.0  # higher score => lower CDF => higher-total location
    shape_vec = eigvecs[:, 1].copy()
    if float(shape_vec[0] - shape_vec[-1]) < 0:
        shape_vec *= -1.0  # higher score => more low-tail and high-tail mass => broader/fatter tails

    settled_idx = [i for i, r in enumerate(core_rows) if r.get("settled") and r.get("actual")]
    settled = [core_rows[i] for i in settled_idx]
    SZ = Z[settled_idx]
    SA = A[settled_idx]
    y4 = np.asarray([int(int(r["actual"]["total_bucket"]) >= 4) for r in settled], dtype=int)
    location_score = SZ @ location_vec
    fat_tail_score = SZ @ shape_vec
    over25 = 1.0 - SA[:, 1]
    high4 = 1.0 - SA[:, 2]
    high5 = 1.0 - SA[:, 3]

    eps = 1e-15
    p = np.clip(high4, eps, 1.0 - eps)
    high4_logloss = float(np.mean(-(y4 * np.log(p) + (1 - y4) * np.log(1 - p))))
    high4_brier = float(np.mean((p - y4) ** 2))

    metrics = {}
    for name, score in {
        "location_pc1": location_score,
        "fat_tail_pc2": fat_tail_score,
        "over25_proxy": over25,
        "high4_exact_from_ou35": high4,
        "high5_from_ou45": high5,
    }.items():
        metrics[name] = {
            "auc_for_realized_4plus": auc(y4, score),
            "bootstrap_auc": bootstrap_auc(y4, score),
        }

    masses = np.column_stack([
        A[:, 0],
        A[:, 1] - A[:, 0],
        A[:, 2] - A[:, 1],
        A[:, 3] - A[:, 2],
        1.0 - A[:, 3],
    ])

    result = {
        "schema_version": "existing-multiline-geometry-r1",
        "status": "POSTVIEW_EXISTING_MULTILINE_GEOMETRY_COMPLETE",
        "source": {
            "prediction_count": int(source["summary"]["prediction_count"]),
            "settled_count": int(source["summary"]["settled_count"]),
            "ladder_rows": int(len(ladder_rows)),
            "line_coverage": coverage,
            "core_1p5_2p5_3p5_4p5_rows": int(len(core_rows)),
            "core_settled_rows": int(len(settled)),
            "core_settled_realized_4plus": int(y4.sum()),
        },
        "label_free_geometry": {
            "cdf_lines": list(LINES),
            "cdf_correlation_matrix": corr.tolist(),
            "standardized_pca_variance_share": shares.tolist(),
            "location_pc1_loadings": location_vec.tolist(),
            "fat_tail_pc2_loadings": shape_vec.tolist(),
            "five_mass_mean": masses.mean(axis=0).tolist(),
            "five_mass_std": masses.std(axis=0, ddof=1).tolist(),
            "five_mass_order": ["T_le_1", "T_eq_2", "T_eq_3", "T_eq_4", "T_ge_5"],
        },
        "settled_descriptive_only": {
            "realized_4plus_rate": float(y4.mean()),
            "high4_exact_brier": high4_brier,
            "high4_exact_logloss": high4_logloss,
            "scores": metrics,
        },
        "interpretation_boundary": {
            "labels_used_for_pca_or_orientation": False,
            "labels_used_only_for_postview_descriptive_scoring": True,
            "selector_fit": False,
            "threshold_search": False,
            "network_refetch": False,
            "new_data_collection": False,
            "formal_weight": 0,
            "promotion_allowed": False,
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "status.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
