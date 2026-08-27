#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
EXP = HERE.parent
FD = HERE.parent.parent
PRED = EXP / "batch003_s91_robust_side_draw_head" / "results" / "batch003_s91_predictions_locked.json"

RAW = {
    "E0": FD / "raw" / "ENG_PremierLeague" / "2025-26.csv",
    "D1": FD / "raw" / "GER_Bundesliga" / "2025-26.csv",
    "I1": FD / "raw" / "ITA_SerieA" / "2025-26.csv",
    "SP1": FD / "raw" / "ESP_LaLiga" / "2025-26.csv",
    "F1": FD / "raw" / "FRA_Ligue1" / "2025-26.csv",
}
MODELS = [
    "S60",
    "S70_Robust",
    "S80_RobustCompactDraw",
    "S90_HierarchicalDrawSide",
    "S91_RobustSideDrawHead",
]
CLASS = {"HOME": 0, "DRAW": 1, "AWAY": 2}
NAMES = ["HOME", "DRAW", "AWAY"]


def parse_date(s: str) -> str:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(s)


def load_results(path: Path):
    out = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        need = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
        if not rd.fieldnames or not need.issubset(rd.fieldnames):
            raise RuntimeError(f"missing reveal columns: {path}")
        for r in rd:
            ds = (r.get("Date") or "").strip()
            h = (r.get("HomeTeam") or "").strip()
            a = (r.get("AwayTeam") or "").strip()
            gh = (r.get("FTHG") or "").strip()
            ga = (r.get("FTAG") or "").strip()
            if not ds or not h or not a or gh == "" or ga == "":
                continue
            key = (parse_date(ds), h, a)
            if key in out:
                raise RuntimeError(f"duplicate reveal key {path} {key}")
            out[key] = (int(float(gh)), int(float(ga)))
    return out


def actual(gh: int, ga: int) -> int:
    return 0 if gh > ga else 1 if gh == ga else 2


def pv(q):
    return [float(q["p_home"]), float(q["p_draw"]), float(q["p_away"])]


def metrics(rows, key):
    n = len(rows)
    hits = 0
    ll = br = rps = 0.0
    picks = [0, 0, 0]
    ph = [0, 0, 0]
    acts = [0, 0, 0]
    for r in rows:
        y = r["y"]
        q = r[key]
        p = pv(q)
        t = CLASS[q["top1"]]
        hits += int(t == y)
        picks[t] += 1
        ph[t] += int(t == y)
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
        "top1_picks": dict(zip(["home", "draw", "away"], picks)),
        "top1_hits": dict(zip(["home", "draw", "away"], ph)),
        "actuals": dict(zip(["home", "draw", "away"], acts)),
        "draw_recall": (ph[1] / acts[1] if acts[1] else None),
        "false_draw_picks": picks[1] - ph[1],
    }


def delta(a, b):
    return {
        "hits": a["hits"] - b["hits"],
        "top1_pp": 100 * (a["top1_accuracy"] - b["top1_accuracy"]),
        "logloss": a["logloss"] - b["logloss"],
        "brier": a["brier"] - b["brier"],
        "rps": a["rps"] - b["rps"],
        "draw_picks": a["top1_picks"]["draw"] - b["top1_picks"]["draw"],
        "draw_hits": a["top1_hits"]["draw"] - b["top1_hits"]["draw"],
    }


def changed(rows, base, cand):
    z = [r for r in rows if r[base]["top1"] != r[cand]["top1"]]
    out = {
        "count": len(z),
        "gains": 0,
        "losses": 0,
        "both_wrong": 0,
        "both_correct": 0,
        "rows": [],
    }
    for r in z:
        y = r["y"]
        b = CLASS[r[base]["top1"]] == y
        c = CLASS[r[cand]["top1"]] == y
        out["gains"] += int((not b) and c)
        out["losses"] += int(b and (not c))
        out["both_wrong"] += int((not b) and (not c))
        out["both_correct"] += int(b and c)
        out["rows"].append({
            "batch_index": r["batch_index"],
            "match": f"{r['home']} vs {r['away']}",
            "score": f"{r['home_goals']}-{r['away_goals']}",
            "actual": r["actual"],
            "base": r[base]["top1"],
            "candidate": r[cand]["top1"],
            "base_correct": b,
            "candidate_correct": c,
        })
    return out


