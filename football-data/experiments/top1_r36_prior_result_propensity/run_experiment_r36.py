#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R35_DIR = HERE.parent / "top1_r35_calendar_kickoff_context"
sys.path.insert(0, str(R35_DIR))
import run_experiment_r35 as r35  # noqa: E402

r34 = r35.r34
r9 = r35.r9

COMP_PRIOR_NAMES = [
    "comp_home_rate",
    "comp_draw_rate",
    "comp_away_rate",
    "comp_home_minus_away",
    "comp_draw_minus_global",
    "log_comp_matches",
]
VENUE_RESULT_NAMES = [
    "home_home_win_rate",
    "home_home_draw_rate",
    "home_home_loss_rate",
    "away_away_win_rate",
    "away_away_draw_rate",
    "away_away_loss_rate",
    "venue_draw_mean",
    "venue_win_edge",
    "log_min_venue_matches",
]
FEATURE_SETS = {
    "COMP_RESULT_PRIOR_ONLY": COMP_PRIOR_NAMES,
    "TEAM_VENUE_RESULT_PROPENSITY_ONLY": VENUE_RESULT_NAMES,
    "PRIOR_RESULT_PROPENSITY_CONTEXT": COMP_PRIOR_NAMES + VENUE_RESULT_NAMES,
}

COMP_PRIOR_STRENGTH = 30.0
TEAM_VENUE_PRIOR_STRENGTH = 8.0

MIN_VALIDATION_GAIN_HITS = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MAX_VALIDATION_LOGLOSS_WORSEN = 0.001
MIN_TEST_GAIN_HITS = 1
MIN_POSITIVE_TEST_BLOCKS = 2
MAX_NEGATIVE_TEST_BLOCKS = 1
MAX_TEST_LOGLOSS_WORSEN = 0.001


def norm_rates(counts, fallback=None):
    n = float(sum(counts))
    if n > 0:
        return np.asarray(counts, dtype=float) / n
    if fallback is None:
        return np.asarray([1.0 / 3.0] * 3, dtype=float)
    return np.asarray(fallback, dtype=float)


def shrink_rates(counts, prior_rates, strength):
    c = np.asarray(counts, dtype=float)
    p = np.asarray(prior_rates, dtype=float)
    n = float(c.sum())
    v = (c + float(strength) * p) / (n + float(strength))
    v = np.clip(v, 1e-12, None)
    return v / v.sum()


class PriorResultState:
    def __init__(self):
        self.global_counts = np.zeros(3, dtype=float)
        self.comp_counts = defaultdict(lambda: np.zeros(3, dtype=float))
        self.home_venue = defaultdict(lambda: np.zeros(3, dtype=float))
        self.away_venue = defaultdict(lambda: np.zeros(3, dtype=float))

    def features(self, row):
        comp = row["competition_id"]
        h = row["home_team"]
        a = row["away_team"]

        global_rates = norm_rates(self.global_counts)
        comp_raw = self.comp_counts[comp]
        comp_rates = shrink_rates(comp_raw, global_rates, COMP_PRIOR_STRENGTH)

        home_prior = comp_rates
        away_prior = np.asarray([comp_rates[2], comp_rates[1], comp_rates[0]], dtype=float)

        h_raw = self.home_venue[h]
        a_raw = self.away_venue[a]
        h_rates = shrink_rates(h_raw, home_prior, TEAM_VENUE_PRIOR_STRENGTH)
        a_rates = shrink_rates(a_raw, away_prior, TEAM_VENUE_PRIOR_STRENGTH)

        return {
            "comp_home_rate": float(comp_rates[0]),
            "comp_draw_rate": float(comp_rates[1]),
            "comp_away_rate": float(comp_rates[2]),
            "comp_home_minus_away": float(comp_rates[0] - comp_rates[2]),
            "comp_draw_minus_global": float(comp_rates[1] - global_rates[1]),
            "log_comp_matches": math.log1p(float(comp_raw.sum())),
            "home_home_win_rate": float(h_rates[0]),
            "home_home_draw_rate": float(h_rates[1]),
            "home_home_loss_rate": float(h_rates[2]),
            "away_away_win_rate": float(a_rates[0]),
            "away_away_draw_rate": float(a_rates[1]),
            "away_away_loss_rate": float(a_rates[2]),
            "venue_draw_mean": float((h_rates[1] + a_rates[1]) / 2.0),
            "venue_win_edge": float(h_rates[0] - a_rates[0]),
            "log_min_venue_matches": math.log1p(float(min(h_raw.sum(), a_raw.sum()))),
        }

    def update(self, row):
        y = int(r9.actual(row))
        self.global_counts[y] += 1.0
        self.comp_counts[row["competition_id"]][y] += 1.0
        self.home_venue[row["home_team"]][y] += 1.0
        ay = 2 if y == 0 else 1 if y == 1 else 0
        self.away_venue[row["away_team"]][ay] += 1.0


