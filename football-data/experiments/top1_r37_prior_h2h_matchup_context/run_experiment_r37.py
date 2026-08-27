#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R36_DIR = HERE.parent / "top1_r36_prior_result_propensity"
sys.path.insert(0, str(R36_DIR))
import run_experiment_r36 as r36  # noqa: E402

r34 = r36.r34
r9 = r36.r9

PAIR_PRIOR_STRENGTH = 3.0
SAME_VENUE_PRIOR_STRENGTH = 3.0

H2H_RESULT_NAMES = [
    "h2h_has_history",
    "log_h2h_matches",
    "h2h_home_win_rate",
    "h2h_draw_rate",
    "h2h_home_loss_rate",
    "h2h_home_edge",
    "h2h_days_since_last_scaled",
    "h2h_same_venue_has_history",
    "log_h2h_same_venue_matches",
    "h2h_same_venue_home_win_rate",
    "h2h_same_venue_draw_rate",
    "h2h_same_venue_home_loss_rate",
]
H2H_SCORE_XG_NAMES = [
    "h2h_has_history",
    "log_h2h_matches",
    "h2h_goal_diff_mean",
    "h2h_goal_total_mean",
    "h2h_xg_diff_mean",
    "h2h_xg_total_mean",
    "h2h_same_venue_has_history",
    "h2h_same_venue_goal_diff_mean",
    "h2h_same_venue_xg_diff_mean",
]
FEATURE_SETS = {
    "H2H_RESULT_ONLY": H2H_RESULT_NAMES,
    "H2H_SCORE_XG_ONLY": H2H_SCORE_XG_NAMES,
    "H2H_MATCHUP_CONTEXT": list(dict.fromkeys(H2H_RESULT_NAMES + H2H_SCORE_XG_NAMES)),
}

MIN_VALIDATION_GAIN_HITS = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MAX_VALIDATION_LOGLOSS_WORSEN = 0.001
MIN_TEST_GAIN_HITS = 1
MIN_POSITIVE_TEST_BLOCKS = 2
MAX_NEGATIVE_TEST_BLOCKS = 1
MAX_TEST_LOGLOSS_WORSEN = 0.001


def pair_key(a, b):
    return tuple(sorted((str(a), str(b))))


def rate3(counts, prior=None, strength=PAIR_PRIOR_STRENGTH):
    c = np.asarray(counts, dtype=float)
    if prior is None:
        p = np.asarray([1.0 / 3.0] * 3, dtype=float)
    else:
        p = np.asarray(prior, dtype=float)
    v = c + float(strength) * p
    v = np.clip(v, 1e-12, None)
    return v / v.sum()


