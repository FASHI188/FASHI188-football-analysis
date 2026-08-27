#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
DATA = HERE / "data"
EXP = HERE.parent
S80_DIR = EXP / "batch002_stage1_s80_robust_compact_draw"
sys.path.insert(0, str(S80_DIR))

import run_batch002_s80 as s80  # noqa: E402
import safe_metadata_batch002 as safe_meta  # noqa: E402

s2 = s80.s2
s70 = s80.s70
r14 = s80.r14
r9 = s80.r9
r23 = s80.r23
r24 = s80.r24

LOCK = EXP / "batch003_100match_lock" / "results" / "batch003_locked_100.json"
HISTORY_N = 60000
TRAIN_N = 24123
C_FIXED = 0.5
TOP1 = {0: "HOME", 1: "DRAW", 2: "AWAY"}


def load_lock():
    s = json.loads(LOCK.read_text(encoding="utf-8"))
    if s["status"] != "LOCKED" or len(s["rows"]) != 100:
        raise RuntimeError("Batch-003 cohort lock mismatch")
    g = s["governance"]
    if g["outcome_fields_accessed"] or g["selection_uses_results"] or g["selection_uses_odds"]:
        raise RuntimeError("Batch-003 cohort governance mismatch")
    return s


def feat(rec):
    return r9.feat_k1(rec["raw"]) + rec["robust"] + rec["compact_draw"]


def fit_s90(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    X = [feat(x) for x in train]
    y_draw = [int(x["y"] == 1) for x in train]
    draw_head = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=C_FIXED, max_iter=3000, random_state=0),
    )
    draw_head.fit(X, y_draw)

    nd = [x for x in train if x["y"] != 1]
    side_head = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=C_FIXED, max_iter=3000, random_state=0),
    )
    side_head.fit([feat(x) for x in nd], [int(x["y"] == 0) for x in nd])
    return draw_head, side_head, len(X[0]), len(nd)


def positive_prob(pipe, X):
    classes = list(pipe[-1].classes_)
    if 1 not in classes:
        raise RuntimeError(f"positive class missing: {classes}")
    return float(pipe.predict_proba([X])[0][classes.index(1)])


def pred_s90(models, raw, robust, compact_draw):
    draw_head, side_head = models
    X = r9.feat_k1(raw) + robust + compact_draw
    pd = float(np.clip(positive_prob(draw_head, X), 1e-9, 1 - 1e-9))
    ph_nd = float(np.clip(positive_prob(side_head, X), 1e-9, 1 - 1e-9))
    v = np.asarray([(1 - pd) * ph_nd, pd, (1 - pd) * (1 - ph_nd)], dtype=float)
    v = np.clip(v, 1e-12, None)
    v /= v.sum()
    return r9.decorate(v)


def named(p):
    return {
        "p_home": float(p["p_home"]),
        "p_draw": float(p["p_draw"]),
        "p_away": float(p["p_away"]),
        "top1": TOP1[int(p["top1"])],
    }


