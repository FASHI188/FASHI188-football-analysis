#!/usr/bin/env python3
"""V6.53.0 rich-data draw-only intervention.

Market Top-1 is the default decision. This challenger may change a home/away market
Top-1 to DRAW only when a rich-market CatBoost draw classifier clears a historically
selected threshold. It never changes a market draw to another class and never changes
home directly to away or vice versa.

Selection discipline:
- train 2022/23;
- select depth, draw sample weight, draw-probability threshold, and market-margin cap
  on 2023/24 only;
- retrain fixed model spec on 2022/23+2023/24;
- evaluate the fixed decision rule on untouched 2024/25;
- A100 opens only if holdout uplift >= +0.5pp with adequate intervention support;
- probability vector is unchanged, so proper scores are identical to market;
- B300/C100 are never read.

No A100 result is used for model/rule selection. Research only; CURRENT V5.0.1 unchanged.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from catboost import CatBoostClassifier

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_rich_market_catboost_full500_v6510 as v651  # noqa: E402

OUT = ROOT / "manifests" / "v6_rich_draw_intervention_full500_v6530_status.json"
DEPTHS = (4, 5, 6)
DRAW_WEIGHTS = (1.0, 1.25, 1.5, 1.75, 2.0)
DRAW_P_THRESHOLDS = (0.30, 0.325, 0.35, 0.375, 0.40, 0.425, 0.45, 0.475, 0.50)
MARKET_MARGIN_CAPS = (0.05, 0.10, 0.15, 0.20, 0.30, 1.00)
MIN_SELECTION_OVERRIDES = 15
MIN_HOLDOUT_OVERRIDES = 12
HOLDOUT_REQUIRED_UPLIFT_PP = 0.5


def fit_model(train: list[dict[str, Any]], depth: int, draw_weight: float) -> CatBoostClassifier:
    x = np.asarray([r["x"] for r in train], dtype=float)
    y = np.asarray([r["y"] for r in train], dtype=int)
    sw = np.where(y == 1, draw_weight, 1.0)
    model = CatBoostClassifier(
        loss_function="MultiClass", iterations=v651.ITERATIONS, depth=depth,
        learning_rate=v651.LEARNING_RATE, l2_leaf_reg=v651.L2,
        random_seed=6530, verbose=False, allow_writing_files=False, thread_count=-1,
    )
    model.fit(x, y, sample_weight=sw)
    return model


def evaluate(rows: list[dict[str, Any]], model: CatBoostClassifier, draw_thr: float, margin_cap: float) -> dict[str, Any]:
    x = np.asarray([r["x"] for r in rows], dtype=float)
    y = np.asarray([r["y"] for r in rows], dtype=int)
    market = np.asarray([r["market"] for r in rows], dtype=float)
    p = np.asarray(model.predict_proba(x), dtype=float)
    mp = market.argmax(axis=1)
    sorted_market = np.sort(market, axis=1)
    margin = sorted_market[:,-1] - sorted_market[:,-2]
    override = (mp != 1) & (p[:,1] >= draw_thr - 1e-12) & (margin <= margin_cap + 1e-12)
    cp = mp.copy(); cp[override] = 1
    mh = int(np.sum(mp == y)); ch = int(np.sum(cp == y))
    wins = int(np.sum(override & (y == 1)))
    losses = int(np.sum(override & (mp == y)))
    neutral = int(override.sum()) - wins - losses
    by_league = {}
    for cid in sorted({str(r["competition_id"]) for r in rows}):
        mask = np.asarray([str(r["competition_id"]) == cid for r in rows])
        by_league[cid] = {
            "n": int(mask.sum()), "market_hits": int(np.sum((mp == y)&mask)),
            "candidate_hits": int(np.sum((cp == y)&mask)), "overrides": int(np.sum(override&mask)),
            "draw_wins": int(np.sum(override&mask&(y==1))),
        }
    return {
        "count": len(rows), "market_hits": mh, "candidate_hits": ch,
        "market_top1": mh/len(rows), "candidate_top1": ch/len(rows),
        "uplift_pp": 100.0*(ch-mh)/len(rows),
        "overrides": int(override.sum()), "override_wins": wins, "override_losses": losses,
        "override_neutral": neutral, "net_override_gain": wins-losses,
        "predicted_counts": dict(Counter(str(int(z)) for z in cp)),
        "actual_counts": dict(Counter(str(int(z)) for z in y)), "by_league": by_league,
        "proper_scores": "identical_to_market_by_construction",
    }


def load_a100_labels() -> np.ndarray:
    out=[]
    with v651.LABELS.open("r", encoding="utf-8") as h:
        for _ in range(100):
            r=json.loads(h.readline())
            if r.get("partition")!=v651.PART or int(r["full_index"])!=len(out): raise RuntimeError("A100 label contract changed")
            out.append(int(r["label"]))
    return np.asarray(out,dtype=int)


def main() -> int:
    hist, hist_audit = v651.build_historical()
    train1=[r for r in hist if r["season"]=="2022/23"]
    sel=[r for r in hist if r["season"]=="2023/24"]
    board=[]
    for depth in DEPTHS:
        for dw in DRAW_WEIGHTS:
            model=fit_model(train1,depth,dw)
            for thr in DRAW_P_THRESHOLDS:
                for cap in MARKET_MARGIN_CAPS:
                    met=evaluate(sel,model,thr,cap)
                    if met["overrides"]>=MIN_SELECTION_OVERRIDES:
                        board.append({"depth":depth,"draw_weight":dw,"draw_p_threshold":thr,"market_margin_cap":cap,"selection":met})
    if not board: raise RuntimeError("no V6.53 rule has selection support")
    board.sort(key=lambda z:(z["selection"]["net_override_gain"],z["selection"]["uplift_pp"],-z["selection"]["overrides"],z["draw_p_threshold"],-z["market_margin_cap"]),reverse=True)
    chosen=board[0]

    train2=[r for r in hist if r["season"] in {"2022/23","2023/24"}]
    hold=[r for r in hist if r["season"]=="2024/25"]
    model2=fit_model(train2,int(chosen["depth"]),float(chosen["draw_weight"]))
    holdout=evaluate(hold,model2,float(chosen["draw_p_threshold"]),float(chosen["market_margin_cap"]))
    hist_gate=bool(chosen["selection"]["uplift_pp"]>0 and holdout["uplift_pp"]>=HOLDOUT_REQUIRED_UPLIFT_PP-1e-12 and holdout["overrides"]>=MIN_HOLDOUT_OVERRIDES)

    payload:dict[str,Any]={
        "schema_version":"V6.53.0-rich-draw-intervention-full500-r1","status":"PASS",
        "formal_current_version":"V5.0.1","formal_weight":0,
        "governance":{
            "selection_season_only":"2023/24","holdout_season":"2024/25","A100_values_used_for_selection":False,
            "draw_only_override":True,"probability_vector_modified":False,"B_CONFIRM300_labels_read":False,"C_SEALED100_labels_read":False,"CURRENT_unchanged":True,
        },
        "historical_audit":hist_audit,
        "grid":{"depths":DEPTHS,"draw_weights":DRAW_WEIGHTS,"draw_p_thresholds":DRAW_P_THRESHOLDS,"market_margin_caps":MARKET_MARGIN_CAPS,"minimum_selection_overrides":MIN_SELECTION_OVERRIDES,"minimum_holdout_overrides":MIN_HOLDOUT_OVERRIDES},
        "selected_rule":chosen,"holdout_2024_25":holdout,"historical_gate":hist_gate,"selection_leaderboard_top10":board[:10],
    }
    if not hist_gate:
        payload["A_FAST100"]={"status":"NOT_OPENED_HISTORICAL_HOLDOUT_GATE_FAILED"}; payload["next_step"]="DO_NOT_OPEN_B300"
        OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(payload,ensure_ascii=False,indent=2)); return 0

    arows,aaudit=v651.load_a100_features(); y=load_a100_labels(); rows=[]
    for r,yy in zip(arows,y): z=dict(r); z["y"]=int(yy); rows.append(z)
    model_all=fit_model(hist,int(chosen["depth"]),float(chosen["draw_weight"]))
    amet=evaluate(rows,model_all,float(chosen["draw_p_threshold"]),float(chosen["market_margin_cap"]))
    gate={"required_candidate_hits":63,"required_uplift_vs_market_pp":3.0,"candidate_hits":amet["candidate_hits"],"market_hits":amet["market_hits"],"uplift_vs_market_pp":amet["uplift_pp"],"top1_gate":amet["candidate_hits"]>=63,"uplift_gate":amet["uplift_pp"]>=3.0-1e-12,"proper_score_guard":True}
    gate["A_FAST100_passed"]=bool(gate["top1_gate"] and gate["uplift_gate"])
    payload["A_FAST100"]={"status":"SCORED_AFTER_HISTORICAL_HOLDOUT_GATE","feature_audit":aaudit,"metrics":amet,"gate":gate}
    payload["next_step"]="OPEN_B_CONFIRM300" if gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300"
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(payload,ensure_ascii=False,indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
