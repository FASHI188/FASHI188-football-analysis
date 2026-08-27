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
S90_DIR = EXP / "batch003_s90_hierarchical_draw"
sys.path.insert(0, str(S90_DIR))

import run_batch003_s90 as s90  # noqa: E402

s80 = s90.s80
s2 = s90.s2
r9 = s90.r9
r14 = s90.r14
safe_meta = s90.safe_meta

LOCKED_S90 = S90_DIR / "results" / "batch003_s90_predictions_locked.json"
HISTORY_N = 60000
TRAIN_N = 24123
C_FIXED = 0.5
TOP1 = {0: "HOME", 1: "DRAW", 2: "AWAY"}


def draw_feat(rec):
    # Exact R19 draw-head feature family: K1 strict-prior state + R14 compact draw 9.
    return r9.feat_k1(rec["raw"]) + rec["compact_draw"]


def fit_draw_head(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=C_FIXED, max_iter=3000, random_state=0),
    )
    model.fit([draw_feat(x) for x in train], [int(x["y"] == 1) for x in train])
    return model, len(draw_feat(train[0]))


def positive_prob(pipe, X):
    classes = list(pipe[-1].classes_)
    if 1 not in classes:
        raise RuntimeError(f"draw positive class missing: {classes}")
    return float(pipe.predict_proba([X])[0][classes.index(1)])


def compose_s91(draw_head, raw, compact_draw, s70_locked):
    pd_raw = positive_prob(draw_head, r9.feat_k1(raw) + compact_draw)
    pd_draw = float(np.clip(pd_raw, 1e-9, 1 - 1e-9))
    h70 = float(s70_locked["p_home"])
    a70 = float(s70_locked["p_away"])
    denom = h70 + a70
    if denom <= 0:
        raise RuntimeError("invalid locked S70 non-draw mass")
    home_share = h70 / denom
    v = np.asarray(
        [(1.0 - pd_draw) * home_share, pd_draw, (1.0 - pd_draw) * (1.0 - home_share)],
        dtype=float,
    )
    v = np.clip(v, 1e-12, None)
    v /= v.sum()
    return {
        "p_home": float(v[0]),
        "p_draw": float(v[1]),
        "p_away": float(v[2]),
        "top1": TOP1[int(np.argmax(v))],
        "draw_head_raw_probability": pd_raw,
    }


