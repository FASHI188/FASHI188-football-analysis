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
R31_DIR = HERE.parent / "top1_r31_rest_schedule_context"
R33_DIR = HERE.parent / "top1_r33_prior_player_continuity"
sys.path.insert(0, str(R31_DIR))
import run_experiment_r31 as r31  # noqa: E402

r12 = r31.r12
r19 = r31.r19
r9 = r31.r9

VENUE_SEQUENCE_NAMES = [
    "home_prev_was_home", "home_prev_was_away", "away_prev_was_home", "away_prev_was_away",
    "home_prior_home_streak", "home_prior_away_streak", "away_prior_home_streak", "away_prior_away_streak",
    "home_returns_home_after_away", "away_continues_away_after_away",
]
AWAY_LOAD_NAMES = [
    "home_away_matches_7d", "away_away_matches_7d", "away_matches_7d_diff",
    "home_away_matches_14d", "away_away_matches_14d", "away_matches_14d_diff",
    "home_away_matches_28d", "away_away_matches_28d", "away_matches_28d_diff",
    "home_days_since_last_home", "away_days_since_last_home", "days_since_last_home_diff",
    "home_no_prior_home", "away_no_prior_home",
]
COMP_SWITCH_NAMES = [
    "home_last_comp_same", "away_last_comp_same", "both_last_comp_same",
    "home_comp_switches5", "away_comp_switches5", "comp_switches5_diff",
    "home_distinct_comps5", "away_distinct_comps5", "distinct_comps5_diff",
    "home_current_comp_seen5", "away_current_comp_seen5", "both_current_comp_seen5",
]
FEATURE_SETS = {
    "VENUE_SEQUENCE_ONLY": VENUE_SEQUENCE_NAMES,
    "AWAY_LOAD_ONLY": AWAY_LOAD_NAMES,
    "COMP_SWITCH_ONLY": COMP_SWITCH_NAMES,
    "VENUE_TRAVEL_CONTEXT": VENUE_SEQUENCE_NAMES + AWAY_LOAD_NAMES + COMP_SWITCH_NAMES,
}

MIN_VALIDATION_GAIN_HITS = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MAX_VALIDATION_LOGLOSS_WORSEN = 0.001
MIN_TEST_GAIN_HITS_FOR_BATCH005 = 1
MIN_POSITIVE_TEST_BLOCKS = 2
MAX_NEGATIVE_TEST_BLOCKS = 1
MAX_TEST_LOGLOSS_WORSEN = 0.001


def streak(hist, venue):
    n = 0
    for rec in reversed(hist):
        if rec["venue"] != venue:
            break
        n += 1
    return float(min(n, 10))


def count_recent_venue(hist, d, days, venue):
    return float(sum(1 for rec in hist if rec["venue"] == venue and 0 < (d - rec["date"]).days <= days))


def days_since_venue(hist, d, venue):
    for rec in reversed(hist):
        if rec["venue"] == venue:
            return float(min(30, max(1, (d - rec["date"]).days))), 0.0
    return 30.0, 1.0


def comp_switches5(hist):
    xs = [rec["comp"] for rec in hist[-5:]]
    if len(xs) < 2:
        return 0.0
    return float(sum(int(a != b) for a, b in zip(xs[:-1], xs[1:])))


def distinct_comps5(hist):
    xs = [rec["comp"] for rec in hist[-5:]]
    return float(len(set(xs))) if xs else 0.0


def current_comp_seen5(hist, comp):
    return float(any(rec["comp"] == comp for rec in hist[-5:]))


