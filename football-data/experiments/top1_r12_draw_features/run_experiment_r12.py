#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
R9B_DIR = HERE.parent / "top1_r9b_xg_hf"
sys.path.insert(0, str(R9B_DIR))
import run_experiment_r9b as r9  # noqa: E402

EXPECTED = {
    "fixtures_sha256": "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7",
    "match_stats_sha256": "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9",
    "snapshot_sha256": "6ea5f6d98a6b43c1f34df58f08edfa52819415f79da88428947caae68d9170ba",
}

FEATURE_NAMES = [
    "competition_draw_rate",
    "home_team_draw_rate",
    "away_team_draw_rate",
    "team_draw_mean",
    "team_draw_rate_gap",
    "home_venue_draw_rate",
    "away_venue_draw_rate",
    "home_recent_draw_rate",
    "away_recent_draw_rate",
    "recent_draw_mean",
    "home_low2_rate",
    "away_low2_rate",
    "team_low2_mean",
    "competition_low2_rate",
    "poisson_p00",
    "poisson_p11",
    "poisson_low_draw_mass",
    "xg_p00",
    "xg_p11",
    "xg_low_draw_mass",
    "goal_gap_ratio",
    "xg_gap_ratio",
    "goal_parity",
    "xg_parity",
    "competition_x_team_draw",
    "low_draw_mass_x_team_draw",
    "recent_draw_x_xg_parity",
]


class DrawState:
    def __init__(self):
        self.comp_n = Counter()
        self.comp_draw = Counter()
        self.comp_low2 = Counter()
        self.team_n = Counter()
        self.team_draw = Counter()
        self.team_low2 = Counter()
        self.venue_n = Counter()
        self.venue_draw = Counter()
        self.hist = defaultdict(list)

    @staticmethod
    def smooth(k: float, n: float, prior: float, strength: float) -> float:
        return (k + strength * prior) / (n + strength)

    def comp_rates(self, comp: str):
        n = self.comp_n[comp]
        return (
            self.smooth(self.comp_draw[comp], n, 0.26, 30.0),
            self.smooth(self.comp_low2[comp], n, 0.55, 30.0),
        )

    def recent(self, team: str, d: date, draw_prior: float, low_prior: float):
        sw = sd = sl = 0.0
        for dd, is_draw, is_low2 in reversed(self.hist[team]):
            days = (d - dd).days
            if days <= 0:
                continue
            if days > 1440:
                break
            w = math.exp(-math.log(2) * days / 180.0)
            sw += w
            sd += w * is_draw
            sl += w * is_low2
        return (
            (sd + 6.0 * draw_prior) / (sw + 6.0),
            (sl + 6.0 * low_prior) / (sw + 6.0),
        )

    def features(self, row, p):
        c = row["competition_id"]
        h = row["home_team"]
        a = row["away_team"]
        d = date.fromisoformat(row["date"])
        cd, cl = self.comp_rates(c)

        hd = self.smooth(self.team_draw[h], self.team_n[h], cd, 12.0)
        ad = self.smooth(self.team_draw[a], self.team_n[a], cd, 12.0)
        hl = self.smooth(self.team_low2[h], self.team_n[h], cl, 12.0)
        al = self.smooth(self.team_low2[a], self.team_n[a], cl, 12.0)
        hv = self.smooth(self.venue_draw[(h, "H")], self.venue_n[(h, "H")], cd, 8.0)
        av = self.smooth(self.venue_draw[(a, "A")], self.venue_n[(a, "A")], cd, 8.0)
        hr, _ = self.recent(h, d, cd, cl)
        ar, _ = self.recent(a, d, cd, cl)

        p00 = math.exp(-p["mu_total"])
        p11 = p00 * p["mu_home"] * p["mu_away"]
        xp00 = math.exp(-p["xg_mu_total"])
        xp11 = xp00 * p["xg_mu_home"] * p["xg_mu_away"]
        low_draw = p00 + p11
        xlow_draw = xp00 + xp11
        gap = abs(p["mu_home"] - p["mu_away"])
        xgap = abs(p["xg_mu_home"] - p["xg_mu_away"])
        goal_gap_ratio = gap / (p["mu_total"] + 0.25)
        xg_gap_ratio = xgap / (p["xg_mu_total"] + 0.25)
        parity = math.exp(-gap)
        xparity = math.exp(-xgap)
        team_draw_mean = (hd + ad) / 2.0
        recent_draw_mean = (hr + ar) / 2.0

        return [
            cd,
            hd,
            ad,
            team_draw_mean,
            abs(hd - ad),
            hv,
            av,
            hr,
            ar,
            recent_draw_mean,
            hl,
            al,
            (hl + al) / 2.0,
            cl,
            p00,
            p11,
            low_draw,
            xp00,
            xp11,
            xlow_draw,
            goal_gap_ratio,
            xg_gap_ratio,
            parity,
            xparity,
            cd * team_draw_mean,
            low_draw * team_draw_mean,
            recent_draw_mean * xparity,
        ]

    def update(self, row):
        c = row["competition_id"]
        h = row["home_team"]
        a = row["away_team"]
        d = date.fromisoformat(row["date"])
        draw = int(row["home_goals"] == row["away_goals"])
        low2 = int(row["home_goals"] + row["away_goals"] <= 2)
        self.comp_n[c] += 1
        self.comp_draw[c] += draw
        self.comp_low2[c] += low2
        for t in (h, a):
            self.team_n[t] += 1
            self.team_draw[t] += draw
            self.team_low2[t] += low2
            self.hist[t].append((d, draw, low2))
        self.venue_n[(h, "H")] += 1
        self.venue_n[(a, "A")] += 1
        self.venue_draw[(h, "H")] += draw
        self.venue_draw[(a, "A")] += draw