def run():
    lock = load_lock()
    # Generic metadata-only resolver: fixture/team/league identity + kickoff only, no outcomes.
    targets, mapping_audit, comp_map = safe_meta.safe_target_metadata(s2, lock)
    pool = s2.load_frozen_pool()

    by_date = defaultdict(list)
    for z in targets:
        by_date[z["date"]].append(z)

    predictions = []
    date_audit = []
    changed_90_vs_60 = changed_90_vs_70 = changed_90_vs_80 = 0
    draw_picks_60 = draw_picks_70 = draw_picks_80 = draw_picks_90 = 0

    for day in sorted(by_date):
        q = sorted(by_date[day], key=lambda z: z["batch_index"])
        effective_cutoff = min(pd.to_datetime(z["nominal_cutoff_utc"], utc=True) for z in q)
        eligible = [x for x in pool if x["_known"] < effective_cutoff]
        eligible.sort(key=lambda x: (x["date"], x["game_id"]))
        if len(eligible) < HISTORY_N:
            raise RuntimeError(f"insufficient strict-prior history {day}: {len(eligible)}")
        window = [{k: v for k, v in x.items() if k != "_known"} for x in eligible[-HISTORY_N:]]
        hp, state, robust_hist, draw_state = s80.history_joint(window)
        train = hp[-TRAIN_N:]

        base_model = r24.model(train)
        model70 = s70.fit(train)
        model80 = s80.fit_s80(train)
        draw_head, side_head, feature_count, non_draw_train_rows = fit_s90(train)

        date_audit.append({
            "date": day,
            "matches": len(q),
            "effective_cutoff_utc": effective_cutoff.isoformat(),
            "history_rows": HISTORY_N,
            "train_rows": TRAIN_N,
            "non_draw_side_train_rows": non_draw_train_rows,
            "feature_count_each_head": feature_count,
            "fixed_C": C_FIXED,
            "same_date_results_withheld": True,
        })

        for z in q:
            target = {
                "date": z["date"],
                "game_id": z["fixture_id"],
                "competition_id": z["competition_id"],
                "home_team": z["home_team"],
                "away_team": z["away_team"],
            }
            raw = state.pred(target)
            robust = s70.robust_vec(robust_hist, target["home_team"], target["away_team"])
            compact_draw = r14.compact(draw_state.features(target, raw))

            p60 = r23.pred(base_model, raw)
            p70 = s70.predict(model70, raw, robust)
            p80 = s80.pred_s80(model80, raw, robust, compact_draw)
            p90 = pred_s90((draw_head, side_head), raw, robust, compact_draw)
            n60, n70, n80, n90 = map(named, (p60, p70, p80, p90))

            changed_90_vs_60 += int(n90["top1"] != n60["top1"])
            changed_90_vs_70 += int(n90["top1"] != n70["top1"])
            changed_90_vs_80 += int(n90["top1"] != n80["top1"])
            draw_picks_60 += int(n60["top1"] == "DRAW")
            draw_picks_70 += int(n70["top1"] == "DRAW")
            draw_picks_80 += int(n80["top1"] == "DRAW")
            draw_picks_90 += int(n90["top1"] == "DRAW")

            predictions.append({
                "batch_index": z["batch_index"],
                "date": z["date"],
                "division": z["division"],
                "home": z["home"],
                "away": z["away"],
                "fixture_id": z["fixture_id"],
                "kickoff_utc": z["kickoff_utc"],
                "nominal_cutoff_utc": z["nominal_cutoff_utc"],
                "effective_same_date_cutoff_utc": effective_cutoff.isoformat(),
                "S60": n60,
                "S70_Robust": n70,
                "S80_RobustCompactDraw": n80,
                "S90_HierarchicalDrawSide": n90,
                "status": "LOCKED_NO_TARGET_RESULT",
            })

    predictions.sort(key=lambda x: x["batch_index"])
    out = {
        "schema_version": "football3-batch003-s90-hierarchical-draw-side-v1",
        "status": "BATCH003_S90_PREDICTIONS_LOCKED",
        "rows": len(predictions),
        "cohort_sha256": lock["cohort_sha256"],
        "candidate": {
            "name": "S90_HierarchicalDrawSide",
            "architecture": "binary DRAW-vs-NONDRAW head + independent HOME-vs-AWAY head trained on historical non-draws; probabilities recomposed exactly",
            "features_each_head": "R9b K1 strict-prior state + S70 robust features + R14 compact draw features",
            "history_rows": HISTORY_N,
            "train_rows": TRAIN_N,
            "classifier_each_head": "StandardScaler + LogisticRegression C=0.5 random_state=0",
            "class_weight": None,
            "decision_threshold_override": None,
            "manual_draw_boost": False,
            "hyperparameter_search_on_batch003": False,
            "numeric_parameters_inherited_pre_Batch003": True,
            "Batch002_used_for_failure_mode_diagnosis": True,
            "Batch002_used_for_numeric_parameter_tuning": False,
        },
        "governance": {
            "target_results_loaded": False,
            "target_postmatch_stats_loaded": False,
            "target_odds_used": False,
            "market_used": False,
            "candidate_design_locked_before_target_scoring": True,
            "same_date_results_withheld": True,
            "historical_xg_requires_known_at_before_effective_cutoff": True,
            "manual_probability_adjustment": False,
            "accuracy_not_computed": True,
            "reveal_forbidden_until_predictions_persisted": True,
        },
        "pre_reveal_top1_counts": {
            "S60_draw_picks": draw_picks_60,
            "S70_draw_picks": draw_picks_70,
            "S80_draw_picks": draw_picks_80,
            "S90_draw_picks": draw_picks_90,
            "S90_changed_vs_S60": changed_90_vs_60,
            "S90_changed_vs_S70": changed_90_vs_70,
            "S90_changed_vs_S80": changed_90_vs_80,
        },
        "competition_map": {k: {"id": v[0], "texts": v[1]} for k, v in comp_map.items()},
        "date_audit": date_audit,
        "predictions": predictions,
    }
    if len(predictions) != 100:
        raise RuntimeError(f"prediction coverage incomplete: {len(predictions)}/100")

    OUT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    (OUT / "batch003_s90_predictions_locked.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (DATA / "mapping_audit_batch003_s90.json").write_text(
        json.dumps(mapping_audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": out["status"],
        "rows": out["rows"],
        "cohort_sha256": out["cohort_sha256"],
        "pre_reveal_top1_counts": out["pre_reveal_top1_counts"],
    }, indent=2, ensure_ascii=False))


def verify():
    lock = load_lock()
    s = json.loads((OUT / "batch003_s90_predictions_locked.json").read_text(encoding="utf-8"))
    g = s["governance"]
    c = s["candidate"]
    p = s["predictions"]
    assert s["status"] == "BATCH003_S90_PREDICTIONS_LOCKED"
    assert s["cohort_sha256"] == lock["cohort_sha256"]
    assert s["rows"] == 100 and len(p) == 100
    assert [x["batch_index"] for x in p] == list(range(1, 101))
    assert c["history_rows"] == 60000 and c["train_rows"] == 24123
    assert c["class_weight"] is None and c["decision_threshold_override"] is None
    assert not c["manual_draw_boost"] and not c["hyperparameter_search_on_batch003"]
    assert c["Batch002_used_for_failure_mode_diagnosis"] and not c["Batch002_used_for_numeric_parameter_tuning"]
    assert not g["target_results_loaded"] and not g["target_postmatch_stats_loaded"]
    assert not g["target_odds_used"] and not g["market_used"]
    assert g["candidate_design_locked_before_target_scoring"]
    assert g["same_date_results_withheld"] and g["historical_xg_requires_known_at_before_effective_cutoff"]
    assert not g["manual_probability_adjustment"] and g["accuracy_not_computed"]
    for row in p:
        for key in ("S60", "S70_Robust", "S80_RobustCompactDraw", "S90_HierarchicalDrawSide"):
            q = row[key]
            assert abs(q["p_home"] + q["p_draw"] + q["p_away"] - 1.0) < 1e-9
            assert q["top1"] in {"HOME", "DRAW", "AWAY"}
    print("BATCH003_S90_VERIFY_PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_batch003_s90.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()
