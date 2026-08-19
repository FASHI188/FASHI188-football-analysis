#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import c074g_footballdata_2526_directt_confirmation as c074g

SCHEMA = "C076A_DIRECTT_TAIL_MASS_IMPACT_AUDIT_V1"
QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
THRESHOLDS = [0.005, 0.01, 0.02, 0.03, 0.05, 0.10]


def dist_summary(x: np.ndarray) -> dict:
    a = np.asarray(x, dtype=float)
    out = {
        "n": int(len(a)),
        "mean": float(np.mean(a)),
        "min": float(np.min(a)),
        "max": float(np.max(a)),
    }
    for q in QUANTILES:
        out[f"p{int(round(100*q)):02d}"] = float(np.quantile(a, q))
    out["fraction_below"] = {
        f"{100*t:.1f}%": float(np.mean(a < t)) for t in THRESHOLDS
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/c076a_tail_mass")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    raw, source_audit = c074g.load_all()
    duplicate_ids = int(raw.duplicated(["league_key","date","HomeTeam","AwayTeam"]).sum())
    feat = c074g.build_history_features(raw)
    usable = feat.loc[feat["eligible_history"] & feat["odds_valid"]].copy()
    train = usable.loc[(usable["season_start"] >= c074g.TRAIN_START) & (usable["season_start"] <= c074g.TRAIN_END)].copy()
    test = usable.loc[usable["season_start"] == c074g.TEST_START].copy()

    coverage = {
        "train_n": int(len(train)), "test_n": int(len(test)),
        "train_leagues": sorted(train.league_key.unique().tolist()),
        "test_leagues": sorted(test.league_key.unique().tolist()),
        "duplicate_identity_rows_all_loaded": duplicate_ids,
        "train_classes": sorted(train.target.unique().astype(int).tolist()),
        "test_classes": sorted(test.target.unique().astype(int).tolist()),
    }
    if (
        len(train) != 14032 or len(test) != 2329 or duplicate_ids != 0
        or coverage["train_classes"] != list(range(8)) or coverage["test_classes"] != list(range(8))
    ):
        raise RuntimeError(f"C074-G canonical coverage drift: {coverage}")

    ytr = train.target.to_numpy(dtype=int)
    baseline = c074g.pipeline(); candidate = c074g.pipeline()
    baseline.fit(train[c074g.BASE_FEATURES], ytr)
    candidate.fit(train[c074g.CANDIDATE_FEATURES], ytr)
    pb = c074g.aligned_proba(baseline, test[c074g.BASE_FEATURES])
    pc = c074g.aligned_proba(candidate, test[c074g.CANDIDATE_FEATURES])

    if float(np.max(np.abs(pb.sum(axis=1)-1.0))) > 1e-10 or float(np.max(np.abs(pc.sum(axis=1)-1.0))) > 1e-10:
        raise RuntimeError("probability conservation drift")

    b7 = pb[:, 7]
    c7 = pc[:, 7]
    actual7 = (test.target.to_numpy(int) == 7)

    leagues = []
    test_lg = test.league_key.to_numpy()
    for league in sorted(test.league_key.unique()):
        mask = test_lg == league
        x = c7[mask]
        leagues.append({
            "league": str(league), "n": int(mask.sum()),
            "candidate_mean_p7plus": float(np.mean(x)),
            "candidate_p50_p7plus": float(np.quantile(x, .50)),
            "candidate_p90_p7plus": float(np.quantile(x, .90)),
            "candidate_p95_p7plus": float(np.quantile(x, .95)),
            "candidate_max_p7plus": float(np.max(x)),
            "actual_t7plus_n": int(actual7[mask].sum()),
            "actual_t7plus_rate": float(actual7[mask].mean()),
        })

    rows = pd.DataFrame({
        "date": test.date.astype(str).to_numpy(),
        "league_key": test.league_key.to_numpy(),
        "HomeTeam": test.HomeTeam.to_numpy(),
        "AwayTeam": test.AwayTeam.to_numpy(),
        "baseline_p7plus": b7,
        "candidate_p7plus": c7,
        "actual_t7plus": actual7.astype(int),
    })
    rows.to_csv(out / "confirmation_tail_mass_rows.csv", index=False)
    pd.DataFrame(leagues).to_csv(out / "league_tail_mass.csv", index=False)

    summary = {
        "schema_version": SCHEMA,
        "status": "DESCRIPTIVE_IMPACT_AUDIT_COMPLETE",
        "formal_weight": 0,
        "authority": "read_only_post_confirmation_audit_no_promotion",
        "coverage": coverage,
        "candidate_p_t7plus": dist_summary(c7),
        "baseline_p_t7plus": dist_summary(b7),
        "observed_t7plus": {
            "n": int(actual7.sum()),
            "rate": float(actual7.mean()),
        },
        "leagues": leagues,
        "identifiability": {
            "generic_binary_event_max_probability_uncertainty_from_unknown_tail": "<= P(T>=7) per match if the event settlement is not constant over all exact scores in the 7+ set",
            "constant_on_all_7plus_scores_uncertainty": 0,
            "ou_0_5_through_6_5_internal_tail_uncertainty": 0,
            "ou_7_0_and_above_may_depend_on_exact_tail": True,
            "exact_total_7_8_plus_may_depend_on_exact_tail": True,
            "exact_score_may_depend_on_exact_tail": True,
            "btts_hda_ah_score_dependent_ev_may_depend_on_exact_tail": True,
            "expected_total_finite_upper_bound_from_public_8class_vector_alone": False,
        },
        "governance": {
            "low_tail_mass_exception_created": False,
            "CURRENT_exact_tail_gate_overridden": False,
            "unified_matrix_generated": False,
            "formal_exact_score_allowed": False,
            "feature_search": False,
            "C_search": False,
            "subset_search": False,
            "transform_search": False,
            "new_target_domain_opened": False,
            "C071_reserve_52180_opened": False,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False,
            "protected_opened": False,
        },
        "source_audit": source_audit,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": summary["status"],
        "candidate_p7plus": summary["candidate_p_t7plus"],
        "observed_t7plus": summary["observed_t7plus"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
