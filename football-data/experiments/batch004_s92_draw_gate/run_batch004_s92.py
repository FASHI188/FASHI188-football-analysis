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
S91_DIR = EXP / "batch003_s91_robust_side_draw_head"
sys.path.insert(0, str(S91_DIR))

import run_batch003_s91 as s91  # noqa: E402

s90 = s91.s90
s80 = s91.s80
s2 = s91.s2
s70 = s90.s70
r9 = s91.r9
r23 = s2.r23
r24 = s2.r24
r14 = s90.r14
safe_meta = s91.safe_meta

LOCK = EXP / "batch004_100match_lock" / "results" / "batch004_locked_100.json"
S92_VALIDATION = EXP / "s92_draw_gate_history_validation" / "results" / "summary_s92_history_gate.json"
HISTORY_N = 60000
TRAIN_N = 24123
TOP1 = {0: "HOME", 1: "DRAW", 2: "AWAY"}
EXPECTED_THRESHOLD = 0.40
EXPECTED_VALIDATION_COMMIT = "f58c3d37b58f91a5cf571c8ee393545936d05d70"
EXPECTED_COHORT = "5ec0327c090e4321f15f9682b89c14885e39b30133ec403e4f51541a0006c32a"


def load_lock():
    s = json.loads(LOCK.read_text(encoding="utf-8"))
    if s["status"] != "LOCKED" or len(s["rows"]) != 100:
        raise RuntimeError("Batch004 cohort lock missing/mismatch")
    if s["cohort_sha256"] != EXPECTED_COHORT:
        raise RuntimeError("Batch004 cohort hash mismatch")
    g = s["governance"]
    if g["outcome_fields_accessed"] or g["selection_uses_results"] or g["selection_uses_odds"]:
        raise RuntimeError("Batch004 lock governance mismatch")
    return s


def load_gate_threshold():
    s = json.loads(S92_VALIDATION.read_text(encoding="utf-8"))
    if s["status"] != "S92_HISTORY_GATE_VALIDATION_COMPLETE":
        raise RuntimeError("S92 history validation missing")
    g = s["governance"]
    sel = s["selected"]
    if not sel["supported"] or float(sel["threshold"]) != EXPECTED_THRESHOLD:
        raise RuntimeError("S92 selected threshold mismatch")
    if g["Batch001_results_used"] or g["Batch002_results_used"] or g["Batch003_results_used"]:
        raise RuntimeError("S92 threshold provenance contaminated")
    if g["market_used"] or g["odds_used"]:
        raise RuntimeError("S92 threshold provenance used market/odds")
    return float(sel["threshold"]), s


def named(p):
    top = p["top1"]
    if isinstance(top, str):
        top_name = top
    else:
        top_name = TOP1[int(top)]
    return {
        "p_home": float(p["p_home"]),
        "p_draw": float(p["p_draw"]),
        "p_away": float(p["p_away"]),
        "top1": top_name,
    }


def s92_gate(p91, p70, threshold):
    pd = float(p91["draw_head_raw_probability"])
    if pd >= threshold:
        top = "DRAW"
    else:
        top = "HOME" if float(p70["p_home"]) >= float(p70["p_away"]) else "AWAY"
    return {
        "p_home": float(p91["p_home"]),
        "p_draw": float(p91["p_draw"]),
        "p_away": float(p91["p_away"]),
        "top1": top,
        "draw_head_raw_probability": pd,
        "decision_gate_threshold": threshold,
    }