def freeze_gate():
    DATA.mkdir(parents=True, exist_ok=True)
    m = r9.freeze()
    for k, v in EXPECTED.items():
        if m.get(k) != v:
            raise RuntimeError(f"R12 hash gate failed for {k}: {m.get(k)} != {v}")
    out = {
        "schema_version": "football3-top1-r12-draw-features",
        "status": "FROZEN_FROM_EXACT_R9B_SNAPSHOT",
        "classification": "DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION",
        "formal_weight": 0,
        "source": "R9b exact 20000-row snapshot",
        "expected_hashes": EXPECTED,
        "observed_hashes": {k: m[k] for k in EXPECTED},
        "snapshot_rows": m["snapshot_rows"],
        "first_date": m["first_date"],
        "last_date": m["last_date"],
        "strict_prior_xg": True,
        "same_date_results_withheld": True,
        "same_date_xg_withheld": True,
        "odds_used": False,
        "market_prices_used": False,
        "feature_contract": FEATURE_NAMES,
        "hyperparameter_search_used": False,
    }
    (DATA / "source_manifest_r12.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(out, indent=2))


def decorate_probs(model, X):
    pr = model.predict_proba(X)
    classes = list(model[-1].classes_)
    out = []
    for row in pr:
        v = np.zeros(3, dtype=float)
        for cls, prob in zip(classes, row):
            v[int(cls)] = float(prob)
        v = np.clip(v, 1e-12, None)
        v /= v.sum()
        out.append(r9.decorate(v))
    return out


def draw_diag(rows, key):
    from sklearn.metrics import average_precision_score, roc_auc_score

    y = np.array([int(r["y"] == 1) for r in rows], dtype=int)
    p = np.array([r[key]["p_draw"] for r in rows], dtype=float)
    picks = sum(r[key]["top1"] == 1 for r in rows)
    hits = sum(r[key]["top1"] == 1 and r["y"] == 1 for r in rows)
    actual = int(y.sum())
    return {
        "roc_auc": float(roc_auc_score(y, p)),
        "average_precision": float(average_precision_score(y, p)),
        "top1_draw_picks": int(picks),
        "top1_draw_hits": int(hits),
        "top1_draw_precision": float(hits / picks) if picks else 0.0,
        "top1_draw_recall": float(hits / actual) if actual else 0.0,
        "actual_draws": actual,
    }


def delta(a, b):
    return {
        "top1_pp": (a["top1_accuracy"] - b["top1_accuracy"]) * 100.0,
        "hits": a["hits"] - b["hits"],
        "logloss": a["logloss"] - b["logloss"],
        "brier": a["brier"] - b["brier"],
        "rps": a["rps"] - b["rps"],
        "draw_top1_picks": a["top1_picks"]["draw"] - b["top1_picks"]["draw"],
        "draw_top1_hits": a["top1_hits"]["draw"] - b["top1_hits"]["draw"],
    }


def run():
    rows = r9.load()
    base_state = r9.S()
    draw_state = DrawState()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for ds in sorted(by):
        pending = []
        for row in sorted(by[ds], key=lambda x: x["game_id"]):
            raw = base_state.pred(row)
            dfeat = draw_state.features(row, raw)
            pred.append({"date": ds, "y": r9.actual(row), "raw": raw, "draw_features": dfeat})
            pending.append((row, raw))
        for row, raw in pending:
            base_state.update(row, raw)
            draw_state.update(row)

    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    y = [r["y"] for r in train]
    k1 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    k2 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    k1.fit([r9.feat_k1(r["raw"]) for r in train], y)
    k2.fit([r9.feat_k1(r["raw"]) + r["draw_features"] for r in train], y)

    for subset in (val, test):
        p1 = decorate_probs(k1, [r9.feat_k1(r["raw"]) for r in subset])
        p2 = decorate_probs(k2, [r9.feat_k1(r["raw"]) + r["draw_features"] for r in subset])
        for rec, a, b in zip(subset, p1, p2):
            rec["K1"] = a
            rec["K2"] = b

    v1, v2 = r9.metrics(val, "K1"), r9.metrics(val, "K2")
    t1, t2 = r9.metrics(test, "K1"), r9.metrics(test, "K2")

    # Hard reproduction gate: R12 must be evaluated on the same R9b split and baseline.
    if v1["hits"] != 2064 or t1["hits"] != 1877 or len(val) != 4096 or len(test) != 3805:
        raise RuntimeError(f"K1 reproduction gate failed: val={v1['hits']}/{len(val)} test={t1['hits']}/{len(test)}")

    summary = {
        "schema_version": "football3-top1-r12-draw-features",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION",
        "formal_weight": 0,
        "governance": {
            "snapshot_rows": 20000,
            "burn_in": b1,
            "train": len(train),
            "validation": len(val),
            "test": len(test),
            "coverage_required": 1.0,
            "same_date_results_and_xg_withheld": True,
            "strict_prior_draw_features": True,
            "odds_used": False,
            "market_prices_used": False,
            "manual_probability_adjustment": False,
            "hyperparameter_search_used": False,
            "single_preregistered_challenger": True,
            "test_used_for_model_selection": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "models": {
            "K1": "R9b multinomial baseline with strict-prior xG",
            "K2": "K1 plus fixed strict-prior team/league draw tendency, low-score mass and parity features",
        },
        "r12_feature_count": len(FEATURE_NAMES),
        "r12_features": FEATURE_NAMES,
        "validation": {
            "K1": v1,
            "K2": v2,
            "draw_diag_K1": draw_diag(val, "K1"),
            "draw_diag_K2": draw_diag(val, "K2"),
            "delta_K2_minus_K1": delta(v2, v1),
        },
        "test": {
            "K1": t1,
            "K2": t2,
            "draw_diag_K1": draw_diag(test, "K1"),
            "draw_diag_K2": draw_diag(test, "K2"),
            "delta_K2_minus_K1": delta(t2, t1),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r12.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


def verify():
    m = json.loads((DATA / "source_manifest_r12.json").read_text(encoding="utf-8"))
    s = json.loads((OUT / "summary_r12.json").read_text(encoding="utf-8"))
    assert m["observed_hashes"] == EXPECTED
    assert m["snapshot_rows"] == 20000
    assert m["same_date_results_withheld"] and m["same_date_xg_withheld"]
    assert not m["odds_used"] and not m["market_prices_used"]
    g = s["governance"]
    assert g["coverage_required"] == 1.0
    assert g["strict_prior_draw_features"] and g["same_date_results_and_xg_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert not g["hyperparameter_search_used"] and g["single_preregistered_challenger"]
    assert not g["test_used_for_model_selection"] and not g["formal_promotion_allowed_from_this_run"]
    for split in ("validation", "test"):
        assert s[split]["K1"]["coverage"] == 1.0
        assert s[split]["K2"]["coverage"] == 1.0
    print("R12_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"freeze", "run", "verify"}:
        raise SystemExit("usage: run_experiment_r12.py {freeze|run|verify}")
    {"freeze": freeze_gate, "run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
