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
S2 = EXP / "batch001_stage2_historical_s60_replay"
S70 = EXP / "batch001_stage4a_robust_s70"
R14 = EXP / "top1_r14_compact_draw"
for p in (S2, S70, R14):
    sys.path.insert(0, str(p))

import run_stage2 as s2  # noqa: E402
import run_stage4a as s70  # noqa: E402
import run_experiment_r14 as r14  # noqa: E402

r9 = s2.r9
r23 = s2.r23
r24 = s2.r24
LOCK = EXP / "batch002_100match_lock" / "results" / "batch002_locked_100.json"
HISTORY_N = 60000
TRAIN_N = 24123
TOP1 = {0: "HOME", 1: "DRAW", 2: "AWAY"}
COMPACT_NAMES = list(r14.COMPACT_NAMES)


def load_lock():
    s = json.loads(LOCK.read_text(encoding="utf-8"))
    if s["status"] != "LOCKED" or len(s["rows"]) != 100:
        raise RuntimeError("Batch-002 cohort lock mismatch")
    g = s["governance"]
    if g["outcome_columns_read"] or g["selection_uses_results"] or g["selection_uses_odds"]:
        raise RuntimeError("Batch-002 cohort governance mismatch")
    return s


def history_joint(rows):
    """Replay one frozen history window with same-date withholding for every feature family."""
    state = r9.S()
    robust_hist = defaultdict(list)
    draw_state = r14.r12.DrawState()
    pred = []
    by_date = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)
    for day in sorted(by_date):
        pending = []
        for row in sorted(by_date[day], key=lambda z: z["game_id"]):
            raw = state.pred(row)
            robust = s70.robust_vec(robust_hist, row["home_team"], row["away_team"])
            draw = r14.compact(draw_state.features(row, raw))
            pred.append({
                "date": day,
                "game_id": row["game_id"],
                "y": r9.actual(row),
                "raw": raw,
                "robust": robust,
                "compact_draw": draw,
            })
            pending.append((row, raw))
        for row, raw in pending:
            state.update(row, raw)
            robust_hist[str(row["home_team"])].append(s70.side_rec(row, True))
            robust_hist[str(row["away_team"])].append(s70.side_rec(row, False))
            draw_state.update(row)
    return pred, state, robust_hist, draw_state


def fit_s80(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    X = [r9.feat_k1(x["raw"]) + x["robust"] + x["compact_draw"] for x in train]
    y = [x["y"] for x in train]
    m = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.5, max_iter=3000, random_state=0),
    )
    m.fit(X, y)
    return m


