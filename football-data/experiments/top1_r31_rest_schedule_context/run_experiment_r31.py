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
R19_DIR = HERE.parent / "top1_r19_compact_two_stage_draw"
R30_DIR = HERE.parent / "top1_r30_ranked_draw_budget"
sys.path.insert(0, str(R19_DIR))
import run_experiment_r19 as r19  # noqa: E402

r12 = r19.r12
r9 = r19.r9

REST_NAMES = [
    "home_rest_days",
    "away_rest_days",
    "rest_days_diff",
    "rest_days_abs_gap",
    "home_short_rest_le3",
    "away_short_rest_le3",
    "both_short_rest_le3",
    "home_long_rest_ge8",
    "away_long_rest_ge8",
    "home_no_prior_match",
    "away_no_prior_match",
]
DENSITY_NAMES = [
    "home_matches_7d",
    "away_matches_7d",
    "matches_7d_diff",
    "home_matches_14d",
    "away_matches_14d",
    "matches_14d_diff",
    "home_matches_28d",
    "away_matches_28d",
    "matches_28d_diff",
]
COMBINED_NAMES = REST_NAMES + DENSITY_NAMES
FEATURE_SETS = {
    "REST_ONLY": REST_NAMES,
    "DENSITY_ONLY": DENSITY_NAMES,
    "REST_PLUS_DENSITY": COMBINED_NAMES,
}

MIN_VALIDATION_GAIN_HITS = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MAX_VALIDATION_LOGLOSS_WORSEN = 0.001
MIN_TEST_GAIN_HITS_FOR_BATCH005 = 1
MIN_POSITIVE_TEST_BLOCKS = 2
MAX_NEGATIVE_TEST_BLOCKS = 1
MAX_TEST_LOGLOSS_WORSEN = 0.001


def clipped_rest(last_date, d):
    if last_date is None:
        return 14.0, 1.0
    return float(min(30, max(1, (d - last_date).days))), 0.0


def count_recent(hist, d, days):
    return float(sum(1 for x in hist if 0 < (d - x).days <= days))


def schedule_features(row, histories):
    d = date.fromisoformat(row["date"])
    h = row["home_team"]
    a = row["away_team"]
    hh = histories[h]
    ah = histories[a]
    hr, hnew = clipped_rest(hh[-1] if hh else None, d)
    ar, anew = clipped_rest(ah[-1] if ah else None, d)
    h7, a7 = count_recent(hh, d, 7), count_recent(ah, d, 7)
    h14, a14 = count_recent(hh, d, 14), count_recent(ah, d, 14)
    h28, a28 = count_recent(hh, d, 28), count_recent(ah, d, 28)
    return {
        "home_rest_days": hr,
        "away_rest_days": ar,
        "rest_days_diff": hr - ar,
        "rest_days_abs_gap": abs(hr - ar),
        "home_short_rest_le3": float(hr <= 3 and not hnew),
        "away_short_rest_le3": float(ar <= 3 and not anew),
        "both_short_rest_le3": float(hr <= 3 and ar <= 3 and not hnew and not anew),
        "home_long_rest_ge8": float(hr >= 8 and not hnew),
        "away_long_rest_ge8": float(ar >= 8 and not anew),
        "home_no_prior_match": hnew,
        "away_no_prior_match": anew,
        "home_matches_7d": h7,
        "away_matches_7d": a7,
        "matches_7d_diff": h7 - a7,
        "home_matches_14d": h14,
        "away_matches_14d": a14,
        "matches_14d_diff": h14 - a14,
        "home_matches_28d": h28,
        "away_matches_28d": a28,
        "matches_28d_diff": h28 - a28,
    }


def build_history():
    r12.freeze_gate()
    rows = r9.load()
    base = r9.S()
    draw = r12.DrawState()
    histories = defaultdict(list)
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            df = draw.features(row, raw)
            sf = schedule_features(row, histories)
            pred.append({
                "date": day,
                "y": r9.actual(row),
                "raw": raw,
                "draw_features": df,
                "schedule_features": sf,
            })
            pending.append((row, raw))
        # Strict prior: no same-date result/xG/schedule updates are visible to any
        # other match on the same date.
        for row, raw in pending:
            base.update(row, raw)
            draw.update(row)
            d = date.fromisoformat(row["date"])
            histories[row["home_team"]].append(d)
            histories[row["away_team"]].append(d)
    return pred


def x_for(rec, feature_names):
    return list(r9.feat_k1(rec["raw"])) + [float(rec["schedule_features"][n]) for n in feature_names]


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


def baseline_decorate(k1, rows):
    p = r19.decorate_k1(k1, rows)
    return [{"date": r["date"], "y": r["y"], "P": q} for r, q in zip(rows, p)]