def context_features(row, histories):
    d = date.fromisoformat(row["date"])
    hhist = histories[row["home_team"]]
    ahist = histories[row["away_team"]]
    comp = row["competition_id"]

    hpv = hhist[-1]["venue"] if hhist else None
    apv = ahist[-1]["venue"] if ahist else None
    h7 = count_recent_venue(hhist, d, 7, "A")
    a7 = count_recent_venue(ahist, d, 7, "A")
    h14 = count_recent_venue(hhist, d, 14, "A")
    a14 = count_recent_venue(ahist, d, 14, "A")
    h28 = count_recent_venue(hhist, d, 28, "A")
    a28 = count_recent_venue(ahist, d, 28, "A")
    hdhome, hnohome = days_since_venue(hhist, d, "H")
    adhome, anohome = days_since_venue(ahist, d, "H")
    hlast_same = float(bool(hhist) and hhist[-1]["comp"] == comp)
    alast_same = float(bool(ahist) and ahist[-1]["comp"] == comp)
    hcs = comp_switches5(hhist)
    acs = comp_switches5(ahist)
    hdc = distinct_comps5(hhist)
    adc = distinct_comps5(ahist)
    hseen = current_comp_seen5(hhist, comp)
    aseen = current_comp_seen5(ahist, comp)

    return {
        "home_prev_was_home": float(hpv == "H"),
        "home_prev_was_away": float(hpv == "A"),
        "away_prev_was_home": float(apv == "H"),
        "away_prev_was_away": float(apv == "A"),
        "home_prior_home_streak": streak(hhist, "H"),
        "home_prior_away_streak": streak(hhist, "A"),
        "away_prior_home_streak": streak(ahist, "H"),
        "away_prior_away_streak": streak(ahist, "A"),
        "home_returns_home_after_away": float(hpv == "A"),
        "away_continues_away_after_away": float(apv == "A"),
        "home_away_matches_7d": h7,
        "away_away_matches_7d": a7,
        "away_matches_7d_diff": h7 - a7,
        "home_away_matches_14d": h14,
        "away_away_matches_14d": a14,
        "away_matches_14d_diff": h14 - a14,
        "home_away_matches_28d": h28,
        "away_away_matches_28d": a28,
        "away_matches_28d_diff": h28 - a28,
        "home_days_since_last_home": hdhome,
        "away_days_since_last_home": adhome,
        "days_since_last_home_diff": hdhome - adhome,
        "home_no_prior_home": hnohome,
        "away_no_prior_home": anohome,
        "home_last_comp_same": hlast_same,
        "away_last_comp_same": alast_same,
        "both_last_comp_same": hlast_same * alast_same,
        "home_comp_switches5": hcs,
        "away_comp_switches5": acs,
        "comp_switches5_diff": hcs - acs,
        "home_distinct_comps5": hdc,
        "away_distinct_comps5": adc,
        "distinct_comps5_diff": hdc - adc,
        "home_current_comp_seen5": hseen,
        "away_current_comp_seen5": aseen,
        "both_current_comp_seen5": hseen * aseen,
    }