class H2HState:
    def __init__(self):
        self.hist = defaultdict(list)

    def features(self, row):
        h = str(row["home_team"])
        a = str(row["away_team"])
        d = date.fromisoformat(row["date"])
        hist = self.hist[pair_key(h, a)]

        counts = np.zeros(3, dtype=float)
        gd = gt = xd = xt = 0.0
        same = []
        for rec in hist:
            if rec["home_team"] == h:
                gf, ga = rec["home_goals"], rec["away_goals"]
                xf, xa = rec["home_xg"], rec["away_xg"]
            else:
                gf, ga = rec["away_goals"], rec["home_goals"]
                xf, xa = rec["away_xg"], rec["home_xg"]
            y = 0 if gf > ga else 1 if gf == ga else 2
            counts[y] += 1.0
            gd += gf - ga
            gt += gf + ga
            xd += xf - xa
            xt += xf + xa
            if rec["home_team"] == h and rec["away_team"] == a:
                same.append((gf, ga, xf, xa, y))

        n = len(hist)
        rates = rate3(counts)
        same_counts = np.zeros(3, dtype=float)
        sgd = sxd = 0.0
        for gf, ga, xf, xa, y in same:
            same_counts[y] += 1.0
            sgd += gf - ga
            sxd += xf - xa
        sn = len(same)
        same_rates = rate3(same_counts, rates, SAME_VENUE_PRIOR_STRENGTH)

        if n:
            days = max(1, (d - hist[-1]["date"]).days)
            days_scaled = min(3650.0, float(days)) / 365.0
        else:
            days_scaled = 10.0

        return {
            "h2h_has_history": float(n > 0),
            "log_h2h_matches": math.log1p(float(n)),
            "h2h_home_win_rate": float(rates[0]),
            "h2h_draw_rate": float(rates[1]),
            "h2h_home_loss_rate": float(rates[2]),
            "h2h_home_edge": float(rates[0] - rates[2]),
            "h2h_days_since_last_scaled": float(days_scaled),
            "h2h_same_venue_has_history": float(sn > 0),
            "log_h2h_same_venue_matches": math.log1p(float(sn)),
            "h2h_same_venue_home_win_rate": float(same_rates[0]),
            "h2h_same_venue_draw_rate": float(same_rates[1]),
            "h2h_same_venue_home_loss_rate": float(same_rates[2]),
            "h2h_goal_diff_mean": float(gd / n) if n else 0.0,
            "h2h_goal_total_mean": float(gt / n) if n else 0.0,
            "h2h_xg_diff_mean": float(xd / n) if n else 0.0,
            "h2h_xg_total_mean": float(xt / n) if n else 0.0,
            "h2h_same_venue_goal_diff_mean": float(sgd / sn) if sn else 0.0,
            "h2h_same_venue_xg_diff_mean": float(sxd / sn) if sn else 0.0,
        }

    def update(self, row):
        self.hist[pair_key(row["home_team"], row["away_team"])].append(
            {
                "date": date.fromisoformat(row["date"]),
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
                "home_goals": int(row["home_goals"]),
                "away_goals": int(row["away_goals"]),
                "home_xg": float(row["home_xg"]),
                "away_xg": float(row["away_xg"]),
            }
        )


def build_history():
    r34.r12.freeze_gate()
    rows = r9.load()
    base = r9.S()
    h2h = H2HState()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            cf = h2h.features(row)
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "context_features": cf})
            pending.append((row, raw))
        for row, raw in pending:
            base.update(row, raw)
            h2h.update(row)
    return pred


def x_for(rec, feature_names):
    return list(r9.feat_k1(rec["raw"])) + [float(rec["context_features"][n]) for n in feature_names]


def fit_model(train, feature_names):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    m.fit([x_for(r, feature_names) for r in train], [r["y"] for r in train])
    return m


def decorate(model, rows, feature_names):
    pr = model.predict_proba([x_for(r, feature_names) for r in rows])
    classes = list(model[-1].classes_)
    out = []
    for src, row in zip(rows, pr):
        v = np.zeros(3, dtype=float)
        for cls, p in zip(classes, row):
            v[int(cls)] = float(p)
        v = np.clip(v, 1e-12, None)
        v /= v.sum()
        out.append({"date": src["date"], "y": src["y"], "P": r9.decorate(v)})
    return out