def run():
    lock = s90.load_lock()
    old = json.loads(LOCKED_S90.read_text(encoding="utf-8"))
    if old["status"] != "BATCH003_S90_PREDICTIONS_LOCKED" or old["rows"] != 100:
        raise RuntimeError("locked S90 source missing/mismatch")
    if old["cohort_sha256"] != lock["cohort_sha256"]:
        raise RuntimeError("S90 source cohort mismatch")
    og = old["governance"]
    if og["target_results_loaded"] or not og["accuracy_not_computed"]:
        raise RuntimeError("S90 source is not valid pre-reveal material")
    old_by_idx = {int(r["batch_index"]): r for r in old["predictions"]}

    targets, mapping_audit, comp_map = safe_meta.safe_target_metadata(s2, lock)
    pool = s2.load_frozen_pool()
    by_date = defaultdict(list)
    for z in targets:
        by_date[z["date"]].append(z)

    predictions = []
    date_audit = []
    counts = {
        "S60_draw_picks": 0,
        "S70_draw_picks": 0,
        "S80_draw_picks": 0,
        "S90_draw_picks": 0,
        "S91_draw_picks": 0,
        "S91_changed_vs_S60": 0,
        "S91_changed_vs_S70": 0,
        "S91_changed_vs_S80": 0,
        "S91_changed_vs_S90": 0,
    }

    for day in sorted(by_date):
        q = sorted(by_date[day], key=lambda z: z["batch_index"])
        effective_cutoff = min(pd.to_datetime(z["nominal_cutoff_utc"], utc=True) for z in q)
        eligible = [x for x in pool if x["_known"] < effective_cutoff]
        eligible.sort(key=lambda x: (x["date"], x["game_id"]))
        if len(eligible) < HISTORY_N:
            raise RuntimeError(f"insufficient strict-prior history {day}: {len(eligible)}")
        window = [{k: v for k, v in x.items() if k != "_known"} for x in eligible[-HISTORY_N:]]
        hp, state, _robust_hist, draw_state = s80.history_joint(window)
        train = hp[-TRAIN_N:]
        draw_head, feature_count = fit_draw_head(train)

        date_audit.append({
            "date": day,
            "matches": len(q),
            "effective_cutoff_utc": effective_cutoff.isoformat(),
            "history_rows": HISTORY_N,
            "train_rows": TRAIN_N,
            "draw_head_feature_count": feature_count,
            "draw_head_fixed_C": C_FIXED,
            "same_date_results_withheld": True,
        })

        for z in q:
            idx = int(z["batch_index"])
            base = old_by_idx[idx]
            if (base["date"], base["division"], base["home"], base["away"]) != (
                z["date"], z["division"], z["home"], z["away"]
            ):
                raise RuntimeError(f"locked row identity mismatch idx={idx}")
            target = {
                "date": z["date"],
                "game_id": z["fixture_id"],
                "competition_id": z["competition_id"],
                "home_team": z["home_team"],
                "away_team": z["away_team"],
            }
            raw = state.pred(target)
            compact_draw = r14.compact(draw_state.features(target, raw))
            p91 = compose_s91(draw_head, raw, compact_draw, base["S70_Robust"])

            for k in ("S60", "S70_Robust", "S80_RobustCompactDraw", "S90_HierarchicalDrawSide"):
                if base[k]["top1"] == "DRAW":
                    counts[{"S60":"S60_draw_picks","S70_Robust":"S70_draw_picks","S80_RobustCompactDraw":"S80_draw_picks","S90_HierarchicalDrawSide":"S90_draw_picks"}[k]] += 1
            counts["S91_draw_picks"] += int(p91["top1"] == "DRAW")
            counts["S91_changed_vs_S60"] += int(p91["top1"] != base["S60"]["top1"])
            counts["S91_changed_vs_S70"] += int(p91["top1"] != base["S70_Robust"]["top1"])
            counts["S91_changed_vs_S80"] += int(p91["top1"] != base["S80_RobustCompactDraw"]["top1"])
            counts["S91_changed_vs_S90"] += int(p91["top1"] != base["S90_HierarchicalDrawSide"]["top1"])

            predictions.append({
                "batch_index": idx,
                "date": base["date"],
                "division": base["division"],
                "home": base["home"],
                "away": base["away"],
                "fixture_id": base["fixture_id"],
                "kickoff_utc": base["kickoff_utc"],
                "effective_same_date_cutoff_utc": effective_cutoff.isoformat(),
                "S60": base["S60"],
                "S70_Robust": base["S70_Robust"],
                "S80_RobustCompactDraw": base["S80_RobustCompactDraw"],
                "S90_HierarchicalDrawSide": base["S90_HierarchicalDrawSide"],
                "S91_RobustSideDrawHead": p91,
                "status": "LOCKED_NO_TARGET_RESULT",
            })

    predictions.sort(key=lambda x: x["batch_index"])
    if len(predictions) != 100 or [x["batch_index"] for x in predictions] != list(range(1, 101)):
        raise RuntimeError("S91 prediction coverage/order mismatch")

    out = {
        "schema_version": "football3-batch003-s91-robust-side-draw-head-v1",
        "status": "BATCH003_S91_PREDICTIONS_LOCKED",
        "rows": 100,
        "cohort_sha256": lock["cohort_sha256"],
        "source_locked_s90_commit": "c02e66c0cbf9ca43e51404f8ab5f22a7c24356ff",
        "candidate": {
            "name": "S91_RobustSideDrawHead",
            "draw_head": "R19 architecture: binary DRAW-vs-NONDRAW on K1 + R14 compact draw features",
            "side_structure": "locked S70_Robust HOME/AWAY ratio, renormalized within non-draw mass",
            "history_rows": HISTORY_N,
            "train_rows": TRAIN_N,
            "draw_head_classifier": "StandardScaler + LogisticRegression C=0.5 random_state=0",
            "class_weight": None,
            "decision_threshold_override": None,
            "manual_draw_boost": False,
            "hyperparameter_search_on_batch003": False,
            "Batch002_used_for_failure_mode_diagnosis": True,
            "Batch002_used_for_numeric_parameter_tuning": False,
            "R19_preexisting_test_evidence_used_for_architecture_choice": True,
        },
        "governance": {
            "target_results_loaded": False,
            "target_postmatch_stats_loaded": False,
            "target_odds_used": False,
            "market_used": False,
            "candidate_design_locked_before_target_scoring": True,
            "same_date_results_withheld": True,
            "historical_xg_requires_known_at_before_effective_cutoff": True,
            "accuracy_not_computed": True,
            "reveal_forbidden_until_predictions_persisted": True,
        },
        "pre_reveal_top1_counts": counts,
        "competition_map": {k: {"id": v[0], "texts": v[1]} for k, v in comp_map.items()},
        "date_audit": date_audit,
        "predictions": predictions,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    (OUT / "batch003_s91_predictions_locked.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (DATA / "mapping_audit_batch003_s91.json").write_text(json.dumps(mapping_audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "rows": 100, "cohort_sha256": out["cohort_sha256"], "pre_reveal_top1_counts": counts}, indent=2, ensure_ascii=False))


def verify():
    lock = s90.load_lock()
    s = json.loads((OUT / "batch003_s91_predictions_locked.json").read_text(encoding="utf-8"))
    c = s["candidate"]
    g = s["governance"]
    assert s["status"] == "BATCH003_S91_PREDICTIONS_LOCKED" and s["rows"] == 100
    assert s["cohort_sha256"] == lock["cohort_sha256"]
    assert c["class_weight"] is None and c["decision_threshold_override"] is None
    assert not c["manual_draw_boost"] and not c["hyperparameter_search_on_batch003"]
    assert c["R19_preexisting_test_evidence_used_for_architecture_choice"]
    assert not c["Batch002_used_for_numeric_parameter_tuning"]
    assert not g["target_results_loaded"] and not g["target_postmatch_stats_loaded"]
    assert not g["target_odds_used"] and not g["market_used"] and g["accuracy_not_computed"]
    assert g["candidate_design_locked_before_target_scoring"] and g["same_date_results_withheld"]
    for row in s["predictions"]:
        q = row["S91_RobustSideDrawHead"]
        assert abs(q["p_home"] + q["p_draw"] + q["p_away"] - 1.0) < 1e-9
        assert q["top1"] in {"HOME", "DRAW", "AWAY"}
    print("BATCH003_S91_VERIFY_PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_batch003_s91.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()