def run():
    s = json.loads(PRED.read_text(encoding="utf-8"))
    if s["status"] != "BATCH003_S91_PREDICTIONS_LOCKED" or s["rows"] != 100:
        raise RuntimeError("S91 lock missing/mismatch")
    g = s["governance"]
    if g["target_results_loaded"] or not g["candidate_design_locked_before_target_scoring"] or not g["accuracy_not_computed"]:
        raise RuntimeError("S91 pre-reveal governance violated")

    src = {d: load_results(path) for d, path in RAW.items()}
    rows = []
    missing = []
    for p in s["predictions"]:
        key = (p["date"], p["home"], p["away"])
        z = src[p["division"]].get(key)
        if z is None:
            missing.append({"batch_index": p["batch_index"], "division": p["division"], "key": key})
            continue
        gh, ga = z
        y = actual(gh, ga)
        rows.append({
            **p,
            "home_goals": gh,
            "away_goals": ga,
            "y": y,
            "actual": NAMES[y],
        })
    if missing or len(rows) != 100:
        raise RuntimeError(f"reveal mapping incomplete {len(rows)}/100 missing={missing[:8]}")
    rows.sort(key=lambda x: x["batch_index"])

    mm = {k: metrics(rows, k) for k in MODELS}
    out = {
        "schema_version": "football3-batch003-reveal-score-v1",
        "status": "BATCH003_REVEALED_SCORED",
        "classification": "RETROSPECTIVE_PSEUDO_PROSPECTIVE_FRESH_100_MATCH_BATCH",
        "cohort_sha256": s["cohort_sha256"],
        "governance": {
            "S91_predictions_locked_before_reveal": True,
            "locked_prediction_commit": "e8fd3260f41c77947fc9ca370fd79273604a78e7",
            "locked_prediction_run_id": 33040195977,
            "locked_prediction_artifact_digest": "sha256:4319387b70ba8762fdc6a336ba302b40ee263be423baea629107f7457caae832",
            "outcomes_first_loaded_in_this_reveal_stage": True,
            "predictions_modified_after_reveal": False,
            "odds_used": False,
            "market_used": False,
            "Batch003_may_be_used_for_diagnosis_after_this_run": True,
            "Batch003_not_fresh_for_future_model_selection_claims": True,
        },
        "models": mm,
        "deltas_vs_S60": {k: delta(mm[k], mm["S60"]) for k in MODELS if k != "S60"},
        "deltas_vs_S70": {k: delta(mm[k], mm["S70_Robust"]) for k in MODELS if k not in {"S60", "S70_Robust"}},
        "changed_decisions": {
            "S90_vs_S70": changed(rows, "S70_Robust", "S90_HierarchicalDrawSide"),
            "S91_vs_S70": changed(rows, "S70_Robust", "S91_RobustSideDrawHead"),
            "S91_vs_S90": changed(rows, "S90_HierarchicalDrawSide", "S91_RobustSideDrawHead"),
        },
        "scored_rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_batch003_reveal.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": out["status"],
        "models": mm,
        "changed_decisions": out["changed_decisions"],
    }, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_batch003_reveal.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert s["status"] == "BATCH003_REVEALED_SCORED"
    assert len(s["scored_rows"]) == 100
    assert set(s["models"]) == set(MODELS)
    assert g["S91_predictions_locked_before_reveal"]
    assert g["outcomes_first_loaded_in_this_reveal_stage"]
    assert not g["predictions_modified_after_reveal"]
    assert not g["odds_used"] and not g["market_used"]
    assert s["cohort_sha256"] == "54703b944a8670443c7ffb0cf16c2de54ab4ada307bb3150ff63345de21459c5"
    print("BATCH003_REVEAL_VERIFY_PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: score_batch003.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()