def metrics(rows):
    proxy = [{"y": r["y"], "P": r["P"]} for r in rows]
    return r9.metrics(proxy, "P")


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
            raise RuntimeError("R31 paired rows misaligned")
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

    # Frozen K1 control.
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    k1 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    k1.fit([r9.feat_k1(r["raw"]) for r in train], [r["y"] for r in train])
    val_base = baseline_decorate(k1, val0)
    base_v = metrics(val_base)
    if base_v["hits"] != 2064:
        raise RuntimeError(f"R31 K1 validation reproduction gate failed: {base_v['hits']}")

    r30_summary = json.loads((R30_DIR / "results" / "summary_r30.json").read_text(encoding="utf-8"))
    if r30_summary["batch005_decision"]["eligible"] or r30_summary["selected_rank_budget_rule"] is not None:
        raise RuntimeError("R31 requires frozen R30 failure control")

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
            "name": name,
            "features": features,
            "viable": viable,
            "validation": mv,
            "gain_hits": gain,
            "gain_top1_pp": 100 * (mv["top1_accuracy"] - base_v["top1_accuracy"]),
            "logloss_delta": ll_delta,
            "brier_delta": mv["brier"] - base_v["brier"],
            "rps_delta": mv["rps"] - base_v["rps"],
            "paired": paired,
        })

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
        selected_features = FEATURE_SETS[selected_name]
        test_base = baseline_decorate(k1, test0)
        base_t = metrics(test_base)
        if base_t["hits"] != 1877:
            raise RuntimeError(f"R31 K1 test reproduction gate failed: {base_t['hits']}")
        test = decorate(models[selected_name], test0, selected_features)
        mt = metrics(test)
        paired_t = paired_blocks(test_base, test)
        test_gain = mt["hits"] - base_t["hits"]
        test_ll_delta = mt["logloss"] - base_t["logloss"]
        historical_test = {
            "baseline": base_t,
            "candidate": mt,
            "gain_hits": test_gain,
            "gain_top1_pp": 100 * (mt["top1_accuracy"] - base_t["top1_accuracy"]),
            "logloss_delta": test_ll_delta,
            "brier_delta": mt["brier"] - base_t["brier"],
            "rps_delta": mt["rps"] - base_t["rps"],
            "paired": paired_t,
        }
        batch005_eligible = (
            test_gain >= MIN_TEST_GAIN_HITS_FOR_BATCH005
            and paired_t["positive_time_blocks"] >= MIN_POSITIVE_TEST_BLOCKS
            and paired_t["negative_time_blocks"] <= MAX_NEGATIVE_TEST_BLOCKS
            and test_ll_delta <= MAX_TEST_LOGLOSS_WORSEN
        )
        stop_reason = None if batch005_eligible else "FROZEN_REST_SCHEDULE_FEATURE_SET_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None
        historical_test = None
        batch005_eligible = False
        stop_reason = "NO_VALIDATION_ROBUST_REST_SCHEDULE_GAIN"

    summary = {
        "schema_version": "football3-top1-r31-rest-schedule-context",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_NEW_PREMATCH_INFORMATION_FAMILY_BEFORE_BATCH005",
        "formal_weight": 0,
        "governance": {
            "base_commit": "0f7ed3fd051909c645f703693491481d69202bfd",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "same_date_schedule_updates_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "external_injury_or_lineup_data_used": False,
            "weather_used": False,
            "coach_or_tactical_labels_used": False,
            "feature_family_grid_predeclared": True,
            "model_hyperparameter_search_used": False,
            "candidate_selected_on_validation_only": True,
            "test_evaluated_only_after_viable_validation_freeze": True,
            "test_used_for_candidate_selection": False,
            "batch004_used_for_candidate_selection": False,
            "batch005_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "After R30 exhausted draw-only ranking, do strictly pre-match rest and schedule-density features add stable 1X2 Top1 information beyond K1?",
        "prematch_information_family": {
            "family": "REST_AND_SCHEDULE_DENSITY",
            "rest_definition": "days since team's previous match, clipped to 1..30; unseen team defaults to 14 with explicit unseen indicator",
            "density_definition": "team match counts in prior 7/14/28 calendar days",
            "same_date_rule": "all matches on a date are predicted before any schedule/history update from that date",
            "candidate_feature_sets": FEATURE_SETS,
        },
        "selection_contract": {
            "min_validation_gain_hits": MIN_VALIDATION_GAIN_HITS,
            "min_positive_validation_blocks": MIN_POSITIVE_VALIDATION_BLOCKS,
            "max_negative_validation_blocks": MAX_NEGATIVE_VALIDATION_BLOCKS,
            "max_validation_logloss_worsen": MAX_VALIDATION_LOGLOSS_WORSEN,
            "min_test_gain_hits_for_batch005": MIN_TEST_GAIN_HITS_FOR_BATCH005,
            "min_positive_test_blocks": MIN_POSITIVE_TEST_BLOCKS,
            "max_negative_test_blocks": MAX_NEGATIVE_TEST_BLOCKS,
            "max_test_logloss_worsen": MAX_TEST_LOGLOSS_WORSEN,
        },
        "controls": {
            "K1_validation": base_v,
            "R30_stop_reason": r30_summary["batch005_decision"]["stop_reason"],
            "R30_next_if_fail": r30_summary["next_if_fail"],
        },
        "validation_candidates": candidates,
        "selected_feature_set": selected,
        "historical_test_confirmation": historical_test,
        "batch005_decision": {
            "eligible": batch005_eligible,
            "action": "SPEND_BATCH005_ON_FROZEN_REST_SCHEDULE_FEATURE_SET" if batch005_eligible else "DO_NOT_SPEND_BATCH005",
            "stop_reason": stop_reason,
        },
        "next_if_fail": "ADD_ANOTHER_INDEPENDENT_PREMATCH_INFORMATION_FAMILY; DO_NOT_RETURN_TO_DRAW_ONLY_GATE_ENGINEERING",
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r31.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r31.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"] and g["same_date_schedule_updates_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert not g["external_injury_or_lineup_data_used"] and not g["weather_used"] and not g["coach_or_tactical_labels_used"]
    assert g["feature_family_grid_predeclared"] and not g["model_hyperparameter_search_used"]
    assert g["candidate_selected_on_validation_only"] and not g["test_used_for_candidate_selection"]
    assert not g["batch004_used_for_candidate_selection"] and not g["batch005_used"]
    assert s["controls"]["K1_validation"]["hits"] == 2064
    assert len(s["validation_candidates"]) == len(FEATURE_SETS)
    if s["batch005_decision"]["eligible"]:
        assert s["selected_feature_set"] is not None
        assert s["historical_test_confirmation"]["gain_hits"] >= MIN_TEST_GAIN_HITS_FOR_BATCH005
    print("R31_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r31.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
