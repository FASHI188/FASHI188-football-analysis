#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
import math
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
PRED = HERE.parent / "batch002_stage1_s80_robust_compact_draw" / "results" / "batch002_s80_predictions_locked.json"
BASE = "https://www.football-data.co.uk/mmz4281/2526"
DIVS = ("E0", "D1", "I1", "SP1", "F1")
CLASS = {"HOME": 0, "DRAW": 1, "AWAY": 2}
NAMES = ["HOME", "DRAW", "AWAY"]
MODELS = ("S60", "S70_Robust", "S80_RobustCompactDraw")
LOCKED_COMMIT = "3f665e22fc8c1564fa0868d67475c894504b43f5"
LOCK_RUN_ID = 32986685978
LOCK_ARTIFACT_DIGEST = "sha256:140adf40bc8f0e4d3d288ccec54a17ffda89dca5235488c2bff431d54a0d330b"
EXPECTED_COHORT = "69fb0866002acb78905a999305b53318f4d9fafe1b21dcf12da91ffc11e8dc68"
EXPECTED_CHANGED = {"S70_vs_S60": 5, "S80_vs_S60": 4, "S80_vs_S70": 1}


def parse_date(s: str) -> str:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(s)


def source(div: str):
    req = urllib.request.Request(
        f"{BASE}/{div}.csv",
        headers={"User-Agent": "football3-batch002-reveal/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8-sig", errors="replace")
    out = {}
    for z in csv.DictReader(io.StringIO(text)):
        ds = (z.get("Date") or "").strip()
        h = (z.get("HomeTeam") or "").strip()
        a = (z.get("AwayTeam") or "").strip()
        gh = (z.get("FTHG") or "").strip()
        ga = (z.get("FTAG") or "").strip()
        if not ds or not h or not a or gh == "" or ga == "":
            continue
        k = (parse_date(ds), h, a)
        if k in out:
            raise RuntimeError(f"duplicate reveal key {div} {k}")
        out[k] = (int(float(gh)), int(float(ga)))
    return out


def actual(gh: int, ga: int) -> int:
    return 0 if gh > ga else 1 if gh == ga else 2


def pv(q):
    return [float(q["p_home"]), float(q["p_draw"]), float(q["p_away"])]


def metrics(rows, key: str):
    n = len(rows)
    hits = 0
    ll = br = rps = 0.0
    picks = [0, 0, 0]
    pick_hits = [0, 0, 0]
    acts = [0, 0, 0]
    for r in rows:
        y = r["y"]
        p = pv(r[key])
        t = CLASS[r[key]["top1"]]
        ok = t == y
        hits += ok
        picks[t] += 1
        pick_hits[t] += ok
        acts[y] += 1
        ll -= math.log(max(p[y], 1e-15))
        br += sum((p[i] - (i == y)) ** 2 for i in range(3))
        rps += ((p[0] - (y == 0)) ** 2 + ((p[0] + p[1]) - (y <= 1)) ** 2) / 2
    return {
        "count": n,
        "hits": hits,
        "top1_accuracy": hits / n,
        "logloss": ll / n,
        "brier": br / n,
        "rps": rps / n,
        "top1_picks": dict(zip(("home", "draw", "away"), picks)),
        "top1_hits": dict(zip(("home", "draw", "away"), pick_hits)),
        "actuals": dict(zip(("home", "draw", "away"), acts)),
    }


def delta(candidate, base):
    return {
        "hits": candidate["hits"] - base["hits"],
        "top1_pp": 100 * (candidate["top1_accuracy"] - base["top1_accuracy"]),
        "logloss": candidate["logloss"] - base["logloss"],
        "brier": candidate["brier"] - base["brier"],
        "rps": candidate["rps"] - base["rps"],
    }


def changed(rows, base_key: str, candidate_key: str):
    z = [r for r in rows if r[base_key]["top1"] != r[candidate_key]["top1"]]
    return {
        "count": len(z),
        "gains": sum((not r[f"{base_key}_correct"]) and r[f"{candidate_key}_correct"] for r in z),
        "losses": sum(r[f"{base_key}_correct"] and (not r[f"{candidate_key}_correct"]) for r in z),
        "both_wrong": sum((not r[f"{base_key}_correct"]) and (not r[f"{candidate_key}_correct"]) for r in z),
        "both_correct": sum(r[f"{base_key}_correct"] and r[f"{candidate_key}_correct"] for r in z),
        "rows": [
            {
                "batch_index": r["batch_index"],
                "match": f"{r['home']} vs {r['away']}",
                "score": f"{r['home_goals']}-{r['away_goals']}",
                "actual": r["actual"],
                "base": r[base_key]["top1"],
                "candidate": r[candidate_key]["top1"],
                "base_correct": r[f"{base_key}_correct"],
                "candidate_correct": r[f"{candidate_key}_correct"],
            }
            for r in z
        ],
    }


def conf_bucket(q):
    m = max(pv(q))
    if m < 0.40:
        return "<40"
    if m < 0.45:
        return "40-45"
    if m < 0.50:
        return "45-50"
    if m < 0.60:
        return "50-60"
    return "60+"


def run():
    s = json.loads(PRED.read_text(encoding="utf-8"))
    if s.get("status") != "BATCH002_S80_PREDICTIONS_LOCKED" or s.get("rows") != 100:
        raise RuntimeError("Batch-002 S80 lock missing/mismatch")
    if s.get("cohort_sha256") != EXPECTED_COHORT:
        raise RuntimeError("Batch-002 cohort hash mismatch")
    if s.get("top1_changed_counts") != EXPECTED_CHANGED:
        raise RuntimeError("Batch-002 pre-reveal changed-count contract mismatch")
    g = s["governance"]
    if (
        g["target_results_loaded"]
        or g["target_postmatch_stats_loaded"]
        or g["target_odds_used"]
        or g["market_used"]
        or not g["candidate_design_locked_before_target_scoring"]
        or not g["accuracy_not_computed"]
    ):
        raise RuntimeError("Batch-002 pre-reveal governance violated")

    src = {d: source(d) for d in DIVS}
    rows = []
    missing = []
    for p in s["predictions"]:
        k = (p["date"], p["home"], p["away"])
        z = src[p["division"]].get(k)
        if z is None:
            missing.append({"idx": p["batch_index"], "division": p["division"], "key": k})
            continue
        gh, ga = z
        y = actual(gh, ga)
        rec = {**p, "home_goals": gh, "away_goals": ga, "y": y, "actual": NAMES[y]}
        for key in MODELS:
            rec[f"{key}_correct"] = CLASS[p[key]["top1"]] == y
        rows.append(rec)
    if missing or len(rows) != 100:
        raise RuntimeError(f"reveal mapping incomplete {len(rows)}/100 missing={missing[:10]}")
    rows.sort(key=lambda x: x["batch_index"])

    m = {key: metrics(rows, key) for key in MODELS}
    changed_pairs = {
        "S70_vs_S60": changed(rows, "S60", "S70_Robust"),
        "S80_vs_S60": changed(rows, "S60", "S80_RobustCompactDraw"),
        "S80_vs_S70": changed(rows, "S70_Robust", "S80_RobustCompactDraw"),
    }
    for key, expected in EXPECTED_CHANGED.items():
        if changed_pairs[key]["count"] != expected:
            raise RuntimeError(f"changed decision count drift {key}")

    per_division = {}
    for d in DIVS:
        z = [r for r in rows if r["division"] == d]
        mm = {key: metrics(z, key) for key in MODELS}
        per_division[d] = {
            "count": len(z),
            **mm,
            "delta_S70_minus_S60": delta(mm["S70_Robust"], mm["S60"]),
            "delta_S80_minus_S60": delta(mm["S80_RobustCompactDraw"], mm["S60"]),
            "delta_S80_minus_S70": delta(mm["S80_RobustCompactDraw"], mm["S70_Robust"]),
        }

    confidence = {}
    for key in MODELS:
        confidence[key] = {}
        for bucket in ("<40", "40-45", "45-50", "50-60", "60+"):
            z = [r for r in rows if conf_bucket(r[key]) == bucket]
            hits = sum(CLASS[r[key]["top1"]] == r["y"] for r in z)
            confidence[key][bucket] = {
                "count": len(z),
                "hits": hits,
                "accuracy": hits / len(z) if z else None,
            }

    errors = []
    for r in rows:
        if not r["S80_RobustCompactDraw_correct"]:
            p = pv(r["S80_RobustCompactDraw"])
            errors.append(
                {
                    "batch_index": r["batch_index"],
                    "division": r["division"],
                    "match": f"{r['home']} vs {r['away']}",
                    "score": f"{r['home_goals']}-{r['away_goals']}",
                    "actual": r["actual"],
                    "S60_top1": r["S60"]["top1"],
                    "S70_top1": r["S70_Robust"]["top1"],
                    "S80_top1": r["S80_RobustCompactDraw"]["top1"],
                    "S80_max_prob": max(p),
                    "S80_actual_prob": p[r["y"]],
                }
            )
    errors.sort(key=lambda x: (-x["S80_max_prob"], x["batch_index"]))

    out = {
        "schema_version": "football3-batch002-reveal-score-v1",
        "status": "BATCH002_REVEALED_SCORED",
        "classification": "RETROSPECTIVE_PSEUDO_PROSPECTIVE_100_MATCH_BATCH",
        "cohort_sha256": s["cohort_sha256"],
        "governance": {
            "S80_predictions_locked_before_reveal": True,
            "locked_prediction_commit": LOCKED_COMMIT,
            "locked_prediction_run_id": LOCK_RUN_ID,
            "locked_prediction_artifact_digest": LOCK_ARTIFACT_DIGEST,
            "outcomes_first_loaded_in_this_reveal_stage": True,
            "predictions_modified_after_reveal": False,
            "odds_used": False,
            "market_used": False,
            "Batch002_may_be_used_for_diagnosis_after_this_run": True,
            "Batch002_not_fresh_for_future_model_selection_claims": True,
        },
        "S60": m["S60"],
        "S70_Robust": m["S70_Robust"],
        "S80_RobustCompactDraw": m["S80_RobustCompactDraw"],
        "delta_S70_minus_S60": delta(m["S70_Robust"], m["S60"]),
        "delta_S80_minus_S60": delta(m["S80_RobustCompactDraw"], m["S60"]),
        "delta_S80_minus_S70": delta(m["S80_RobustCompactDraw"], m["S70_Robust"]),
        "changed_decisions": changed_pairs,
        "per_division": per_division,
        "confidence_buckets": confidence,
        "S80_errors_by_confidence": errors,
        "scored_rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_batch002_reveal.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": out["status"],
                "S60": out["S60"],
                "S70_Robust": out["S70_Robust"],
                "S80_RobustCompactDraw": out["S80_RobustCompactDraw"],
                "delta_S80_minus_S60": out["delta_S80_minus_S60"],
                "delta_S80_minus_S70": out["delta_S80_minus_S70"],
                "changed_decisions": out["changed_decisions"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def verify():
    s = json.loads((OUT / "summary_batch002_reveal.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert s["status"] == "BATCH002_REVEALED_SCORED"
    assert s["cohort_sha256"] == EXPECTED_COHORT
    assert len(s["scored_rows"]) == 100
    assert g["S80_predictions_locked_before_reveal"]
    assert g["locked_prediction_commit"] == LOCKED_COMMIT
    assert g["outcomes_first_loaded_in_this_reveal_stage"]
    assert not g["predictions_modified_after_reveal"]
    assert not g["odds_used"] and not g["market_used"]
    assert s["changed_decisions"]["S70_vs_S60"]["count"] == 5
    assert s["changed_decisions"]["S80_vs_S60"]["count"] == 4
    assert s["changed_decisions"]["S80_vs_S70"]["count"] == 1
    print("BATCH002_REVEAL_VERIFY_PASS")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: score_batch002.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()