def run():
    lock = load_lock()
    threshold, gate_validation = load_gate_threshold()

    # Identity/kickoff resolver only; target outcome/stat/odds fields are forbidden here.
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
        "S91_draw_picks": 0,
        "S92_draw_picks": 0,
        "S92_changed_vs_S60": 0,
        "S92_changed_vs_S70": 0,
        "S92_changed_vs_S80": 0,
        "S92_changed_vs_S91": 0,
    }

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
        draw_head, draw_feature_count = s91.fit_draw_head(train)

        date_audit.append({
            "date": day,
            "matches": len(q),
            "effective_cutoff_utc": effective_cutoff.isoformat(),
            "history_rows": HISTORY_N,
            "train_rows": TRAIN_N,
            "draw_head_feature_count": draw_feature_count,
            "draw_head_fixed_C": s91.C_FIXED,
            "S92_gate_threshold": threshold,
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

            p60 = named(r23.pred(base_model, raw))
            p70 = named(s70.predict(model70, raw, robust))
            p80 = named(s80.pred_s80(model80, raw, robust, compact_draw))
            p91 = s91.compose_s91(draw_head, raw, compact_draw, p70)
            p91_named = {
                "p_home": float(p91["p_home"]),
                "p_draw": float(p91["p_draw"]),
                "p_away": float(p91["p_away"]),
                "top1": p91["top1"],
                "draw_head_raw_probability": float(p91["draw_head_raw_probability"]),
            }
            p92 = s92_gate(p91_named, p70, threshold)

            for key, p in (("S60_draw_picks", p60), ("S70_draw_picks", p70), ("S80_draw_picks", p80), ("S91_draw_picks", p91_named), ("S92_draw_picks", p92)):
                counts[key] += int(p["top1"] == "DRAW")
            counts["S92_changed_vs_S60"] += int(p92["top1"] != p60["top1"])
            counts["S92_changed_vs_S70"] += int(p92["top1"] != p70["top1"])
            counts["S92_changed_vs_S80"] += int(p92["top1"] != p80["top1"])
            counts["S92_changed_vs_S91"] += int(p92["top1"] != p91_named["top1"])

            predictions.append({
                "batch_index": int(z["batch_index"]),
                "date": z["date"],
                "division": z["division"],
                "home": z["home"],
                "away": z["away"],
                "fixture_id": z["fixture_id"],
                "kickoff_utc": z["kickoff_utc"],
                "nominal_cutoff_utc": z["nominal_cutoff_utc"],
                "effective_same_date_cutoff_utc": effective_cutoff.isoformat(),
                "S60": p60,
                "S70_Robust": p70,
                "S80_RobustCompactDraw": p80,
                "S91_RobustSideDrawHead": p91_named,
                "S92_HistoricalDrawGate": p92,
                "status": "LOCKED_NO_TARGET_RESULT",
            })

    predictions.sort(key=lambda x: x["batch_index"])
    if len(predictions) != 100 or [x["batch_index"] for x in predictions] != list(range(1, 101)):
        raise RuntimeError("S92 prediction coverage/order mismatch")

    out = {
        "schema_version": "football3-batch004-s92-historical-draw-gate-v1",
        "status": "BATCH004_S92_PREDICTIONS_LOCKED",
        "rows": 100,
        "cohort_sha256": lock["cohort_sha256"],
        "candidate": {
            "name": "S92_HistoricalDrawGate",
            "probability_model": "S91_RobustSideDrawHead unchanged probabilities",
            "decision_rule": "DRAW iff draw-head raw probability >= 0.40; otherwise S70_Robust HOME/AWAY direction",
            "decision_gate_threshold": threshold,
            "threshold_source_validation_commit": EXPECTED_VALIDATION_COMMIT,
            "threshold_source_validation_rows": int(gate_validation["validation_rows"]),
            "threshold_source_gain_hits_vs_S91": int(gate_validation["selected"]["gain_hits_vs_S91_argmax"]),
            "threshold_source_gain_pp_vs_S91": float(gate_validation["selected"]["gain_pp_vs_S91_argmax"]),
            "history_rows": HISTORY_N,
            "train_rows": TRAIN_N,
            "draw_head_classifier": "StandardScaler + LogisticRegression C=0.5 random_state=0",
            "manual_probability_adjustment": False,
            "threshold_search_on_batch004": False,
            "Batch002_results_used_for_numeric_tuning": False,
            "Batch003_results_used_for_numeric_tuning": False,
        },
        "governance": {
            "target_results_loaded": False,
            "target_postmatch_stats_loaded": False,
            "target_odds_used": False,
            "market_used": False,
            "candidate_design_locked_before_target_scoring": True,
            "S92_threshold_fixed_before_Batch004_cohort_lock": True,
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
    (OUT / "batch004_s92_predictions_locked.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (DATA / "mapping_audit_batch004_s92.json").write_text(json.dumps(mapping_audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "rows": 100, "cohort_sha256": out["cohort_sha256"], "pre_reveal_top1_counts": counts}, indent=2, ensure_ascii=False))


def verify():
    lock = load_lock()
    threshold, _ = load_gate_threshold()
    s = json.loads((OUT / "batch004_s92_predictions_locked.json").read_text(encoding="utf-8"))
    c = s["candidate"]
    g = s["governance"]
    assert s["status"] == "BATCH004_S92_PREDICTIONS_LOCKED" and s["rows"] == 100
    assert s["cohort_sha256"] == lock["cohort_sha256"] == EXPECTED_COHORT
    assert c["decision_gate_threshold"] == threshold == EXPECTED_THRESHOLD
    assert c["threshold_source_validation_commit"] == EXPECTED_VALIDATION_COMMIT
    assert not c["manual_probability_adjustment"] and not c["threshold_search_on_batch004"]
    assert not c["Batch002_results_used_for_numeric_tuning"] and not c["Batch003_results_used_for_numeric_tuning"]
    assert not g["target_results_loaded"] and not g["target_postmatch_stats_loaded"]
    assert not g["target_odds_used"] and not g["market_used"] and g["accuracy_not_computed"]
    assert g["candidate_design_locked_before_target_scoring"] and g["S92_threshold_fixed_before_Batch004_cohort_lock"]
    assert [x["batch_index"] for x in s["predictions"]] == list(range(1, 101))
    for row in s["predictions"]:
        q91 = row["S91_RobustSideDrawHead"]
        q92 = row["S92_HistoricalDrawGate"]
        assert abs(q92["p_home"] + q92["p_draw"] + q92["p_away"] - 1.0) < 1e-9
        assert q92["top1"] in {"HOME", "DRAW", "AWAY"}
        assert abs(q91["p_home"] - q92["p_home"]) < 1e-12
        assert abs(q91["p_draw"] - q92["p_draw"]) < 1e-12
        assert abs(q91["p_away"] - q92["p_away"]) < 1e-12
        expected = "DRAW" if q92["draw_head_raw_probability"] >= threshold else ("HOME" if row["S70_Robust"]["p_home"] >= row["S70_Robust"]["p_away"] else "AWAY")
        assert q92["top1"] == expected
    print("BATCH004_S92_VERIFY_PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_batch004_s92.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()
