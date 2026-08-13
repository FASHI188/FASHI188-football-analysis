#!/usr/bin/env python3
"""Viewed-sample Fast100 tail-mass audit for total goals.

Research diagnostic only. Reuses the exact V6.19.3 fixed-seed Fast100 identity
construction and viewed labels. It does not fit, tune, select, promote, or alter
any formal model. The purpose is to distinguish two different failure modes:

1) aggregate tail-mass miscalibration (e.g. P(T>=4) too low), versus
2) tail-mass fragmentation across exact buckets 4,5,6,7+ so no individual tail
   bucket becomes the categorical mode even when aggregate tail probability is sane.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "validation"
E = ROOT / "engine"
for p in (V, E):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_goals_fast100_v6193 as fast
import validate_architecture_order_v6190 as arch
import validate_joint_market_ipf_crossseason_v6164 as base
import validate_market_ou_kl_projection_v6162 as ou
from football_v460_engine import load_config, predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals, read_processed_matches

OUT = ROOT / "manifests" / "v6_total_goals_fast100_tail_mass_audit_20260813.json"
BINS = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.40), (0.40, 1.0000001)]


def event_brier(prob: float, actual: bool) -> float:
    return (float(prob) - (1.0 if actual else 0.0)) ** 2


def event_metrics(rows, key, event_name, pred_fn, actual_fn):
    probs = [float(pred_fn(r[key])) for r in rows]
    actuals = [bool(actual_fn(r["actual_total_bucket"])) for r in rows]
    observed = sum(actuals) / len(actuals)
    mean_pred = sum(probs) / len(probs)
    bins = []
    for lo, hi in BINS:
        idx = [i for i, p in enumerate(probs) if lo <= p < hi]
        if not idx:
            continue
        bins.append({
            "range": [lo, hi],
            "count": len(idx),
            "mean_pred": sum(probs[i] for i in idx) / len(idx),
            "observed_rate": sum(1.0 if actuals[i] else 0.0 for i in idx) / len(idx),
        })
    return {
        "event": event_name,
        "observed_rate": observed,
        "mean_predicted_probability": mean_pred,
        "calibration_bias_pred_minus_observed": mean_pred - observed,
        "brier": sum(event_brier(p, a) for p, a in zip(probs, actuals)) / len(rows),
        "calibration_bins": bins,
    }


def bucket_frequency(rows):
    n = len(rows)
    actual = Counter(int(r["actual_total_bucket"]) for r in rows)
    out = {}
    for key in ("formal", "ou"):
        mean_prob = [sum(float(r[key][i]) for r in rows) / n for i in range(8)]
        out[key] = {
            str(i): {
                "observed_frequency": actual.get(i, 0) / n,
                "mean_predicted_probability": mean_prob[i],
                "bias_pred_minus_observed": mean_prob[i] - actual.get(i, 0) / n,
            }
            for i in range(8)
        }
    return out


def rank_desc(probs, bucket):
    ranked = sorted(range(8), key=lambda i: (-float(probs[i]), i))
    return ranked.index(int(bucket)) + 1


def tail_fragmentation(rows, key):
    tail_rows = [r for r in rows if int(r["actual_total_bucket"]) >= 4]
    all_tail_mass = [sum(float(x) for x in r[key][4:]) for r in rows]
    tail_mass_when_tail_actual = [sum(float(x) for x in r[key][4:]) for r in tail_rows]
    max_tail_probs = [max(float(x) for x in r[key][4:]) for r in rows]
    mode_probs = [max(float(x) for x in r[key]) for r in rows]
    max_tail_ranks = []
    actual_bucket_ranks = []
    tail_bucket_top2 = 0
    tail_bucket_top3 = 0
    for r in tail_rows:
        p = [float(x) for x in r[key]]
        tail_bucket = max(range(4, 8), key=lambda i: (p[i], -i))
        rr = rank_desc(p, tail_bucket)
        max_tail_ranks.append(rr)
        actual_bucket_ranks.append(rank_desc(p, int(r["actual_total_bucket"])))
        if rr <= 2:
            tail_bucket_top2 += 1
        if rr <= 3:
            tail_bucket_top3 += 1
    return {
        "actual_tail4plus_count": len(tail_rows),
        "mean_tail4plus_mass_all_rows": sum(all_tail_mass) / len(all_tail_mass),
        "mean_tail4plus_mass_when_tail_actual": sum(tail_mass_when_tail_actual) / len(tail_mass_when_tail_actual),
        "mean_max_individual_tail_bucket_probability_all_rows": sum(max_tail_probs) / len(max_tail_probs),
        "mean_categorical_mode_probability_all_rows": sum(mode_probs) / len(mode_probs),
        "mean_gap_mode_minus_best_tail_bucket": sum(m - t for m, t in zip(mode_probs, max_tail_probs)) / len(rows),
        "actual_tail_rows_best_tail_bucket_top2_rate": tail_bucket_top2 / len(tail_rows),
        "actual_tail_rows_best_tail_bucket_top3_rate": tail_bucket_top3 / len(tail_rows),
        "actual_tail_rows_mean_best_tail_bucket_rank": sum(max_tail_ranks) / len(max_tail_ranks),
        "actual_tail_rows_mean_actual_bucket_rank": sum(actual_bucket_ranks) / len(actual_bucket_ranks),
    }


def classify_tail4(metric):
    bias = float(metric["calibration_bias_pred_minus_observed"])
    if bias < -0.03:
        return "TAIL4PLUS_AGGREGATE_MASS_UNDERESTIMATED_ON_VIEWED_FAST100"
    if bias > 0.03:
        return "TAIL4PLUS_AGGREGATE_MASS_OVERESTIMATED_ON_VIEWED_FAST100"
    return "TAIL4PLUS_AGGREGATE_MASS_WITHIN_3PP_OF_OBSERVED_ON_VIEWED_FAST100"


def main():
    cfg = load_config()
    warmc = int(cfg["validation"]["warmup_competition_matches"])
    warmt = int(cfg["validation"]["warmup_team_matches"])
    contexts = {}
    candidates = []

    for season in fast.SEASONS:
        for cid in fast.COMPS:
            params = ou.params_by_season(cid).get(season)
            if not params:
                continue
            lookup = base.market_lookup(cid, season)
            matches = [m for m in read_processed_matches(cid) if str(m.season) == season]
            matches.sort(key=lambda m: (m.date, m.home_team, m.away_team))
            contexts[(cid, season)] = {
                "matches": matches,
                "lookup": lookup,
                "params": params,
                "temp": ou.calibrator(cid, season),
            }
            bydate = defaultdict(list)
            for m in matches:
                bydate[m.date].append(m)
            histn = 0
            hc = Counter()
            ac = Counter()
            for dt in sorted(bydate):
                day = sorted(bydate[dt], key=lambda x: (x.home_team, x.away_team))
                for m in day:
                    identity = (m.date.isoformat(), m.home_team, m.away_team)
                    if histn >= warmc and hc[m.home_team] >= warmt and ac[m.away_team] >= warmt and identity in lookup:
                        candidates.append((cid, season, *identity))
                for m in day:
                    histn += 1
                    hc[m.home_team] += 1
                    ac[m.away_team] += 1

    if len(candidates) != 9186:
        raise RuntimeError(f"candidate identity drift: expected 9186, got {len(candidates)}")
    selected = random.Random(fast.SEED).sample(candidates, fast.N)
    rows = []
    failures = []

    for cid, season, date_iso, home, away in selected:
        ctx = contexts[(cid, season)]
        target = next(m for m in ctx["matches"] if m.date.isoformat() == date_iso and m.home_team == home and m.away_team == away)
        hist = [m for m in ctx["matches"] if m.date < target.date]
        try:
            pred = predict_from_history(hist, cid, season, home, away, target.date, selected_parameters=ctx["params"], use_team_effects=True)
        except Exception as exc:
            failures.append({"identity": [cid, season, date_iso, home, away], "stage": "formal", "error": str(exc)})
            continue
        prior = temperature_scale_matrix(pred["probabilities"]["score_matrix"], ctx["temp"])
        marg = derive_score_marginals(prior)
        mk = ctx["lookup"][(date_iso, home, away)]
        tdict = ou.project(marg["total_goals"], float(mk["p_over25"]))
        if tdict is None:
            failures.append({"identity": [cid, season, date_iso, home, away], "stage": "ou_project"})
            continue
        formal = [float(x) for x in arch.total_vec(prior)]
        ou_vec = [float(tdict[k]) for k in ou.TOTAL_KEYS]
        if abs(sum(formal) - 1.0) > 1e-10 or abs(sum(ou_vec) - 1.0) > 1e-10:
            raise RuntimeError("probability conservation failure")
        rows.append({
            "competition_id": cid,
            "season": season,
            "date": date_iso,
            "home": home,
            "away": away,
            "actual_total_bucket": min(7, int(target.home_goals + target.away_goals)),
            "formal": formal,
            "ou": ou_vec,
        })

    if failures or len(rows) != 100:
        raise RuntimeError(f"expected exact 100 rows; rows={len(rows)} failures={failures}")

    events = {}
    for key in ("formal", "ou"):
        events[key] = {
            "zero": event_metrics(rows, key, "T=0", lambda p: p[0], lambda a: a == 0),
            "zero_or_one": event_metrics(rows, key, "T<=1", lambda p: p[0] + p[1], lambda a: a <= 1),
            "four_plus": event_metrics(rows, key, "T>=4", lambda p: sum(p[4:]), lambda a: a >= 4),
            "five_plus": event_metrics(rows, key, "T>=5", lambda p: sum(p[5:]), lambda a: a >= 5),
            "six_plus": event_metrics(rows, key, "T>=6", lambda p: sum(p[6:]), lambda a: a >= 6),
            "seven_plus": event_metrics(rows, key, "T>=7", lambda p: p[7], lambda a: a >= 7),
        }

    payload = {
        "schema_version": "V6.19.3-fast100-tail-mass-audit-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "classification": "VIEWED_HISTORICAL_DIAGNOSTIC_ONLY",
        "status": "PASS",
        "seed": fast.SEED,
        "candidate_count": len(candidates),
        "sample_count": len(rows),
        "identity_reuses_exact_fast100": True,
        "no_new_sample_consumed": True,
        "no_fit": True,
        "no_tuning": True,
        "formal_weight": 0,
        "bucket_frequency_calibration": bucket_frequency(rows),
        "events": events,
        "fragmentation": {key: tail_fragmentation(rows, key) for key in ("formal", "ou")},
        "diagnostic_ruling": {
            "formal_tail4plus": classify_tail4(events["formal"]["four_plus"]),
            "ou_tail4plus": classify_tail4(events["ou"]["four_plus"]),
            "interpretation_rule": "No 4+ categorical mode alone is insufficient evidence of tail underprediction; inspect aggregate P(T>=4) calibration and fragmentation together.",
        },
        "rows": rows,
        "governance": {
            "research_only": True,
            "viewed_labels_only": True,
            "current_rule_change": False,
            "formal_model_change": False,
            "formal_data_change": False,
            "r45b_sample_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compact = {
        "status": payload["status"],
        "diagnostic_ruling": payload["diagnostic_ruling"],
        "events": {
            key: {
                event: {
                    "observed_rate": val["observed_rate"],
                    "mean_predicted_probability": val["mean_predicted_probability"],
                    "calibration_bias_pred_minus_observed": val["calibration_bias_pred_minus_observed"],
                    "brier": val["brier"],
                }
                for event, val in events[key].items()
            }
            for key in ("formal", "ou")
        },
        "fragmentation": payload["fragmentation"],
        "bucket_frequency_calibration": payload["bucket_frequency_calibration"],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
