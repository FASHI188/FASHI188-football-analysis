#!/usr/bin/env python3
"""Resolution audit on the exact viewed Fast100 total-goals identities.

Runs the tail-mass audit first to reproduce the exact 100 rows and full P(T=0..7+),
then measures discrimination only. No fitting, tuning, threshold search, candidate
selection, promotion, or new sample consumption.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "validation"
if str(V) not in sys.path:
    sys.path.insert(0, str(V))

import v6_total_goals_fast100_tail_mass_audit_20260813 as tail

OUT = ROOT / "manifests" / "v6_total_goals_fast100_resolution_audit_20260813.json"


def auc(scores, labels):
    pos = [float(s) for s, y in zip(scores, labels) if y]
    neg = [float(s) for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def fixed_quintiles(scores, labels):
    ordered = sorted(zip(scores, labels), key=lambda x: (float(x[0]), bool(x[1])))
    n = len(ordered)
    out = []
    for q in range(5):
        lo = q * n // 5
        hi = (q + 1) * n // 5
        block = ordered[lo:hi]
        out.append({
            "quintile": q + 1,
            "count": len(block),
            "mean_pred": sum(float(s) for s, _ in block) / len(block),
            "observed_rate": sum(1.0 if y else 0.0 for _, y in block) / len(block),
            "min_pred": min(float(s) for s, _ in block),
            "max_pred": max(float(s) for s, _ in block),
        })
    return out


def event_resolution(rows, key, name, prob_fn, actual_fn):
    scores = [float(prob_fn(r[key])) for r in rows]
    labels = [bool(actual_fn(int(r["actual_total_bucket"]))) for r in rows]
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    overall = sum(labels) / len(labels)
    quintiles = fixed_quintiles(scores, labels)
    top = quintiles[-1]["observed_rate"]
    bottom = quintiles[0]["observed_rate"]
    return {
        "event": name,
        "count": len(rows),
        "positive_count": sum(labels),
        "observed_rate": overall,
        "auc": auc(scores, labels),
        "mean_probability_positive": sum(pos) / len(pos),
        "mean_probability_negative": sum(neg) / len(neg),
        "positive_minus_negative_probability_pp": 100.0 * ((sum(pos) / len(pos)) - (sum(neg) / len(neg))),
        "fixed_quintiles": quintiles,
        "top_quintile_observed_rate": top,
        "bottom_quintile_observed_rate": bottom,
        "top_minus_bottom_observed_pp": 100.0 * (top - bottom),
        "top_quintile_lift_vs_base": (top / overall) if overall > 0 else None,
    }


def exact_bucket_resolution(rows, key):
    out = {}
    for bucket in range(8):
        scores = [float(r[key][bucket]) for r in rows]
        labels = [int(r["actual_total_bucket"]) == bucket for r in rows]
        pos = [s for s, y in zip(scores, labels) if y]
        neg = [s for s, y in zip(scores, labels) if not y]
        out[str(bucket)] = {
            "observed_count": sum(labels),
            "auc_one_vs_rest": auc(scores, labels),
            "mean_probability_when_actual": (sum(pos) / len(pos)) if pos else None,
            "mean_probability_when_not_actual": (sum(neg) / len(neg)) if neg else None,
            "separation_pp": (100.0 * ((sum(pos) / len(pos)) - (sum(neg) / len(neg)))) if pos and neg else None,
        }
    return out


def main():
    rc = tail.main()
    if rc != 0:
        return rc
    base = json.loads(tail.OUT.read_text(encoding="utf-8"))
    rows = base["rows"]
    result = {
        "schema_version": "V6.19.3-fast100-resolution-audit-r1",
        "classification": "VIEWED_HISTORICAL_DIAGNOSTIC_ONLY",
        "status": "PASS",
        "seed": base["seed"],
        "candidate_count": base["candidate_count"],
        "sample_count": base["sample_count"],
        "no_new_sample_consumed": True,
        "no_fit": True,
        "no_tuning": True,
        "formal_weight": 0,
        "resolution": {},
        "exact_bucket_resolution": {},
        "governance": base["governance"],
    }
    for key in ("formal", "ou"):
        result["resolution"][key] = {
            "low_0_1": event_resolution(rows, key, "T<=1", lambda p: p[0] + p[1], lambda a: a <= 1),
            "tail_4plus": event_resolution(rows, key, "T>=4", lambda p: sum(p[4:]), lambda a: a >= 4),
            "tail_5plus": event_resolution(rows, key, "T>=5", lambda p: sum(p[5:]), lambda a: a >= 5),
        }
        result["exact_bucket_resolution"][key] = exact_bucket_resolution(rows, key)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compact = {
        "status": result["status"],
        "resolution": result["resolution"],
        "exact_bucket_resolution": result["exact_bucket_resolution"],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
