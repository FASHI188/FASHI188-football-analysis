#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "summary_r43af1.json"
AB1_DIR = HERE.parent / "r43ab1_referee_prior_style_screen"
sys.path.insert(0, str(AB1_DIR))
import run_r43ab1 as ab1  # noqa: E402

r9 = ab1.r9
r34 = ab1.r34
MODEL_C = 0.5
TEAM_SHRINK = 8.0
COMP_SHRINK = 20.0
GLOBAL_RESID_MSE_PRIOR = 1.5
RESID_CLIP = 4.0

FEATURES = [
    "log_min_vol_matches",
    "home_attack_rmse",
    "home_defence_rmse",
    "away_attack_rmse",
    "away_defence_rmse",
    "attack_rmse_diff",
    "defence_rmse_diff",
    "pair_attack_rmse_mean",
    "pair_defence_rmse_mean",
    "home_total_rmse",
    "away_total_rmse",
    "total_rmse_diff",
]


@dataclass
class Vol:
    n: int = 0
    attack_sse: float = 0.0
    defence_sse: float = 0.0


@dataclass
class CompVol:
    n: int = 0
    sse: float = 0.0


class ResidualVolState:
    def __init__(self):
        self.team = defaultdict(Vol)
        self.comp = defaultdict(CompVol)

    def _comp_mse(self, comp: str) -> float:
        s = self.comp[comp]
        return (s.sse + COMP_SHRINK * GLOBAL_RESID_MSE_PRIOR) / (s.n + COMP_SHRINK)

    def _team_rmse(self, team: str, comp: str, which: str) -> float:
        s = self.team[team]
        prior = self._comp_mse(comp)
        sse = s.attack_sse if which == "attack" else s.defence_sse
        mse = (sse + TEAM_SHRINK * prior) / (s.n + TEAM_SHRINK)
        return math.sqrt(max(mse, 1e-12))

    def features(self, comp: str, home: str, away: str) -> dict[str, float]:
        hs = self.team[home]; as_ = self.team[away]
        ha = self._team_rmse(home, comp, "attack")
        hd = self._team_rmse(home, comp, "defence")
        aa = self._team_rmse(away, comp, "attack")
        ad = self._team_rmse(away, comp, "defence")
        ht = math.sqrt((ha * ha + hd * hd) / 2.0)
        at = math.sqrt((aa * aa + ad * ad) / 2.0)
        return {
            "log_min_vol_matches": math.log1p(float(min(hs.n, as_.n))),
            "home_attack_rmse": ha,
            "home_defence_rmse": hd,
            "away_attack_rmse": aa,
            "away_defence_rmse": ad,
            "attack_rmse_diff": ha - aa,
            "defence_rmse_diff": hd - ad,
            "pair_attack_rmse_mean": (ha + aa) / 2.0,
            "pair_defence_rmse_mean": (hd + ad) / 2.0,
            "home_total_rmse": ht,
            "away_total_rmse": at,
            "total_rmse_diff": ht - at,
        }

    @staticmethod
    def _sq(err: float) -> float:
        e = max(-RESID_CLIP, min(RESID_CLIP, float(err)))
        return e * e

    def update(self, row: dict, raw: dict) -> None:
        comp = row["competition_id"]
        home = row["home_team"]; away = row["away_team"]
        hg = float(row["home_goals"]); ag = float(row["away_goals"])
        # Fixed geometric blend of the goal-state and xG-state expectations already present in K1 raw state.
        mh = math.sqrt(max(1e-12, float(raw["mu_home"]) * float(raw["xg_mu_home"])))
        ma = math.sqrt(max(1e-12, float(raw["mu_away"]) * float(raw["xg_mu_away"])))
        home_attack = self._sq(hg - mh)
        home_def = self._sq(ag - ma)
        away_attack = self._sq(ag - ma)
        away_def = self._sq(hg - mh)
        hs = self.team[home]; as_ = self.team[away]
        hs.n += 1; hs.attack_sse += home_attack; hs.defence_sse += home_def
        as_.n += 1; as_.attack_sse += away_attack; as_.defence_sse += away_def
        cs = self.comp[comp]
        cs.n += 2
        cs.sse += home_attack + away_attack


def build_history():
    r34.r12.freeze_gate()
    rows = r9.load()
    base = r9.S(); vol = ResidualVolState(); pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for ds in sorted(by):
        pending = []
        for row in sorted(by[ds], key=lambda x: x["game_id"]):
            raw = base.pred(row)
            vf = vol.features(row["competition_id"], row["home_team"], row["away_team"])
            pred.append({"date": ds, "y": r9.actual(row), "raw": raw, "vol_features": vf})
            pending.append((row, raw))
        for row, raw in pending:
            vol.update(row, raw)
            base.update(row, raw)
    return pred


def x_for(rec):
    return list(r9.feat_k1(rec["raw"])) + [float(rec["vol_features"][k]) for k in FEATURES]


def fit_candidate(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(C=MODEL_C, max_iter=3000, random_state=0))
    m.fit([x_for(r) for r in train], [r["y"] for r in train])
    return m


def decorate_candidate(model, rows):
    probs = model.predict_proba([x_for(r) for r in rows])
    classes = list(model[-1].classes_)
    out = []
    for src, p in zip(rows, probs):
        v = np.zeros(3, dtype=float)
        for cls, q in zip(classes, p):
            v[int(cls)] = float(q)
        out.append({"date": src["date"], "y": src["y"], "P": r9.decorate(v)})
    return out


