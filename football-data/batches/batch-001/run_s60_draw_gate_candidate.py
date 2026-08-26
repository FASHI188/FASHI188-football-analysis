#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXP = ROOT / "experiments"
for p in (
    HERE,
    EXP / "top1_r14_compact_draw",
    EXP / "top1_r19_compact_two_stage_draw",
):
    sys.path.insert(0, str(p))

import run_s60_pseudoprospective_baseline as base  # noqa: E402
import run_experiment_r14 as r14  # noqa: E402
import run_experiment_r19 as r19  # noqa: E402

r9 = base.r9
r23 = base.r23
r24 = base.r24
r12 = r14.r12

DRAW_GATE = 0.08


def history_with_draw(rows):
    state = r9.S()
    draw_state = r12.DrawState()
    pred = []
    by = defaultdict(list)
    for x in rows:
        by[x["date"]].append(x)
    for day in sorted(by):
        pending = []
        for x in sorted(by[day], key=lambda z: z["game_id"]):
            raw = state.pred(x)
            df = draw_state.features(x, raw)
            pred.append(
                {
                    "date": day,
                    "game_id": x["game_id"],
                    "y": r9.actual(x),
                    "raw": raw,
                    "draw_features": df,
                }
            )
            pending.append((x, raw))
        for x, raw in pending:
            state.update(x, raw)
            draw_state.update(x)
    return pred, state, draw_state


def fit_draw_head(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    yd = [int(x["y"] == 1) for x in train]
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.5, max_iter=3000, random_state=0),
    )
    model.fit(
        [r9.feat_k1(x["raw"]) + r14.compact(x["draw_features"]) for x in train],
        yd,
    )
    return model


def main() -> int:
    lock_path = Path(os.environ["BATCH001_LOCK"])
    out_dir = Path(os.environ.get("BATCH001_DRAW_OUT", "batch001_draw_out"))
    work = out_dir / "_work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    lock = base.read_lock(lock_path)
    history, source_meta = base.historical_rows(work)
    hist, state, draw_state = history_with_draw(history)
    train = hist[-base.CLASSIFIER_TRAIN_N:]
    if len(train) != base.CLASSIFIER_TRAIN_N:
        raise RuntimeError("draw candidate training row count drift")

    s60_model = r24.model(train)
    draw_model = fit_draw_head(train)

    teams, leagues, team_sha, league_sha = base.load_metadata(work)
    mapped, audit, cmap = base.map_targets(lock, history, teams, leagues)
    unresolved = [x for x in audit if x["home_id"] is None or x["away_id"] is None]
    if unresolved or len(mapped) != 100:
        raise RuntimeError(f"target mapping incomplete mapped={len(mapped)} unresolved={len(unresolved)}")

    draw_class_index = list(draw_model[-1].classes_).index(1)
    rows = []
    for x in sorted(mapped, key=lambda z: z["batch_index"]):
        raw = state.pred(x)
        df = draw_state.features(x, raw)
        s60 = r23.pred(s60_model, raw)
        X = [r9.feat_k1(raw) + r14.compact(df)]
        pd = float(draw_model.predict_proba(X)[0][draw_class_index])
        j2 = r19.recompose(s60, pd)
        edge = float(j2["p_draw"] - max(j2["p_home"], j2["p_away"]))
        g2_top1 = 1 if int(s60["top1"]) != 1 and int(j2["top1"]) == 1 and edge >= DRAW_GATE else int(s60["top1"])
        rows.append(
            {
                "batch_index": x["batch_index"],
                "match_key_sha256": x["match_key_sha256"],
                "date": x["date"],
                "division": x["division"],
                "home": x["home"],
                "away": x["away"],
                "S60": s60,
                "J2_S60": j2,
                "binary_draw_probability": pd,
                "draw_edge": edge,
                "G2_S60_top1": base.TOP1[g2_top1],
                "G2_switched_to_draw": bool(g2_top1 == 1 and int(s60["top1"]) != 1),
            }
        )

    s60_picks = {k: sum(base.TOP1[int(x["S60"]["top1"])] == k for x in rows) for k in ("home", "draw", "away")}
    j2_picks = {k: sum(base.TOP1[int(x["J2_S60"]["top1"])] == k for x in rows) for k in ("home", "draw", "away")}
    g2_picks = {k: sum(x["G2_S60_top1"] == k for x in rows) for k in ("home", "draw", "away")}
    switches = sum(x["G2_switched_to_draw"] for x in rows)

    payload = {
        "schema_version": "football3-batch001-s60-r21-draw-gate-candidate-v1",
        "status": "DRAW_CANDIDATE_LOCKED_NO_TARGET_LABELS",
        "classification": "RETROSPECTIVE_PSEUDO_PROSPECTIVE_ZERO_TARGET_LABEL_CHALLENGER",
        "batch_lock_sha256": base.EXPECTED_LOCK_SHA256,
        "candidate_contract": {
            "base": "time-shifted S60",
            "draw_head": "R19 dedicated binary draw objective using R9b K1 + R14 compact 9 strict-prior features",
            "recomposition": "preserve S60 home/away ratio and replace draw mass with binary draw head",
            "decision_gate": "R21-style switch from non-draw S60 Top1 to draw only when J2 draw is Top1 and draw_edge >= 0.08",
            "threshold": DRAW_GATE,
            "threshold_origin": "R21 historical validation only; not tuned on Batch-001",
            "classifier_C": 0.5,
            "history_rows": base.HISTORY_N,
            "training_rows": base.CLASSIFIER_TRAIN_N,
        },
        "governance": {
            "target_result_fields_loaded": False,
            "target_xg_loaded": False,
            "target_market_prices_loaded": False,
            "target_rows_used_to_update_state": False,
            "target_labels_used_for_threshold_selection": False,
            "manual_probability_adjustment": False,
        },
        "source_meta": {
            **source_meta,
            "teams_sha256": team_sha,
            "leagues_sha256": league_sha,
        },
        "competition_map": {k: {"id": v[0], "texts": v[1]} for k, v in cmap.items()},
        "mapping_coverage": len(mapped) / len(lock),
        "S60_top1_picks": s60_picks,
        "J2_S60_top1_picks": j2_picks,
        "G2_S60_top1_picks": g2_picks,
        "G2_switches_to_draw": switches,
        "rows": rows,
    }
    p = out_dir / "batch001_s60_draw_candidate_predictions.json"
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt = {
        "status": payload["status"],
        "rows": len(rows),
        "mapping_coverage": payload["mapping_coverage"],
        "S60_top1_picks": s60_picks,
        "J2_S60_top1_picks": j2_picks,
        "G2_S60_top1_picks": g2_picks,
        "G2_switches_to_draw": switches,
        "predictions_sha256": base.fsha(p),
    }
    (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        work.rmdir()
    except OSError:
        pass
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