def pred_s80(model, raw, robust, compact_draw):
    p = model.predict_proba([r9.feat_k1(raw) + robust + compact_draw])[0]
    classes = list(model[-1].classes_)
    v = np.zeros(3, dtype=float)
    for cls, prob in zip(classes, p):
        v[int(cls)] = float(prob)
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
    # Safe metadata resolver reads fixture/team/league identity and kickoff only; no outcome columns.
    targets, mapping_audit, comp_map = s2.safe_target_metadata(lock)
    pool = s2.load_frozen_pool()

    by_date = defaultdict(list)
    for z in targets:
        by_date[z["date"]].append(z)

    predictions = []
    date_audit = []
    changed_70_vs_60 = 0
    changed_80_vs_60 = 0
    changed_80_vs_70 = 0

    for day in sorted(by_date):
        q = sorted(by_date[day], key=lambda z: z["batch_index"])
        effective_cutoff = min(pd.to_datetime(z["nominal_cutoff_utc"], utc=True) for z in q)
        eligible = [x for x in pool if x["_known"] < effective_cutoff]
        eligible.sort(key=lambda x: (x["date"], x["game_id"]))
        if len(eligible) < HISTORY_N:
            raise RuntimeError(f"insufficient strict-prior history {day}: {len(eligible)}")
        window = [{k: v for k, v in x.items() if k != "_known"} for x in eligible[-HISTORY_N:]]
        hp, state, robust_hist, draw_state = history_joint(window)
        train = hp[-TRAIN_N:]
        base_model = r24.model(train)
        model70 = s70.fit(train)
        model80 = fit_s80(train)
        date_audit.append({
            "date": day,
            "matches": len(q),
            "effective_cutoff_utc": effective_cutoff.isoformat(),
            "history_rows": HISTORY_N,
            "train_rows": TRAIN_N,
            "candidate_fixed_C": 0.5,
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
            p80 = pred_s80(model80, raw, robust, compact_draw)
            n60, n70, n80 = named(p60), named(p70), named(p80)
            changed_70_vs_60 += int(n70["top1"] != n60["top1"])
            changed_80_vs_60 += int(n80["top1"] != n60["top1"])
            changed_80_vs_70 += int(n80["top1"] != n70["top1"])
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
                "top1_changed_S70_vs_S60": n70["top1"] != n60["top1"],
                "top1_changed_S80_vs_S60": n80["top1"] != n60["top1"],
                "top1_changed_S80_vs_S70": n80["top1"] != n70["top1"],
                "status": "LOCKED_NO_TARGET_RESULT",
            })

    predictions.sort(key=lambda x: x["batch_index"])
    out = {
        "schema_version": "football3-batch002-stage1-s80-robust-compact-draw-v1",
        "status": "BATCH002_S80_PREDICTIONS_LOCKED",
        "rows": len(predictions),
        "cohort_sha256": lock["cohort_sha256"],
        "candidate": {
            "name": "S80_RobustCompactDraw",
            "base": "S70_Robust",
            "history_rows": HISTORY_N,
            "train_rows": TRAIN_N,
            "classifier": "StandardScaler + multinomial LogisticRegression C=0.5 random_state=0",
            "robust_feature_family": "strict-prior recent 5/10 GF/GA/xGF/xGA, clipped GD, draw/low2/blowout rates",
            "compact_draw_feature_names": COMPACT_NAMES,
            "compact_draw_feature_count": len(COMPACT_NAMES),
            "hyperparameter_search_on_batch002": False,
            "lineup_or_player_features_included": False,
            "lineup_deferred_reason": "broad five-league strict-prior lineup coverage not yet confirmed; old Bundesliga pilot not generalized",
        },
        "governance": {
            "target_results_loaded": False,
            "target_postmatch_stats_loaded": False,
            "target_odds_used": False,
            "market_used": False,
            "candidate_design_locked_before_target_scoring": True,
            "chronologically_prior_results_and_xg_allowed": True,
            "historical_xg_requires_known_at_before_effective_cutoff": True,
            "same_date_results_withheld": True,
            "current_target_lineup_used": False,
            "manual_probability_adjustment": False,
            "accuracy_not_computed": True,
            "reveal_forbidden_until_predictions_persisted": True,
        },
        "top1_changed_counts": {
            "S70_vs_S60": changed_70_vs_60,
            "S80_vs_S60": changed_80_vs_60,
            "S80_vs_S70": changed_80_vs_70,
        },
        "competition_map": {k: {"id": v[0], "texts": v[1]} for k, v in comp_map.items()},
        "date_audit": date_audit,
        "predictions": predictions,
    }
    if len(predictions) != 100:
        raise RuntimeError(f"prediction coverage incomplete: {len(predictions)}/100")
    OUT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    (OUT / "batch002_s80_predictions_locked.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (DATA / "mapping_audit_batch002_s80.json").write_text(
        json.dumps(mapping_audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": out["status"],
        "rows": out["rows"],
        "cohort_sha256": out["cohort_sha256"],
        "top1_changed_counts": out["top1_changed_counts"],
    }, indent=2, ensure_ascii=False))


def verify():
    lock = load_lock()
    s = json.loads((OUT / "batch002_s80_predictions_locked.json").read_text(encoding="utf-8"))
    g = s["governance"]
    c = s["candidate"]
    p = s["predictions"]
    assert s["status"] == "BATCH002_S80_PREDICTIONS_LOCKED"
    assert s["cohort_sha256"] == lock["cohort_sha256"]
    assert s["rows"] == 100 and len(p) == 100
    assert [x["batch_index"] for x in p] == list(range(1, 101))
    assert c["history_rows"] == 60000 and c["train_rows"] == 24123
    assert c["compact_draw_feature_names"] == COMPACT_NAMES and len(COMPACT_NAMES) == 9
    assert not c["hyperparameter_search_on_batch002"] and not c["lineup_or_player_features_included"]
    assert not g["target_results_loaded"] and not g["target_postmatch_stats_loaded"]
    assert not g["target_odds_used"] and not g["market_used"]
    assert g["candidate_design_locked_before_target_scoring"]
    assert g["historical_xg_requires_known_at_before_effective_cutoff"] and g["same_date_results_withheld"]
    assert not g["current_target_lineup_used"] and not g["manual_probability_adjustment"]
    assert g["accuracy_not_computed"] and g["reveal_forbidden_until_predictions_persisted"]
    for row in p:
        for key in ("S60", "S70_Robust", "S80_RobustCompactDraw"):
            q = row[key]
            assert abs(q["p_home"] + q["p_draw"] + q["p_away"] - 1.0) < 1e-9
            assert q["top1"] in {"HOME", "DRAW", "AWAY"}
    print("BATCH002_S80_VERIFY_PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_batch002_s80.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()
