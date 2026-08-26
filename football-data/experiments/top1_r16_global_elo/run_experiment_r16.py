#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
R12_DIR = HERE.parent / "top1_r12_draw_features"
sys.path.insert(0, str(R12_DIR))
import run_experiment_r12 as r12  # noqa: E402

r9 = r12.r9
FEATURES = [
    "elo_diff_400",
    "elo_expected_home_centered",
    "elo_abs_diff_400",
    "global_history_min_log",
    "global_goal_net_diff",
    "global_xg_net_diff",
    "global_scoring_diff",
    "global_xg_scoring_diff",
]


class StrengthState:
    def __init__(self):
        self.elo = defaultdict(lambda: 1500.0)
        self.n = Counter()
        self.gf = Counter(); self.ga = Counter()
        self.xgf = Counter(); self.xga = Counter()

    @staticmethod
    def avg(total, n, prior=1.35, strength=8.0):
        return (total + strength * prior) / (n + strength)

    def features(self, row):
        h, a = row["home_team"], row["away_team"]
        rh, ra = self.elo[h], self.elo[a]
        exp_h = 1.0 / (1.0 + 10.0 ** ((ra - (rh + 60.0)) / 400.0))
        nh, na = self.n[h], self.n[a]
        hgf = self.avg(self.gf[h], nh); hga = self.avg(self.ga[h], nh)
        agf = self.avg(self.gf[a], na); aga = self.avg(self.ga[a], na)
        hxgf = self.avg(self.xgf[h], nh); hxga = self.avg(self.xga[h], nh)
        axgf = self.avg(self.xgf[a], na); axga = self.avg(self.xga[a], na)
        hnet = hgf - hga; anet = agf - aga
        hxnet = hxgf - hxga; axnet = axgf - axga
        return [
            (rh - ra) / 400.0,
            exp_h - 0.5,
            abs(rh - ra) / 400.0,
            math.log1p(min(nh, na)),
            hnet - anet,
            hxnet - axnet,
            hgf - agf,
            hxgf - axgf,
        ]

    def update(self, row):
        h, a = row["home_team"], row["away_team"]
        hg, ag = int(row["home_goals"]), int(row["away_goals"])
        hx, ax = float(row["home_xg"]), float(row["away_xg"])
        rh, ra = self.elo[h], self.elo[a]
        exp_h = 1.0 / (1.0 + 10.0 ** ((ra - (rh + 60.0)) / 400.0))
        score_h = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        delta = 20.0 * (score_h - exp_h)
        self.elo[h] = rh + delta
        self.elo[a] = ra - delta
        self.n[h] += 1; self.n[a] += 1
        self.gf[h] += hg; self.ga[h] += ag
        self.gf[a] += ag; self.ga[a] += hg
        self.xgf[h] += hx; self.xga[h] += ax
        self.xgf[a] += ax; self.xga[a] += hx


def decorate(model, X):
    pr = model.predict_proba(X)
    classes = list(model[-1].classes_)
    out = []
    for row in pr:
        v = np.zeros(3, dtype=float)
        for cls, prob in zip(classes, row):
            v[int(cls)] = float(prob)
        v = np.clip(v, 1e-12, None); v /= v.sum()
        out.append(r9.decorate(v))
    return out


def subset_metrics(rows, key, predicate):
    z = [r for r in rows if predicate(r)]
    return {"count": len(z), "metrics": r9.metrics(z, key) if z else None}