def build_history():
    r34.r12.freeze_gate()
    rows = r9.load()
    base = r9.S()
    prior = PriorResultState()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            cf = prior.features(row)
            pred.append(
                {
                    "date": day,
                    "y": r9.actual(row),
                    "raw": raw,
                    "context_features": cf,
                }
            )
            pending.append((row, raw))

        for row, raw in pending:
            base.update(row, raw)
            prior.update(row)

    return pred


def x_for(rec, feature_names):
    return list(r9.feat_k1(rec["raw"])) + [
        float(rec["context_features"][name]) for name in feature_names
    ]


def fit_model(train, feature_names):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.5, max_iter=3000, random_state=0),
    )
    model.fit([x_for(r, feature_names) for r in train], [r["y"] for r in train])
    return model


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

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.5, max_iter=3000, random_state=0),
    )
    model.fit([r9.feat_k1(r["raw"]) for r in train], [r["y"] for r in train])
    return model


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
    blocks = {
        str(i): {"count": 0, "base_hits": 0, "candidate_hits": 0, "net": 0}
        for i in range(4)
    }
    gain = loss = 0
    for b, c in zip(base_rows, candidate_rows):
        if b["date"] != c["date"] or b["y"] != c["y"]:
            raise RuntimeError("R36 paired rows misaligned")
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
        "challenger_gain": gain,
        "challenger_loss": loss,
        "net_hits": gain - loss,
        "positive_time_blocks": sum(int(z["net"] > 0) for z in blocks.values()),
        "negative_time_blocks": sum(int(z["net"] < 0) for z in blocks.values()),
        "time_blocks": blocks,
    }


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
        raise RuntimeError(f"R36 K1 validation reproduction gate failed: {base_v['hits']}")

    r35_summary = json.loads(
        (R35_DIR / "results" / "summary_r35.json").read_text(encoding="utf-8")
    )
    if r35_summary["decision"]["eligible_for_next_fresh_confirmation"]:
        raise RuntimeError("R36 expects frozen R35 failure control")

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
        candidates.append(
            {
                "name": name,
                "features": features,
                "viable": viable,
                "validation": mv,
                "gain_hits": gain,
                "gain_top1_pp": 100.0 * (mv["top1_accuracy"] - base_v["top1_accuracy"]),
                "logloss_delta": ll_delta,
                "brier_delta": mv["brier"] - base_v["brier"],
                "rps_delta": mv["rps"] - base_v["rps"],
                "paired": paired,
            }
        )

    viable = [x for x in candidates if x["viable"]]
    if viable:
        selected = max(
            viable,
            key=lambda x: (
                x["gain_hits"],
                x["paired"]["positive_time_blocks"],
                -x["paired"]["negative_time_blocks"],
                -x["logloss_delta"],
            ),
        )
        selected_name = selected["name"]
        test_base = baseline_decorate(k1, test0)
        base_t = metrics(test_base)
        if base_t["hits"] != 1877:
            raise RuntimeError(f"R36 K1 test reproduction gate failed: {base_t['hits']}")
        test = decorate(models[selected_name], test0, FEATURE_SETS[selected_name])
        mt = metrics(test)
        paired_t = paired_blocks(test_base, test)
        test_gain = mt["hits"] - base_t["hits"]
        test_ll_delta = mt["logloss"] - base_t["logloss"]
        historical_test = {
            "baseline": base_t,
            "candidate": mt,
            "gain_hits": test_gain,
            "gain_top1_pp": 100.0 * (mt["top1_accuracy"] - base_t["top1_accuracy"]),
            "logloss_delta": test_ll_delta,
            "brier_delta": mt["brier"] - base_t["brier"],
            "rps_delta": mt["rps"] - base_t["rps"],
            "paired": paired_t,
        }
        historically_confirmed = (
            test_gain >= MIN_TEST_GAIN_HITS
            and paired_t["positive_time_blocks"] >= MIN_POSITIVE_TEST_BLOCKS
            and paired_t["negative_time_blocks"] <= MAX_NEGATIVE_TEST_BLOCKS
            and test_ll_delta <= MAX_TEST_LOGLOSS_WORSEN
        )
        stop_reason = None if historically_confirmed else "FROZEN_PRIOR_RESULT_PROPENSITY_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None
        historical_test = None
        historically_confirmed = False
        stop_reason = "NO_VALIDATION_ROBUST_PRIOR_RESULT_PROPENSITY_GAIN"

    summary = {
        "schema_version": "football3-top1-r36-prior-result-propensity",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_INDEPENDENT_STRICT_PRIOR_RESULT_INFORMATION_FAMILY",
        "formal_weight": 0,
        "governance": {
            "base_r35_commit": "56d39021dd2d1b9013956f80b5741070476fc064",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "same_date_result_propensity_updates_withheld": True,
            "current_match_outcome_used": False,
            "odds_used": False,
            "market_prices_used": False,
            "lineup_used": False,
            "postmatch_stats_used": False,
            "feature_family_grid_predeclared": True,
            "fixed_shrinkage_no_search": True,
            "model_hyperparameter_search_used": False,
            "candidate_selected_on_validation_only": True,
            "test_evaluated_only_after_viable_validation_freeze": True,
            "test_used_for_candidate_selection": False,
            "batch005_labels_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "After R35 calendar/kickoff context failed, do strictly prior competition 1X2 and team venue result propensities add stable Top1 information beyond K1?",
        "prematch_information_family": {
            "family": "PRIOR_COMPETITION_AND_TEAM_VENUE_RESULT_PROPENSITY",
            "causal_contract": "Features use only outcomes from strictly earlier match dates. All fixtures on a date are predicted before that date updates any result-propensity state.",
            "fixed_shrinkage": {
                "competition_to_prior_global_strength": COMP_PRIOR_STRENGTH,
                "team_venue_to_prior_competition_strength": TEAM_VENUE_PRIOR_STRENGTH,
            },
            "candidate_feature_sets": FEATURE_SETS,
        },
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
        "controls": {
            "K1_validation": base_v,
            "R35_stop_reason": r35_summary["decision"]["stop_reason"],
        },
        "validation_candidates": candidates,
        "selected_feature_set": selected,
        "historical_test_confirmation": historical_test,
        "decision": {
            "eligible_for_next_fresh_confirmation": historically_confirmed,
            "action": "LOCK_FRESH_CONFIRMATION_FOR_FROZEN_R36" if historically_confirmed else "DO_NOT_PROMOTE_R36",
            "stop_reason": stop_reason,
        },
        "next_if_fail": "Continue an independent auditable prematch family; retain R34 away-load as development-only and do not tune against unusable Batch005.",
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r36.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r36.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert s["status"] == "COMPLETE"
    assert g["strict_prior_features"]
    assert g["same_date_results_and_xg_withheld"]
    assert g["same_date_result_propensity_updates_withheld"]
    assert not g["current_match_outcome_used"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert not g["lineup_used"] and not g["postmatch_stats_used"]
    assert g["fixed_shrinkage_no_search"] and not g["model_hyperparameter_search_used"]
    assert g["candidate_selected_on_validation_only"] and not g["test_used_for_candidate_selection"]
    assert not g["batch005_labels_used"] and not g["formal_promotion_allowed_from_this_run"]
    assert s["controls"]["K1_validation"]["hits"] == 2064
    assert len(s["validation_candidates"]) == len(FEATURE_SETS)
    if s["decision"]["eligible_for_next_fresh_confirmation"]:
        assert s["selected_feature_set"] is not None
        assert s["historical_test_confirmation"] is not None
    print("R36_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r36.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
