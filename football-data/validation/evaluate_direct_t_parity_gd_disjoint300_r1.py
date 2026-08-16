#!/usr/bin/env python3
"""Disjoint 300-match database pressure test for the existing Direct-T parity stack.

This deliberately does NOT invent a new model. It asks a simpler question first:
does the current parity-hierarchical Direct-T + existing conditional-GD behavior
replicate on 300 identities disjoint from the parent fixed500?

Identity selection is label-blind: sort the same rolling test-position-3 pool by
the established SHA-256 identity hash, reserve ranks 1..500 for the parent test,
and use ranks 501..800 here. Labels are attached only after identities freeze.
Latest rolling position 4 remains unopened.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import evaluate_direct_t_parity_gd_fixed500_r1 as base

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "direct_t_parity_gd_disjoint300_r1.json"
OUT = ROOT / "manifests" / "direct_t_parity_gd_disjoint300_r1_status.json"
ROWS_OUT = ROOT / "manifests" / "direct_t_parity_gd_disjoint300_r1_rows.csv"
PARENT_N = 500
_parent_hash = None


def _digest(identities) -> str:
    return hashlib.sha256(("\n".join(sorted(str(x) for x in identities)) + "\n").encode("utf-8")).hexdigest()


def sample_disjoint300(test: pd.DataFrame, n: int):
    global _parent_hash
    if len(test) < PARENT_N + n:
        raise base.ResearchError(f"target fold has only {len(test)} rows; need at least {PARENT_N + n}")
    ranked = test.copy()
    ranked["match_identity"] = ranked.apply(base.row_identity, axis=1)
    ranked["identity_hash"] = ranked["match_identity"].map(base.identity_hash)
    ranked = ranked.sort_values(["identity_hash", "match_identity"]).reset_index(drop=True)
    parent = ranked.iloc[:PARENT_N].copy()
    sample = ranked.iloc[PARENT_N:PARENT_N + n].copy()
    if sample["match_identity"].nunique() != n:
        raise base.ResearchError("disjoint300 identity uniqueness failure")
    overlap = len(set(parent.match_identity) & set(sample.match_identity))
    if overlap != 0:
        raise base.ResearchError(f"disjoint300 overlaps parent fixed500: {overlap}")
    _parent_hash = _digest(parent.match_identity)
    return sample, _digest(sample.match_identity)


def add_pressure_diagnostics(result: dict) -> dict:
    rows = pd.read_csv(ROWS_OUT)
    actual_t = rows["actual_total"].to_numpy(int)
    actual_tc = np.minimum(actual_t, 7)
    flat = rows["flat_pred_total_class"].to_numpy(int)
    hier = rows["hier_pred_total_class"].to_numpy(int)
    parity = rows["hier_pred_parity"].to_numpy(int)
    even = (actual_t % 2) == 0
    draws = rows["actual_result"].astype(str).to_numpy() == "D"

    def t_diag(pred):
        return {
            "exact_total_class_top1": float(np.mean(pred == actual_tc)),
            "within_one_goal_class_top1": float(np.mean(np.abs(pred - actual_tc) <= 1)),
            "mean_absolute_total_class_error": float(np.mean(np.abs(pred - actual_tc))),
            "T4plus_actual_rows": int(np.sum(actual_t >= 4)),
            "T4plus_predicted_rows": int(np.sum(pred >= 4)),
            "T4plus_recall": float(np.mean(pred[actual_t >= 4] >= 4)) if np.any(actual_t >= 4) else None,
            "draw_subset_exact_total_top1": float(np.mean(pred[draws] == actual_tc[draws])) if np.any(draws) else None,
        }

    result["sample"]["parent_fixed500_identity_sha256_reproduced"] = _parent_hash
    result["sample"]["overlap_with_parent_fixed500"] = 0
    result["sample"]["hash_rank_slice_one_based"] = [501, 800]
    result["pressure_diagnostics"] = {
        "actual_total_mean": float(np.mean(actual_t)),
        "actual_T4plus_rate": float(np.mean(actual_t >= 4)),
        "actual_even_rate": float(np.mean(even)),
        "actual_draw_rate": float(np.mean(draws)),
        "parity_top1_accuracy": float(np.mean(parity == (actual_t % 2))),
        "flat_total": t_diag(flat),
        "hierarchical_total": t_diag(hier),
    }
    return result


def main() -> None:
    base.EXPERIMENT_CONFIG = CONFIG
    base.OUT = OUT
    base.ROWS_OUT = ROWS_OUT
    base.sample_fixed_n = sample_disjoint300
    result = base.run()
    result = add_pressure_diagnostics(result)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compact = {
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "sample": result["sample"],
        "direct_total": result["metrics"]["direct_total"],
        "parity_head": result["metrics"]["parity_head"],
        "hda": result["metrics"]["hda"],
        "exact_score": result["metrics"]["exact_score"],
        "draw_diagnostics": result["draw_diagnostics"],
        "pressure_diagnostics": result["pressure_diagnostics"],
        "decision_checks": result["decision_checks"],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