def run():
    DATA.mkdir(parents=True, exist_ok=True)
    r12.freeze_gate()
    src = json.loads((r12.DATA / "source_manifest_r12.json").read_text(encoding="utf-8"))
    (DATA / "source_manifest_r16.json").write_text(json.dumps({
        "schema_version": "football3-top1-r16-global-elo-strength",
        "status": "FROZEN_FROM_EXACT_R9B_SNAPSHOT",
        "classification": "DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION",
        "formal_weight": 0,
        "r9b_hashes": src["observed_hashes"],
        "snapshot_rows": 20000,
        "same_date_results_and_xg_withheld": True,
        "odds_used": False,
        "market_prices_used": False,
        "static_current_team_rating_used": False,
        "elo_initial": 1500.0,
        "elo_home_advantage": 60.0,
        "elo_K": 20.0,
        "features": FEATURES,
        "hyperparameter_search_used": False,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    rows = r9.load()
    base = r9.S(); strength = StrengthState()
    pred = []
    by = defaultdict(list)
    for row in rows: by[row["date"]].append(row)
    for ds in sorted(by):
        pending = []
        for row in sorted(by[ds], key=lambda x: x["game_id"]):
            raw = base.pred(row)
            sf = strength.features(row)
            pred.append({"date": ds, "y": r9.actual(row), "raw": raw, "strength": sf})
            pending.append((row, raw))
        for row, raw in pending:
            base.update(row, raw)
            strength.update(row)

    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    y = [r["y"] for r in train]
    k1 = make_pipeline(StandardScaler(), LogisticRegression(C=.5, max_iter=3000, random_state=0))
    k4 = make_pipeline(StandardScaler(), LogisticRegression(C=.5, max_iter=3000, random_state=0))
    k1.fit([r9.feat_k1(r["raw"]) for r in train], y)
    k4.fit([r9.feat_k1(r["raw"]) + r["strength"] for r in train], y)

    for subset in (val, test):
        p1 = decorate(k1, [r9.feat_k1(r["raw"]) for r in subset])
        p4 = decorate(k4, [r9.feat_k1(r["raw"]) + r["strength"] for r in subset])
        for rec, a, b in zip(subset, p1, p4): rec["K1"], rec["K4"] = a, b

    v1, v4 = r9.metrics(val, "K1"), r9.metrics(val, "K4")
    t1, t4 = r9.metrics(test, "K1"), r9.metrics(test, "K4")
    if v1["hits"] != 2064 or t1["hits"] != 1877:
        raise RuntimeError("R16 K1 reproduction gate failed")

    # competition-specific history counts are raw home_history/away_history from R9b.
    cold = lambda r: min(r["raw"]["home_history"], r["raw"]["away_history"]) < 5
    mature = lambda r: min(r["raw"]["home_history"], r["raw"]["away_history"]) >= 5
    summary = {
        "schema_version": "football3-top1-r16-global-elo-strength",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION",
        "formal_weight": 0,
        "governance": {
            "snapshot_rows": 20000, "burn_in": b1, "train": len(train), "validation": len(val), "test": len(test),
            "coverage_required": 1.0, "same_date_results_and_xg_withheld": True,
            "strict_prior_global_strength": True, "odds_used": False, "market_prices_used": False,
            "static_current_team_rating_used": False, "hyperparameter_search_used": False,
            "test_used_for_model_selection": False, "formal_promotion_allowed_from_this_run": False,
        },
        "models": {"K1": "R9b baseline", "K4": "K1 plus strict-prior all-competition Elo and global goals/xG strength"},
        "features": FEATURES,
        "validation": {
            "K1": v1, "K4": v4, "delta_K4_minus_K1": r12.delta(v4, v1),
            "competition_cold_start_K1": subset_metrics(val, "K1", cold),
            "competition_cold_start_K4": subset_metrics(val, "K4", cold),
            "competition_mature_K1": subset_metrics(val, "K1", mature),
            "competition_mature_K4": subset_metrics(val, "K4", mature),
        },
        "test": {
            "K1": t1, "K4": t4, "delta_K4_minus_K1": r12.delta(t4, t1),
            "competition_cold_start_K1": subset_metrics(test, "K1", cold),
            "competition_cold_start_K4": subset_metrics(test, "K4", cold),
            "competition_mature_K1": subset_metrics(test, "K1", mature),
            "competition_mature_K4": subset_metrics(test, "K4", mature),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r16.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


def verify():
    s = json.loads((OUT / "summary_r16.json").read_text(encoding="utf-8")); g = s["governance"]
    assert g["coverage_required"] == 1.0 and g["same_date_results_and_xg_withheld"]
    assert g["strict_prior_global_strength"] and not g["odds_used"] and not g["market_prices_used"]
    assert not g["static_current_team_rating_used"] and not g["hyperparameter_search_used"]
    assert not g["test_used_for_model_selection"] and not g["formal_promotion_allowed_from_this_run"]
    print("R16_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}: raise SystemExit("usage: run_experiment_r16.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()

if __name__ == "__main__": main()
