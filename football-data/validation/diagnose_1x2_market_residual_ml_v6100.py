#!/usr/bin/env python3
"""Research-only market-residual ML diagnostic for 1X2 accuracy.

The market de-vig probabilities are the primary anchor. Models are allowed only to learn
residual corrections using information already available in the retrospective prematch
row: market probabilities, frozen formal-model probabilities, their disagreements and
competition identity. No result-derived, lineup-hindsight or in-match features are used.

Chronology is strict within each competition:
- earliest 60%: train
- next 20%: model/hyperparameter selection
- latest 20%: untouched test

Legacy odds remain RETROSPECTIVE_REFERENCE_ONLY because original quote timestamps are
not available for the full historical backfill. This script cannot change CURRENT.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from diagnose_1x2_market_anchor_v697 import (
    _load_model_rows, _match_market, _market_probs, _model_probs, _pick_probs
)

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

OUT = ROOT / "manifests" / "v6_1x2_market_residual_ml_v6100_status.json"
SEED = 20260724
DIRECTIONS = ("home", "draw", "away")
CLASS_TO_INT = {"home": 0, "draw": 1, "away": 2}
INT_TO_CLASS = {v: k for k, v in CLASS_TO_INT.items()}


def _entropy(p: dict[str, float]) -> float:
    return -sum(float(p[k]) * math.log(max(1e-12, float(p[k]))) for k in DIRECTIONS)


def _margin(p: dict[str, float]) -> float:
    vals = sorted((float(p[k]) for k in DIRECTIONS), reverse=True)
    return vals[0] - vals[1]


def _feature_names(competitions: list[str]) -> list[str]:
    base = [
        "market_home","market_draw","market_away",
        "model_home","model_draw","model_away",
        "resid_home","resid_draw","resid_away",
        "market_max","market_margin","market_entropy",
        "model_max","model_margin","model_entropy",
        "market_model_l1","market_model_top1_disagree",
        "market_pick_home","market_pick_draw","market_pick_away",
        "model_pick_home","model_pick_draw","model_pick_away",
        "market_log_h_d","market_log_a_d","model_log_h_d","model_log_a_d",
    ]
    return base + [f"comp::{cid}" for cid in competitions]


def _features(row: dict[str, Any], competitions: list[str]) -> list[float]:
    q = _market_probs(row)
    m = _model_probs(row)
    qpick = _pick_probs(q)
    mpick = _pick_probs(m)
    eps = 1e-9
    values = [
        q["home"], q["draw"], q["away"],
        m["home"], m["draw"], m["away"],
        m["home"]-q["home"], m["draw"]-q["draw"], m["away"]-q["away"],
        max(q.values()), _margin(q), _entropy(q),
        max(m.values()), _margin(m), _entropy(m),
        sum(abs(m[k]-q[k]) for k in DIRECTIONS),
        1.0 if qpick != mpick else 0.0,
        1.0 if qpick=="home" else 0.0, 1.0 if qpick=="draw" else 0.0, 1.0 if qpick=="away" else 0.0,
        1.0 if mpick=="home" else 0.0, 1.0 if mpick=="draw" else 0.0, 1.0 if mpick=="away" else 0.0,
        math.log((q["home"]+eps)/(q["draw"]+eps)), math.log((q["away"]+eps)/(q["draw"]+eps)),
        math.log((m["home"]+eps)/(m["draw"]+eps)), math.log((m["away"]+eps)/(m["draw"]+eps)),
    ]
    cid = row["competition_id"]
    values.extend(1.0 if cid == c else 0.0 for c in competitions)
    return values


def _split(rows: list[dict[str, Any]]):
    train, valid, test = [], [], []
    for cid in sorted({r["competition_id"] for r in rows}):
        sub = sorted([r for r in rows if r["competition_id"] == cid], key=lambda r: (r["date"], r["home_team"], r["away_team"]))
        n = len(sub)
        i1 = max(1, int(n * 0.60))
        i2 = max(i1 + 1, int(n * 0.80))
        if i2 >= n:
            i2 = n - 1
        train.extend(sub[:i1]); valid.extend(sub[i1:i2]); test.extend(sub[i2:])
    return train, valid, test


def _acc_from_picks(rows, picks):
    hits = sum(1 for r,p in zip(rows,picks) if p == r["actual"])
    return {"count": len(rows), "hits": hits, "accuracy": hits/len(rows) if rows else None}


def _market_eval(rows):
    picks = [_pick_probs(_market_probs(r)) for r in rows]
    return _acc_from_picks(rows, picks)


def _model_eval(rows):
    picks = [_pick_probs(_model_probs(r)) for r in rows]
    return _acc_from_picks(rows, picks)


def _predict_labels(model, X):
    return [INT_TO_CLASS[int(v)] for v in model.predict(X)]


def _proba_brier(proba, y):
    total = 0.0
    for probs, yi in zip(proba, y):
        for j in range(3):
            total += (float(probs[j]) - (1.0 if j == yi else 0.0))**2
    return total / len(y)


def _score_model(model, X, y, rows):
    picks = _predict_labels(model, X)
    acc = _acc_from_picks(rows, picks)
    proba = model.predict_proba(X)
    acc["brier"] = _proba_brier(proba, y)
    acc["log_loss"] = float(log_loss(y, proba, labels=[0,1,2]))
    return acc


def _candidate_models():
    out = []
    for c in (0.05,0.1,0.25,0.5,1.0,2.0,5.0):
        out.append((f"logreg_C{c}", Pipeline([
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(C=c, max_iter=3000, solver="lbfgs", class_weight=None, random_state=SEED)),
        ])))
    for lr in (0.025,0.05,0.08):
        for leaves in (5,9,15):
            for l2 in (1.0,5.0,15.0):
                out.append((f"hgb_lr{lr}_leaf{leaves}_l2{l2}", HistGradientBoostingClassifier(
                    learning_rate=lr, max_iter=180, max_leaf_nodes=leaves,
                    min_samples_leaf=35, l2_regularization=l2, random_state=SEED,
                )))
    return out


def _disjoint_blocks(rows, pickers: dict[str, list[str]], block=100):
    # Test rows are already chronological within competition but pooled by competition.
    # Shuffle once only for balanced non-overlapping diagnostic blocks.
    idx = list(range(len(rows)))
    random.Random(SEED+6100).shuffle(idx)
    full = len(idx)//block
    blocks=[]
    comparison=Counter()
    for bi in range(full):
        ids=idx[bi*block:(bi+1)*block]
        item={"block":bi+1,"n":block}
        market_hits=sum(1 for i in ids if pickers["market"][i]==rows[i]["actual"])
        item["market_accuracy"]=market_hits/block
        for name in ("selected_ml","old_model"):
            hits=sum(1 for i in ids if pickers[name][i]==rows[i]["actual"])
            item[f"{name}_accuracy"]=hits/block
        item["ml_vs_market_uplift_pp"]=(item["selected_ml_accuracy"]-item["market_accuracy"])*100.0
        comparison["win" if item["ml_vs_market_uplift_pp"]>0 else "tie" if item["ml_vs_market_uplift_pp"]==0 else "loss"] += 1
        blocks.append(item)
    return {"full_block_count":full,"leftover_count":len(idx)-full*block,"ml_vs_market":dict(comparison),"blocks":blocks}


def main() -> int:
    rows, providers = _match_market(_load_model_rows())
    competitions = sorted({r["competition_id"] for r in rows})
    train, valid, test = _split(rows)
    fnames = _feature_names(competitions)
    Xtr=[_features(r,competitions) for r in train]; ytr=[CLASS_TO_INT[r["actual"]] for r in train]
    Xv=[_features(r,competitions) for r in valid]; yv=[CLASS_TO_INT[r["actual"]] for r in valid]
    Xt=[_features(r,competitions) for r in test]; yt=[CLASS_TO_INT[r["actual"]] for r in test]

    validation=[]
    fitted={}
    for name, model in _candidate_models():
        model.fit(Xtr,ytr)
        score=_score_model(model,Xv,yv,valid)
        validation.append({"name":name,**score})
        fitted[name]=model
    validation.sort(key=lambda x:(x["accuracy"],-x["brier"],-x["log_loss"]),reverse=True)
    selected_name=validation[0]["name"]

    # Refit selected model on train+validation, evaluate once on untouched test.
    selected_template=dict(_candidate_models())[selected_name]
    Xtv=Xtr+Xv; ytv=ytr+yv
    selected_template.fit(Xtv,ytv)
    selected_test=_score_model(selected_template,Xt,yt,test)
    market_test=_market_eval(test)
    old_test=_model_eval(test)

    market_picks=[_pick_probs(_market_probs(r)) for r in test]
    old_picks=[_pick_probs(_model_probs(r)) for r in test]
    ml_picks=_predict_labels(selected_template,Xt)

    # Paired wins/losses of ML against market.
    pair=Counter()
    for r,mp,qp in zip(test,ml_picks,market_picks):
        mc=mp==r["actual"]; qc=qp==r["actual"]
        pair["both_correct" if mc and qc else "ml_only_correct" if mc else "market_only_correct" if qc else "both_wrong"] += 1

    by_comp={}
    for cid in competitions:
        ids=[i for i,r in enumerate(test) if r["competition_id"]==cid]
        if not ids: continue
        n=len(ids)
        mh=sum(1 for i in ids if ml_picks[i]==test[i]["actual"])
        qh=sum(1 for i in ids if market_picks[i]==test[i]["actual"])
        oh=sum(1 for i in ids if old_picks[i]==test[i]["actual"])
        by_comp[cid]={"count":n,"selected_ml_accuracy":mh/n,"market_accuracy":qh/n,"old_model_accuracy":oh/n,"ml_vs_market_uplift_pp":(mh-qh)/n*100.0}

    disjoint=_disjoint_blocks(test,{"market":market_picks,"old_model":old_picks,"selected_ml":ml_picks})

    payload={
        "schema_version":"V6.10.0-market-residual-ml-1x2-r1",
        "generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status":"PASS",
        "formal_current_version":"V5.0.1",
        "market_data_classification":"RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "provider_class_counts":providers,
        "split_contract":{"train":len(train),"validation":len(valid),"test":len(test),"within_competition_chronological":True,"train_fraction":0.60,"validation_fraction":0.20,"test_fraction":0.20},
        "feature_names":fnames,
        "validation_leaderboard":validation,
        "selected_model":selected_name,
        "untouched_latest_20pct_test":{
            "old_model":old_test,
            "market_only":market_test,
            "selected_ml":selected_test,
            "ml_vs_market_uplift_pp":(selected_test["accuracy"]-market_test["accuracy"])*100.0,
            "market_vs_old_uplift_pp":(market_test["accuracy"]-old_test["accuracy"])*100.0,
            "paired_ml_vs_market":dict(pair),
        },
        "disjoint_100_test_blocks":disjoint,
        "by_competition_test":by_comp,
        "governance":{"research_only":True,"legacy_market_not_formal_snapshot":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False},
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"selected_model":selected_name,"test":payload["untouched_latest_20pct_test"],"disjoint":{k:v for k,v in disjoint.items() if k!="blocks"},"by_competition":by_comp},ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