def baseline_model(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    m.fit([r9.feat_k1(r["raw"]) for r in train], [r["y"] for r in train])
    return m


def baseline_decorate(k1, rows):
    p = r34.r19.decorate_k1(k1, rows)
    return [{"date": r["date"], "y": r["y"], "P": q} for r, q in zip(rows, p)]


def metrics(rows):
    return r9.metrics([{"y": r["y"], "P": r["P"]} for r in rows], "P")


def date_blocks(rows, n=4):
    dates = sorted({r["date"] for r in rows})
    chunks = np.array_split(np.asarray(dates, dtype=object), n)
    out = {}
    for i, chunk in enumerate(chunks):
        for d in chunk.tolist():
            out[d] = i
    return out


def paired_blocks(base_rows, candidate_rows):
    block_map = date_blocks(base_rows, 4)
    blocks = {str(i): {"count": 0, "base_hits": 0, "candidate_hits": 0, "net": 0} for i in range(4)}
    gain = loss = 0
    for b, c in zip(base_rows, candidate_rows):
        if b["date"] != c["date"] or b["y"] != c["y"]:
            raise RuntimeError("R37 paired rows misaligned")
        y = b["y"]
        cb = int(b["P"]["top1"] == y)
        cc = int(c["P"]["top1"] == y)
        gain += int(cc and not cb)
        loss += int(cb and not cc)
        z = blocks[str(block_map[b["date"]])]
        z["count"] += 1
        z["base_hits"] += cb
        z["candidate_hits"] += cc
    for z in blocks.values():
        z["net"] = z["candidate_hits"] - z["base_hits"]
    return {
        "challenger_gain": gain, "challenger_loss": loss, "net_hits": gain - loss,
        "positive_time_blocks": sum(int(z["net"] > 0) for z in blocks.values()),
        "negative_time_blocks": sum(int(z["net"] < 0) for z in blocks.values()),
        "time_blocks": blocks,
    }


def coverage(rows):
    n = len(rows)
    h = sum(int(r["context_features"]["h2h_has_history"] > 0) for r in rows)
    s = sum(int(r["context_features"]["h2h_same_venue_has_history"] > 0) for r in rows)
    return {"rows": n, "h2h_history_rows": h, "h2h_history_rate": h / n, "same_venue_rows": s, "same_venue_rate": s / n}


def run():
    pred = build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val0, test0 = pred[b1:b2], pred[b2:b3], pred[b3:]

    k1 = baseline_model(train)
    val_base = baseline_decorate(k1, val0)
    base_v = metrics(val_base)
    if base_v["hits"] != 2064:
        raise RuntimeError(f"R37 K1 validation reproduction gate failed: {base_v['hits']}")

    r36_summary = json.loads((R36_DIR / "results" / "summary_r36.json").read_text(encoding="utf-8"))
    if r36_summary["decision"]["eligible_for_next_fresh_confirmation"]:
        raise RuntimeError("R37 expects frozen R36 failure control")

    candidates = []
    models = {}
    for name, features in FEATURE_SETS.items():
        model = fit_model(train, features)
        models[name] = model
        val = decorate(model, val0, features)
        mv = metrics(val)
        paired = paired_blocks(val_base, val)
        gain = mv["hits"] - base_v["hits"]
        ll_delta = mv["logloss"] - base_v["logloss"]
        viable = (
            gain >= MIN_VALIDATION_GAIN_HITS
            and paired["positive_time_blocks"] >= MIN_POSITIVE_VALIDATION_BLOCKS
            and paired["negative_time_blocks"] <= MAX_NEGATIVE_VALIDATION_BLOCKS
            and ll_delta <= MAX_VALIDATION_LOGLOSS_WORSEN
        )
        candidates.append({
            "name": name, "features": features, "viable": viable, "validation": mv,
            "gain_hits": gain,
            "gain_top1_pp": 100.0 * (mv["top1_accuracy"] - base_v["top1_accuracy"]),
            "logloss_delta": ll_delta, "brier_delta": mv["brier"] - base_v["brier"],
            "rps_delta": mv["rps"] - base_v["rps"], "paired": paired,
        })

    viable = [x for x in candidates if x["viable"]]
    if viable:
        selected = max(viable, key=lambda x: (x["gain_hits"], x["paired"]["positive_time_blocks"], -x["paired"]["negative_time_blocks"], -x["logloss_delta"]))
        selected_name = selected["name"]
        test_base = baseline_decorate(k1, test0)
        base_t = metrics(test_base)
        if base_t["hits"] != 1877:
            raise RuntimeError(f"R37 K1 test reproduction gate failed: {base_t['hits']}")
        test = decorate(models[selected_name], test0, FEATURE_SETS[selected_name])
        mt = metrics(test)
        paired_t = paired_blocks(test_base, test)
        test_gain = mt["hits"] - base_t["hits"]
        test_ll_delta = mt["logloss"] - base_t["logloss"]
        historical_test = {
            "baseline": base_t, "candidate": mt, "gain_hits": test_gain,
            "gain_top1_pp": 100.0 * (mt["top1_accuracy"] - base_t["top1_accuracy"]),
            "logloss_delta": test_ll_delta, "brier_delta": mt["brier"] - base_t["brier"],
            "rps_delta": mt["rps"] - base_t["rps"], "paired": paired_t,
        }
        historically_confirmed = (
            test_gain >= MIN_TEST_GAIN_HITS
            and paired_t["positive_time_blocks"] >= MIN_POSITIVE_TEST_BLOCKS
            and paired_t["negative_time_blocks"] <= MAX_NEGATIVE_TEST_BLOCKS
            and test_ll_delta <= MAX_TEST_LOGLOSS_WORSEN
        )
        stop_reason = None if historically_confirmed else "FROZEN_PRIOR_H2H_MATCHUP_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None
        historical_test = None
        historically_confirmed = False
        stop_reason = "NO_VALIDATION_ROBUST_PRIOR_H2H_MATCHUP_GAIN"

    summary = {
        "schema_version": "football3-top1-r37-prior-h2h-matchup-context",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_INDEPENDENT_STRICT_PRIOR_MATCHUP_INFORMATION_FAMILY",
        "formal_weight": 0,
        "governance": {
            "base_r36_commit": "c13b26cf28a57656f31a5830ddd16a412167870b",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "same_date_h2h_updates_withheld": True,
            "current_match_outcome_used": False,
            "current_match_xg_used": False,
            "odds_used": False,
            "market_prices_used": False,
            "lineup_used": False,
            "postmatch_stats_used": False,
            "fixed_shrinkage_no_search": True,
            "model_hyperparameter_search_used": False,
            "candidate_selected_on_validation_only": True,
            "test_evaluated_only_after_viable_validation_freeze": True,
            "test_used_for_candidate_selection": False,
            "batch005_labels_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "After R36 improved probability scores but not Top1, does strictly prior opponent-pair matchup history localize useful 1X2/draw information beyond K1?",
        "prematch_information_family": {
            "family": "PRIOR_HEAD_TO_HEAD_RESULT_SCORE_AND_XG_CONTEXT",
            "causal_contract": "Only earlier-date meetings between the same two teams are visible. All fixtures on a date are predicted before that date updates H2H state.",
            "fixed_shrinkage": {"pair_result_prior_strength": PAIR_PRIOR_STRENGTH, "same_venue_to_pair_strength": SAME_VENUE_PRIOR_STRENGTH},
            "candidate_feature_sets": FEATURE_SETS,
        },
        "coverage": {"train": coverage(train), "validation": coverage(val0), "historical_test": coverage(test0)},
        "selection_contract": {
            "min_validation_gain_hits": MIN_VALIDATION_GAIN_HITS,
            "min_positive_validation_blocks": MIN_POSITIVE_VALIDATION_BLOCKS,
            "max_negative_validation_blocks": MAX_NEGATIVE_VALIDATION_BLOCKS,
            "max_validation_logloss_worsen": MAX_VALIDATION_LOGLOSS_WORSEN,
            "min_test_gain_hits": MIN_TEST_GAIN_HITS,
            "min_positive_test_blocks": MIN_POSITIVE_TEST_BLOCKS,
            "max_negative_test_blocks": MAX_NEGATIVE_TEST_BLOCKS,
            "max_test_logloss_worsen": MAX_TEST_LOGLOSS_WORSEN,
        },
        "controls": {"K1_validation": base_v, "R36_stop_reason": r36_summary["decision"]["stop_reason"]},
        "validation_candidates": candidates,
        "selected_feature_set": selected,
        "historical_test_confirmation": historical_test,
        "decision": {
            "eligible_for_next_fresh_confirmation": historically_confirmed,
            "action": "LOCK_FRESH_CONFIRMATION_FOR_FROZEN_R37" if historically_confirmed else "DO_NOT_PROMOTE_R37",
            "stop_reason": stop_reason,
        },
        "next_if_fail": "Continue another independent auditable prematch family; do not threshold-tune draw picks on validation/test labels.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r37.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r37.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert s["status"] == "COMPLETE"
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"] and g["same_date_h2h_updates_withheld"]
    assert not g["current_match_outcome_used"] and not g["current_match_xg_used"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["fixed_shrinkage_no_search"] and not g["model_hyperparameter_search_used"]
    assert g["candidate_selected_on_validation_only"] and not g["test_used_for_candidate_selection"]
    assert not g["batch005_labels_used"] and not g["formal_promotion_allowed_from_this_run"]
    assert s["controls"]["K1_validation"]["hits"] == 2064
    assert len(s["validation_candidates"]) == len(FEATURE_SETS)
    if s["decision"]["eligible_for_next_fresh_confirmation"]:
        assert s["selected_feature_set"] is not None and s["historical_test_confirmation"] is not None
    print("R37_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r37.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