def build_history():
    r12.freeze_gate()
    rows = r9.load()
    base = r9.S()
    histories = defaultdict(list)
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            cf = context_features(row, histories)
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "context_features": cf})
            pending.append((row, raw))
        # Strict prior: no result/xG/venue-sequence update from this date is visible
        # until every match on the date has already been predicted.
        for row, raw in pending:
            base.update(row, raw)
            d = date.fromisoformat(row["date"])
            comp = row["competition_id"]
            histories[row["home_team"]].append({"date": d, "venue": "H", "comp": comp})
            histories[row["away_team"]].append({"date": d, "venue": "A", "comp": comp})
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
    p = r19.decorate_k1(k1, rows)
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
            raise RuntimeError("R34 paired rows misaligned")
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
        raise RuntimeError(f"R34 K1 validation reproduction gate failed: {base_v['hits']}")

    r33_summary = json.loads((R33_DIR / "results" / "summary_r33.json").read_text(encoding="utf-8"))
    if r33_summary["batch005_decision"]["eligible"]:
        raise RuntimeError("R34 requires frozen R33 failure control")

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
            "gain_top1_pp": 100 * (mv["top1_accuracy"] - base_v["top1_accuracy"]),
            "logloss_delta": ll_delta,
            "brier_delta": mv["brier"] - base_v["brier"],
            "rps_delta": mv["rps"] - base_v["rps"],
            "paired": paired,
        })

    viable = [x for x in candidates if x["viable"]]
    if viable:
        selected = max(viable, key=lambda x: (
            x["gain_hits"], x["paired"]["positive_time_blocks"], -x["paired"]["negative_time_blocks"], -x["logloss_delta"]
        ))
        selected_name = selected["name"]
        test_base = baseline_decorate(k1, test0)
        base_t = metrics(test_base)
        if base_t["hits"] != 1877:
            raise RuntimeError(f"R34 K1 test reproduction gate failed: {base_t['hits']}")
        test = decorate(models[selected_name], test0, FEATURE_SETS[selected_name])
        mt = metrics(test)
        paired_t = paired_blocks(test_base, test)
        test_gain = mt["hits"] - base_t["hits"]
        test_ll_delta = mt["logloss"] - base_t["logloss"]
        historical_test = {
            "baseline": base_t, "candidate": mt, "gain_hits": test_gain,
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
        stop_reason = None if batch005_eligible else "FROZEN_VENUE_TRAVEL_CONTEXT_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None
        historical_test = None
        batch005_eligible = False
        stop_reason = "NO_VALIDATION_ROBUST_VENUE_TRAVEL_CONTEXT_GAIN"

    summary = {
        "schema_version": "football3-top1-r34-away-venue-sequence-context",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_NEW_PREMATCH_INFORMATION_FAMILY_BEFORE_BATCH005",
        "formal_weight": 0,
        "governance": {
            "base_commit": "60ab6a731ec6cfffdaaf1c3c0b23197f93e3c57f",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "same_date_venue_history_updates_withheld": True,
            "current_fixture_home_away_and_competition_used": True,
            "current_match_lineup_used": False,
            "postmatch_player_stats_used": False,
            "odds_used": False,
            "market_prices_used": False,
            "feature_family_grid_predeclared": True,
            "model_hyperparameter_search_used": False,
            "candidate_selected_on_validation_only": True,
            "test_evaluated_only_after_viable_validation_freeze": True,
            "test_used_for_candidate_selection": False,
            "batch004_used_for_candidate_selection": False,
            "batch005_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "After prior-player continuity failed, do strictly prior venue sequence, away-load and competition-switch states add stable 1X2 Top1 information beyond K1?",
        "prematch_information_family": {
            "family": "PRIOR_VENUE_SEQUENCE_AWAY_LOAD_AND_COMPETITION_SWITCH",
            "causal_contract": "Only prior match dates/venues/competition IDs are used; current fixture home/away designation and competition are pre-match known; same-date history updates are withheld until all fixtures on the date are predicted",
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
            "R33_stop_reason": r33_summary["batch005_decision"]["stop_reason"],
        },
        "validation_candidates": candidates,
        "selected_feature_set": selected,
        "historical_test_confirmation": historical_test,
        "batch005_decision": {
            "eligible": batch005_eligible,
            "action": "SPEND_BATCH005_ON_FROZEN_VENUE_TRAVEL_CONTEXT" if batch005_eligible else "DO_NOT_SPEND_BATCH005",
            "stop_reason": stop_reason,
        },
        "next_if_fail": "ADD_ANOTHER_INDEPENDENT STRICTLY PRIOR PREMATCH INFORMATION FAMILY; PREFER WEATHER_OR_VENUE_GEOGRAPHY ONLY WITH AUDITABLE PREMATCH TIMESTAMPS",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r34.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r34.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"]
    assert g["same_date_venue_history_updates_withheld"]
    assert not g["current_match_lineup_used"] and not g["postmatch_player_stats_used"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["candidate_selected_on_validation_only"] and not g["test_used_for_candidate_selection"]
    assert not g["batch004_used_for_candidate_selection"] and not g["batch005_used"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert s["controls"]["K1_validation"]["hits"] == 2064
    assert len(s["validation_candidates"]) == len(FEATURE_SETS)
    if s["batch005_decision"]["eligible"]:
        assert s["selected_feature_set"] is not None
        assert s["historical_test_confirmation"] is not None
    print("R34_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r34.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