def run():
    pred = build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val0, test0 = pred[b1:b2], pred[b2:b3], pred[b3:]

    base_model = ab1.baseline_model(train)
    val_base_rows = ab1.decorate_baseline(base_model, val0)
    vb = ab1.metrics(val_base_rows)
    if vb["hits"] != 2064:
        raise RuntimeError(f"R43AF1 K1 validation reproduction failed: {vb['hits']}")

    cand_model = fit_candidate(train)
    val_cand_rows = decorate_candidate(cand_model, val0)
    vc = ab1.metrics(val_cand_rows)
    vd = ab1.delta(vb, vc)
    vpb = ab1.paired_blocks(val_base_rows, val_cand_rows)
    validation_gate = ab1.dev_gate(vd, vpb)

    historical_test = None
    if validation_gate:
        test_base_rows = ab1.decorate_baseline(base_model, test0)
        tb = ab1.metrics(test_base_rows)
        if tb["hits"] != 1877:
            raise RuntimeError(f"R43AF1 K1 test reproduction failed: {tb['hits']}")
        test_cand_rows = decorate_candidate(cand_model, test0)
        tc = ab1.metrics(test_cand_rows)
        td = ab1.delta(tb, tc)
        tpb = ab1.paired_blocks(test_base_rows, test_cand_rows)
        strong_test = bool(
            td["accuracy_pp"] >= 1.0
            and td["logloss"] < 0 and td["brier"] < 0 and td["rps"] < 0
            and td["draw_logloss"] <= 0 and td["draw_brier"] <= 0
            and tpb["nonnegative_blocks"] >= 3
        )
        historical_test = {"baseline": tb, "candidate": tc, "candidate_minus_baseline": td, "paired_time_blocks": tpb, "strong_test_gate": strong_test}
        action = "FREEZE_K1_RESIDUAL_VOLATILITY_ARCHITECTURE_FOR_GENUINELY_FRESH_CONFIRMATION" if strong_test else "DO_NOT_PROMOTE_OR_RETUNE_K1_RESIDUAL_VOLATILITY_ON_CONSUMED_HISTORY"
    else:
        action = "CLOSE_K1_RESIDUAL_VOLATILITY_AXIS_NO_STRONG_VALIDATION_SIGNAL"

    min_hist = [min(r["raw"]["home_history"], r["raw"]["away_history"]) for r in val0]
    out = {
        "schema_version": "football3-r43af1-k1-residual-volatility-screen-v1",
        "status": "COMPLETE",
        "classification": "POSTVIEW_HISTORICAL_DEVELOPMENT_STRICT_PRIOR_K1_RESIDUAL_SECOND_MOMENT_FORMAL_WEIGHT_ZERO",
        "formal_weight": 0,
        "governance": {
            "same_r9_consumed_20k_history": True,
            "current_match_result_or_xg_used_in_features": False,
            "same_date_updates_withheld": True,
            "odds_used": False,
            "current_lineup_used": False,
            "feature_set_predeclared": True,
            "window_search": False,
            "hyperparameter_search": False,
            "test_opened_only_after_strong_validation_gate": True,
            "promotion_allowed_from_this_run": False,
        },
        "design": {
            "feature_set": FEATURES,
            "model": f"StandardScaler + multinomial LogisticRegression C={MODEL_C}",
            "team_shrinkage_strength": TEAM_SHRINK,
            "competition_shrinkage_strength": COMP_SHRINK,
            "global_residual_mse_prior": GLOBAL_RESID_MSE_PRIOR,
            "residual_clip_goals": RESID_CLIP,
            "residual_expectation": "geometric mean of each prior match pre-update K1 raw goal-state mu and xG-state mu",
            "state": "all strictly earlier-date matches; no rolling-window or half-life search",
            "validation_gate": ">=+1.0pp Top1; LL/Brier/RPS all improve; draw LL/Brier nonworse; >=3/4 time blocks nonnegative and >=2 positive",
            "test_gate": ">=+1.0pp Top1; LL/Brier/RPS all improve; draw LL/Brier nonworse; >=3/4 time blocks nonnegative",
        },
        "split": {"train_n": len(train), "validation_n": len(val0), "historical_test_n": len(test0)},
        "coverage": {
            "validation_rows": len(val0),
            "validation_min_k1_history_median": float(np.median(min_hist)),
            "validation_min_k1_history_ge5_rate": float(np.mean(np.asarray(min_hist) >= 5)),
        },
        "validation": {"baseline": vb, "candidate": vc, "candidate_minus_baseline": vd, "paired_time_blocks": vpb, "strong_validation_gate": validation_gate},
        "historical_test": historical_test,
        "action": action,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "coverage": out["coverage"], "validation_delta": vd, "validation_blocks": vpb, "validation_gate": validation_gate, "historical_test": historical_test, "action": action}, ensure_ascii=False, indent=2))
    return out


def verify():
    s = json.loads(OUT.read_text(encoding="utf-8"))
    assert s["status"] == "COMPLETE" and s["formal_weight"] == 0
    assert s["governance"]["same_date_updates_withheld"] is True
    assert s["governance"]["window_search"] is False
    assert s["design"]["residual_clip_goals"] == 4.0
    assert s["validation"]["baseline"]["hits"] == 2064
    if s["historical_test"] is not None:
        assert s["historical_test"]["baseline"]["hits"] == 1877
    print("R43AF1 K1 residual volatility development contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(cmd)
